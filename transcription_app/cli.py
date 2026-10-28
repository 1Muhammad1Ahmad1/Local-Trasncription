"""Command-line entry point: arguments, logging, dependency checks, exit codes."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from . import __version__
from .config import ConfigError, load_config
from .paths import Paths
from .pipeline import (
    EXIT_CONFIG, EXIT_DEPENDENCY, EXIT_INTERRUPTED, EXIT_MODEL, BatchRunner, FatalError,
)

LOG_FILES_KEPT = 30

EPILOG = """\
examples:
  python main.py                  process all link files in input/
  python main.py --model medium   use a larger, slower, usually more accurate model
  python main.py --language ur    the videos are in Urdu
  python main.py --language en    the videos are in English
  python main.py --force          redo every file, even ones already done

exit codes:
  0    all files processed or skipped (also when input/ is empty)
  1    one or more files failed, were invalid, or contained no speech
  2    invalid configuration or command-line value
  3    a required dependency (FFmpeg, ffprobe, JavaScript runtime, package) is missing
  4    the speech model could not be downloaded or loaded
  130  cancelled with Ctrl+C
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Transcribe the video link in each input/*.txt file into output/ "
                    "using the same filename. Everything runs locally.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", help="Whisper model, e.g. tiny, base, small, medium, large-v3, turbo (default from config.json)")
    parser.add_argument("--language", help="'auto', 'en' (English), 'ur' (Urdu) or another language code (default from config.json)")
    parser.add_argument("--force", action="store_true", help="reprocess every link file, even if already completed")
    parser.add_argument("--verbose", action="store_true", help="write extra yt-dlp details to the log file")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def setup_logging(paths: Paths, verbose: bool) -> str:
    logs = sorted(paths.logs.glob("run-*.log"))
    for old in logs[: max(0, len(logs) - (LOG_FILES_KEPT - 1))]:
        try:
            old.unlink()
        except OSError:
            pass
    log_path = paths.logs / f"run-{datetime.now():%Y%m%d-%H%M%S}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("transcription")
    logger.handlers[:] = [handler]
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    return str(log_path)


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None, paths: Paths | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)
    paths = paths or Paths()
    paths.ensure()

    try:
        config = load_config(paths.config_file, {"model": args.model, "language": args.language})
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return EXIT_CONFIG

    log_path = setup_logging(paths, args.verbose)
    log = logging.getLogger("transcription")
    log.info("Start: model=%s language=%s device=%s compute=%s force=%s",
             config.model, config.language, config.device, config.compute_type, args.force)
    print(f"Model: {config.model} | Language: {config.language} | Device: {config.device}")

    def prepare():
        from .deps import check_runtime
        from .downloader import AudioDownloader
        from .transcriber import ModelLoadError, WhisperTranscriber, load_whisper_model

        tools, problems = check_runtime(paths)
        if problems:
            raise FatalError("Missing dependencies:\n  - " + "\n  - ".join(problems), EXIT_DEPENDENCY)
        log.info("Tools: ffmpeg=%s ffprobe=%s js=%s %s", tools.ffmpeg.path, tools.ffprobe.path,
                 tools.js.name, tools.js.version)
        try:
            model = load_whisper_model(config, paths.models)
        except ModelLoadError as exc:
            raise FatalError(str(exc), EXIT_MODEL) from None
        downloader = AudioDownloader(
            tools.ffmpeg.path, tools.ffprobe.path, (tools.js.name, tools.js.path),
            retries=config.download_retries, socket_timeout=config.socket_timeout_seconds,
        )
        return downloader, WhisperTranscriber(model, config)

    runner = BatchRunner(paths, config, prepare, force=args.force)
    try:
        code = runner.run()
    except FatalError as exc:
        print("")
        print(f"Stopped before processing: {exc}")
        print("No link files were changed. Fix the problem above and run again.")
        log.error("Fatal: %s", exc)
        code = exc.exit_code
    except KeyboardInterrupt:
        print("\nStopped by Ctrl+C.")
        code = EXIT_INTERRUPTED
    print(f"Log file: {log_path}")
    return code
