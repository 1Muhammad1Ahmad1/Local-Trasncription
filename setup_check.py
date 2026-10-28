"""Setup step run by setup.bat inside the virtual environment.

1. Verifies the Python packages import.
2. Finds working FFmpeg + ffprobe; if missing, installs a project-local copy from
   the yt-dlp FFmpeg-Builds releases (the build yt-dlp's README recommends) after
   verifying its published SHA-256 checksum.
3. Verifies a JavaScript runtime for YouTube (Deno, installed by pip into .venv).
4. Downloads and loads the configured Whisper model, and runs a tiny test inference.

Prints "Setup complete" only when every check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transcription_app.config import ConfigError, load_config  # noqa: E402
from transcription_app.deps import (  # noqa: E402
    find_ffmpeg_tool, find_js_runtime, local_ffmpeg_dir, missing_modules,
)
from transcription_app.paths import Paths  # noqa: E402

FFMPEG_RELEASE = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
FFMPEG_ASSET = "ffmpeg-master-latest-win64-gpl.zip"

FFMPEG_MANUAL = """\
Manual FFmpeg installation:
  1. Download {asset} from
     https://github.com/yt-dlp/FFmpeg-Builds/releases/latest
     (or a Windows build linked from https://ffmpeg.org/download.html).
  2. Open the zip and copy ffmpeg.exe and ffprobe.exe from its 'bin' folder into:
     {target}
  3. Run setup.bat again."""


def heading(text: str) -> None:
    print("")
    print(f"--- {text}")


def _fetch(url: str, dest: Path | None = None, label: str = "") -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "transcription-setup"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if dest is None:
            return response.read()
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as fh:
            while chunk := response.read(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r    {label}: {done / 1e6:.0f} / {total / 1e6:.0f} MB", end="", flush=True)
        print("")
    return None


def install_local_ffmpeg(paths: Paths) -> str | None:
    """Download, verify and unpack ffmpeg.exe/ffprobe.exe into tools/ffmpeg/bin.
    Returns None on success, otherwise an error message."""
    target = local_ffmpeg_dir(paths)
    if os.name != "nt":
        return "automatic FFmpeg install is only available on Windows; install ffmpeg with your package manager."
    print(f"    Installing a project-local FFmpeg into {target}")
    print(f"    Source: {FFMPEG_RELEASE}{FFMPEG_ASSET}")
    try:
        checksums = _fetch(FFMPEG_RELEASE + "checksums.sha256").decode("utf-8", "replace")
    except Exception as exc:
        return f"could not download the FFmpeg checksum list ({exc})."
    expected = None
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == FFMPEG_ASSET:
            expected = parts[0].lower()
    if not expected:
        return "the FFmpeg release does not list a checksum for the Windows build; refusing to install unverified files."

    with tempfile.TemporaryDirectory(prefix="ffmpeg-setup-") as tmp:
        archive = Path(tmp) / FFMPEG_ASSET
        try:
            _fetch(FFMPEG_RELEASE + FFMPEG_ASSET, archive, "Downloading FFmpeg")
        except Exception as exc:
            return f"FFmpeg download failed ({exc})."
        digest = hashlib.sha256()
        with open(archive, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            return "the downloaded FFmpeg archive failed SHA-256 verification; it was deleted."
        print("    SHA-256 checksum verified.")
        target.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as zf:
                for name in ("ffmpeg.exe", "ffprobe.exe"):
                    member = next((m for m in zf.namelist() if m.endswith("/bin/" + name)), None)
                    if member is None:
                        return f"{name} was not found inside the FFmpeg archive."
                    partial = target / (name + ".partial")
                    with zf.open(member) as src, open(partial, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.replace(partial, target / name)
        except (OSError, zipfile.BadZipFile) as exc:
            return f"could not unpack FFmpeg ({exc})."
    return None


def check_model(paths: Paths, config) -> str | None:
    import numpy as np

    from transcription_app.transcriber import ModelLoadError, load_whisper_model

    try:
        model = load_whisper_model(config, paths.models, announce=lambda m: print("    " + m.replace("\n", "\n    ")))
    except ModelLoadError as exc:
        return str(exc)
    try:
        silence = np.zeros(16000, dtype=np.float32)
        segments, _ = model.transcribe(silence, language=config.whisper_language or "en", vad_filter=False)
        list(segments)
    except Exception as exc:
        return f"the model loaded but a test transcription failed ({exc})."
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Install/verify dependencies for the transcription app.")
    parser.add_argument("--reinstall-ffmpeg", action="store_true",
                        help="download the project-local FFmpeg even if one is already available")
    parser.add_argument("--skip-model", action="store_true", help="do not download/check the speech model")
    args = parser.parse_args()

    paths = Paths()
    paths.ensure()
    results: list[tuple[str, bool, str]] = []

    heading("Configuration")
    try:
        config = load_config(paths.config_file)
        print(f"    config.json OK (model: {config.model}, language: {config.language}, device: {config.device})")
        results.append(("config.json", True, ""))
    except ConfigError as exc:
        print(f"    {exc}")
        results.append(("config.json", False, str(exc)))
        config = None

    heading("Python packages")
    missing = missing_modules()
    if missing:
        for item in missing:
            print(f"    MISSING: {item}")
        results.append(("Python packages", False, "reinstall with setup.bat"))
    else:
        import faster_whisper
        import yt_dlp
        print(f"    yt-dlp {yt_dlp.version.__version__}, faster-whisper {faster_whisper.__version__}: OK")
        results.append(("Python packages", True, ""))

    heading("FFmpeg and ffprobe")
    ffmpeg, ffprobe = find_ffmpeg_tool(paths, "ffmpeg"), find_ffmpeg_tool(paths, "ffprobe")
    if args.reinstall_ffmpeg or not (ffmpeg.ok and ffprobe.ok):
        if not args.reinstall_ffmpeg:
            print(f"    Not usable yet (ffmpeg: {ffmpeg.problem or 'ok'}, ffprobe: {ffprobe.problem or 'ok'}).")
            print("    This needs internet once (about 100-200 MB).")
        error = install_local_ffmpeg(paths)
        if error:
            print(f"    FAILED: {error}")
            print(FFMPEG_MANUAL.format(asset=FFMPEG_ASSET, target=local_ffmpeg_dir(paths)))
        ffmpeg, ffprobe = find_ffmpeg_tool(paths, "ffmpeg"), find_ffmpeg_tool(paths, "ffprobe")
    for tool in (ffmpeg, ffprobe):
        if tool.ok:
            print(f"    {tool.name}: {tool.path}")
            print(f"        {tool.version}")
        else:
            print(f"    {tool.name}: NOT WORKING ({tool.problem})")
        results.append((tool.name, tool.ok, tool.problem or ""))

    heading("JavaScript runtime for YouTube (required by yt-dlp)")
    js = find_js_runtime()
    if js.ok:
        print(f"    {js.name} {js.version}: {js.path}")
    else:
        print(f"    NOT FOUND: {js.problem}")
        print("    Fix: run setup.bat again (it installs Deno into .venv), or install Deno from")
        print("         https://docs.deno.com/runtime/getting_started/installation/")
    results.append(("JavaScript runtime", js.ok, js.problem or ""))

    heading("Speech model")
    if args.skip_model:
        print("    Skipped (--skip-model).")
    elif config is None or missing:
        print("    Skipped because of the errors above.")
        results.append(("speech model", False, "not checked"))
    else:
        error = check_model(paths, config)
        if error:
            print(f"    FAILED: {error}")
        else:
            print(f"    Model '{config.model}' loads and runs on {config.device}.")
        results.append(("speech model", error is None, error or ""))

    print("")
    print("=" * 50)
    for name, ok, _ in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print("=" * 50)
    if all(ok for _, ok, _ in results) and args.skip_model:
        print("All checks passed, but the speech model was not checked (--skip-model). Run setup.bat without it.")
        return 1
    if all(ok for _, ok, _ in results):
        print("Setup complete. Put link files in the 'input' folder and double-click run.bat.")
        return 0
    print("Setup is NOT complete. Fix the FAIL items above, then run setup.bat again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
