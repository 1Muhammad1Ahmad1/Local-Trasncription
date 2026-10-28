"""Batch processing: one link file at a time, download -> transcribe -> save."""

from __future__ import annotations

import logging
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .config import Config
from .downloader import DownloadedAudio, DownloadFailed
from .inputs import read_link_file, scan_input_folder
from .output import OutputReplaceError, TranscriptWriter, cleanup_stale_partials
from .paths import Paths
from .state import StateStore, decide
from .transcriber import TranscriptionFailed, TranscriptionInfo

log = logging.getLogger("transcription")

EXIT_OK = 0
EXIT_JOBS_FAILED = 1
EXIT_CONFIG = 2
EXIT_DEPENDENCY = 3
EXIT_MODEL = 4
EXIT_INTERRUPTED = 130


class Downloader(Protocol):
    def download(self, url: str, dest: Path, on_progress=None) -> DownloadedAudio: ...


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, on_segment, on_progress=None) -> TranscriptionInfo: ...


class FatalError(Exception):
    """A shared problem (missing dependency, model failure) that stops the batch."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class Summary:
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)


def fmt_time(seconds: float | None) -> str:
    seconds = int(max(0, seconds or 0))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class StatusLine:
    """Single updating terminal line when interactive, occasional lines otherwise."""

    def __init__(self, stream=None, min_interval: float = 0.5):
        self.stream = stream or sys.stdout
        self.interactive = getattr(self.stream, "isatty", lambda: False)()
        self.min_interval = min_interval if self.interactive else 30.0
        self._last = 0.0
        self._width = 0
        self._active = False

    def update(self, text: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < self.min_interval:
            return
        self._last = now
        if self.interactive:
            pad = max(0, self._width - len(text))
            self.stream.write("\r" + text + " " * pad)
            self._width = len(text)
            self._active = True
        else:
            self.stream.write(text + "\n")
        self.stream.flush()

    def finish(self) -> None:
        if self.interactive and self._active:
            self.stream.write("\n")
            self.stream.flush()
        self._active = False
        self._width = 0
        self._last = 0.0


class BatchRunner:
    def __init__(
        self,
        paths: Paths,
        config: Config,
        prepare: Callable[[], tuple[Downloader, Transcriber]],
        force: bool = False,
        out: Callable[[str], None] = print,
        status: StatusLine | None = None,
    ):
        self.paths = paths
        self.config = config
        self.prepare = prepare
        self.force = force
        self.out = out
        self.status = status or StatusLine()
        self.summary = Summary()
        self.state = StateStore(paths.state_file)
        self.settings = config.transcription_settings()

    def run(self) -> int:
        started = time.monotonic()
        files = scan_input_folder(self.paths.input)
        if not files:
            self.out("No link files found.")
            self.out(f"Put .txt files into: {self.paths.input}")
            self.out("Each file should contain one video link, for example:")
            self.out("    https://www.youtube.com/watch?v=VIDEO_ID")
            self.out("The transcript will be saved in the output folder with the same filename.")
            return EXIT_OK

        removed = cleanup_stale_partials(self.paths.output)
        if removed:
            log.info("Removed %d leftover temporary transcript file(s)", removed)

        links = [read_link_file(p) for p in files]
        plan = []  # (link, process?, reason)
        for link in links:
            if link.error:
                plan.append((link, False, link.error))
                continue
            process, reason = decide(
                self.state.get(link.name), link.url, self.settings,
                self.paths.output / link.name, self.force,
            )
            plan.append((link, process, reason))

        total = len(plan)
        to_process = sum(1 for link, process, _ in plan if link.url and process)
        self.out(f"Found {total} link file(s); {to_process} to process.")
        log.info("Batch: %d files, %d to process, settings=%s", total, to_process, self.settings)

        downloader = transcriber = None
        current = None
        try:
            if to_process:
                # Shared setup happens once, before any download: dependency checks and model load.
                downloader, transcriber = self.prepare()

            for index, (link, process, reason) in enumerate(plan, start=1):
                self.out("")
                self.out(f"[{index}/{total}] {link.name}")
                if link.error:
                    self.out(f"  Invalid link file: {link.error}")
                    self.out("  No transcript created for this file.")
                    log.warning("Invalid input %r: %s", link.name, link.error)
                    self.summary.invalid.append(link.name)
                    continue
                if not process:
                    self.out(f"  Skipped: {reason}.")
                    log.info("Skip %r: %s", link.name, reason)
                    self.summary.skipped.append(link.name)
                    continue
                self.out(f"  Processing: {reason}.")
                current = link
                self._process(link, downloader, transcriber)
                current = None
        except KeyboardInterrupt:
            self.status.finish()
            if current is not None:
                self._record(current, "interrupted", error="cancelled by user")
                self.out(f"  Cancelled while processing {current.name}; it will restart from the beginning next time.")
            self.out("")
            self.out("Stopped by Ctrl+C. Finished transcripts are kept.")
            self._print_summary(started)
            log.warning("Interrupted by user")
            return EXIT_INTERRUPTED

        self._print_summary(started)
        if self.summary.failed or self.summary.invalid or self.summary.empty:
            return EXIT_JOBS_FAILED
        return EXIT_OK

    def _process(self, link, downloader: Downloader, transcriber: Transcriber) -> None:
        final_path = self.paths.output / link.name
        had_previous = final_path.is_file()
        log.info("Job %r url=%s", link.name, link.url)
        self._record(link, "in_progress")
        job_start = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="transcription-", ignore_cleanup_errors=True) as tmp:
                self.out("  Downloading audio...")
                audio = downloader.download(link.url, Path(tmp), self._download_progress)
                self.status.finish()
                if audio.duration:
                    self.out(f"  Audio downloaded ({fmt_time(audio.duration)} long).")

                with TranscriptWriter(final_path, self.config.paragraph_pause_seconds) as writer:
                    self.status.update(f"  Transcribing: {fmt_time(0)} / {fmt_time(audio.duration)}", force=True)
                    info = transcriber.transcribe(audio.path, writer.add_segment, self._transcribe_progress)
                    self.status.finish()
                    if not writer.has_text:
                        self.out("  No speech found: the recording produced an empty transcript.")
                        if had_previous:
                            self.out(f"  The earlier transcript output/{link.name} was left unchanged.")
                        self._record(link, "empty", language=info.language, duration=info.duration)
                        self.summary.empty.append(link.name)
                        return
                    digest = writer.commit()
        except DownloadFailed as exc:
            self._fail(link, f"Download failed: {exc}", had_previous)
            return
        except TranscriptionFailed as exc:
            self._fail(link, f"Transcription failed: {exc}", had_previous)
            return
        except OutputReplaceError as exc:
            self._fail(link, f"Saving failed: {exc}", had_previous)
            return
        except (KeyboardInterrupt, FatalError):
            raise
        except Exception as exc:
            log.exception("Unexpected error for %r", link.name)
            self._fail(link, f"Unexpected error: {exc.__class__.__name__}: {exc}", had_previous)
            return

        self._record(
            link, "completed",
            output_sha256=digest,
            output_bytes=final_path.stat().st_size,
            language=info.language,
            duration=info.duration,
        )
        lang = f", language: {info.language}" if info.language else ""
        self.out(f"  Saved: output/{link.name}{lang}")
        log.info("Completed %r in %.0fs (%d segments)", link.name, time.monotonic() - job_start, info.segments)
        self.summary.completed.append(link.name)

    def _fail(self, link, message: str, had_previous: bool) -> None:
        self.status.finish()
        self.out(f"  {message}")
        if had_previous:
            self.out(f"  The latest attempt failed; the earlier transcript output/{link.name} was kept unchanged.")
        log.error("Failed %r: %s", link.name, message)
        self._record(link, "failed", error=message)
        self.summary.failed.append(link.name)

    def _record(self, link, status: str, **extra) -> None:
        record = {"url": link.url, "settings": self.settings, "status": status,
                  "output_file": f"output/{link.name}"}
        record.update({k: v for k, v in extra.items() if v is not None})
        self.state.put(link.name, record)

    def _download_progress(self, done: int, total: int | None) -> None:
        if total:
            self.status.update(f"  Downloading audio: {done / 1e6:.1f} / {total / 1e6:.1f} MB")
        else:
            self.status.update(f"  Downloading audio: {done / 1e6:.1f} MB")

    def _transcribe_progress(self, position: float, duration: float | None) -> None:
        self.status.update(f"  Transcribing: {fmt_time(position)} / {fmt_time(duration)}")

    def _print_summary(self, started: float) -> None:
        s = self.summary
        elapsed = time.monotonic() - started
        self.out("")
        self.out("=" * 50)
        self.out(f"Completed:          {len(s.completed)}")
        self.out(f"Skipped:            {len(s.skipped)}")
        failed_note = f" (including {len(s.invalid)} invalid link file(s))" if s.invalid else ""
        self.out(f"Failed:             {len(s.failed) + len(s.invalid)}{failed_note}")
        self.out(f"Empty / no speech:  {len(s.empty)}")
        self.out(f"Elapsed time:       {fmt_time(elapsed)}")
        self.out(f"Transcripts folder: {self.paths.output}")
        self.out("=" * 50)
        log.info("Summary: completed=%d skipped=%d failed=%d invalid=%d empty=%d elapsed=%.0fs",
                 len(s.completed), len(s.skipped), len(s.failed), len(s.invalid), len(s.empty), elapsed)
