"""Audio-only downloads through the yt-dlp Python API."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("transcription")


class DownloadFailed(Exception):
    """A single video could not be downloaded; the batch continues."""


@dataclass
class DownloadedAudio:
    path: Path
    title: str | None
    duration: float | None  # seconds, as measured by ffprobe when possible
    extractor: str | None


class _YtdlpLogger:
    """Route yt-dlp messages to the log file instead of the terminal."""

    def __init__(self):
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        log.error("yt-dlp: %s", msg)


# (substring in yt-dlp's message, explanation shown to the user)
_ERROR_HINTS = [
    ("private video", "the video is private. Only the owner's approved accounts can watch it."),
    ("confirm your age", "the video is age-restricted and needs a signed-in account. This app does not use your browser cookies or passwords, so it was skipped."),
    ("age-restricted", "the video is age-restricted and needs a signed-in account. This app does not use your browser cookies or passwords, so it was skipped."),
    ("not a bot", "YouTube asked for a sign-in to confirm you are not a bot. Try again later, or from another network. Updating yt-dlp (run setup.bat) can also help."),
    ("members-only", "the video is for channel members only and needs a signed-in account."),
    ("join this channel", "the video is for channel members only and needs a signed-in account."),
    ("requires payment", "the video must be purchased or rented."),
    ("available in your country", "the video is region-restricted and not available from your location."),
    ("geo restrict", "the video is region-restricted and not available from your location."),
    ("has been removed", "the video has been removed or deleted."),
    ("account associated with this video has been terminated", "the video's channel was terminated, so the video is gone."),
    ("video unavailable", "the video is unavailable (deleted, private, or blocked)."),
    ("this video is unavailable", "the video is unavailable (deleted, private, or blocked)."),
    ("premieres in", "the video is a premiere that has not started yet."),
    ("live event will begin", "the livestream has not started yet."),
    ("login required", "the site requires a signed-in account for this video."),
    ("sign in", "the site requires a signed-in account for this video."),
    ("cookies", "the site requires a signed-in account (cookies) for this video."),
    ("unsupported url", "yt-dlp does not support this website or link format."),
    ("http error 404", "the page was not found (HTTP 404). Check the link."),
    ("http error 403", "the server refused the download (HTTP 403). Updating yt-dlp (run setup.bat) often fixes this for YouTube."),
    ("http error 429", "the site is rate-limiting requests (HTTP 429). Wait a while and try again."),
    ("javascript runtime", "yt-dlp could not run its YouTube JavaScript solver. Run setup.bat again."),
    ("n challenge", "yt-dlp could not solve YouTube's JavaScript challenge. Update yt-dlp by running setup.bat."),
    ("requested format is not available", "no downloadable audio format was offered. Updating yt-dlp (run setup.bat) may help."),
    ("getaddrinfo failed", "no internet connection, or the website name could not be found."),
    ("name or service not known", "no internet connection, or the website name could not be found."),
    ("timed out", "the network connection timed out. Check your internet connection and try again."),
    ("connection reset", "the network connection was interrupted. Try again."),
    ("unable to download webpage", "the web page could not be downloaded. Check your internet connection and the link."),
]


def explain_error(message: str) -> str:
    lowered = message.lower()
    for needle, hint in _ERROR_HINTS:
        if needle in lowered:
            return hint
    return "the download failed."


def _clean_message(message: str) -> str:
    message = message.replace("ERROR: ", "").strip()
    return message.splitlines()[0][:300] if message else "unknown error"


def probe_audio(ffprobe: str, path: Path) -> float | None:
    """Confirm the file contains an audio stream; return its duration in seconds."""
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DownloadFailed(f"could not inspect the downloaded audio with ffprobe ({exc}).") from exc
    if result.returncode != 0:
        raise DownloadFailed(f"the downloaded file is not readable audio ({_clean_message(result.stderr)}).")
    data = json.loads(result.stdout or "{}")
    if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
        raise DownloadFailed("the downloaded file contains no audio track.")
    try:
        return float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        return None


class AudioDownloader:
    def __init__(
        self,
        ffmpeg_path: str,
        ffprobe_path: str,
        js_runtime: tuple[str, str] | None,
        retries: int = 3,
        socket_timeout: int = 30,
    ):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.js_runtime = js_runtime
        self.retries = retries
        self.socket_timeout = socket_timeout

    def _options(self, dest: Path, logger: _YtdlpLogger, hook: Callable) -> dict:
        opts = {
            # Audio-only stream; a combined file is used only if a site has no audio-only format.
            "format": "bestaudio/best",
            "paths": {"home": str(dest), "temp": str(dest)},
            "outtmpl": {"default": "audio.%(ext)s"},
            "noplaylist": True,
            "extract_flat": "in_playlist",
            "logger": logger,
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "progress_hooks": [hook],
            "retries": self.retries,
            "fragment_retries": self.retries,
            "extractor_retries": self.retries,
            "file_access_retries": 3,
            "socket_timeout": self.socket_timeout,
            "ffmpeg_location": str(Path(self.ffmpeg_path).parent),
            "overwrites": True,
            "continuedl": False,
            "writethumbnail": False,
            "writesubtitles": False,
            "writeautomaticsub": False,
            "live_from_start": False,
            "wait_for_video": None,
            "cachedir": False,
        }
        if self.js_runtime:
            name, exe = self.js_runtime
            opts["js_runtimes"] = {name: {"path": exe}}
        return opts

    def download(self, url: str, dest: Path, on_progress: Callable[[int, int | None], None] | None = None) -> DownloadedAudio:
        import yt_dlp
        from yt_dlp.utils import DownloadError, ExtractorError

        logger = _YtdlpLogger()

        def hook(status: dict) -> None:
            if on_progress and status.get("status") == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate")
                on_progress(int(status.get("downloaded_bytes") or 0), int(total) if total else None)

        try:
            with yt_dlp.YoutubeDL(self._options(dest, logger, hook)) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
                # Follow redirect-style results (e.g. short links) without processing playlists.
                for _ in range(5):
                    if info and info.get("_type") in ("url", "url_transparent"):
                        info = ydl.extract_info(info["url"], download=False, process=False, ie_key=info.get("ie_key"))
                    else:
                        break
                self._reject_unsuitable(info)
                result = ydl.process_ie_result(info, download=True)
        except DownloadFailed:
            raise
        except (DownloadError, ExtractorError) as exc:
            raw = _clean_message(logger.errors[-1] if logger.errors else str(exc))
            raise DownloadFailed(f"{explain_error(raw)}\n    Details: {raw}") from None

        audio = self._find_file(result, dest)
        probed = probe_audio(self.ffprobe_path, audio)
        expected = result.get("duration")
        if probed is not None and expected:
            # Guard against a silently truncated download.
            if probed < expected - max(5.0, expected * 0.03):
                raise DownloadFailed(
                    f"the download looks incomplete ({probed:.0f}s of audio, expected about {expected:.0f}s)."
                )
        log.info("Downloaded %s (%s, %.1f MB, %ss)", result.get("id"), result.get("ext"),
                 audio.stat().st_size / 1e6, round(probed or expected or 0))
        return DownloadedAudio(audio, result.get("title"), probed or expected, result.get("extractor"))

    @staticmethod
    def _reject_unsuitable(info: dict | None) -> None:
        if not info:
            raise DownloadFailed("yt-dlp returned no information for this link.")
        kind = info.get("_type", "video")
        if kind in ("playlist", "multi_video"):
            raise DownloadFailed(
                "the link points to a playlist or channel, not a single video. "
                "Use the link of one video (a link with both v= and list= is fine)."
            )
        live_status = info.get("live_status")
        if live_status == "is_live" or (live_status is None and info.get("is_live")):
            raise DownloadFailed("the link is a livestream that is still live. Try again after it ends.")
        if live_status == "is_upcoming":
            raise DownloadFailed("the link is a scheduled livestream or premiere that has not started yet.")
        if live_status == "post_live":
            raise DownloadFailed("the livestream just ended and is still being processed by the site. Try again later.")

    @staticmethod
    def _find_file(result: dict, dest: Path) -> Path:
        for item in result.get("requested_downloads") or []:
            path = item.get("filepath")
            if path and Path(path).is_file():
                return Path(path)
        files = [p for p in dest.iterdir() if p.is_file() and p.name.startswith("audio.")
                 and not p.name.endswith((".part", ".ytdl"))]
        if not files:
            raise DownloadFailed("yt-dlp finished but no audio file was produced.")
        return max(files, key=lambda p: p.stat().st_size)

