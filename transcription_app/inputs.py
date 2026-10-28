"""Finding and validating link files in the input folder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class LinkFile:
    name: str            # exact filename, reused for the transcript
    path: Path
    url: str | None      # set when the file is valid
    error: str | None    # set when the file is invalid or unreadable


def scan_input_folder(input_dir: Path) -> list[Path]:
    """Return .txt files directly inside input_dir (no recursion), in a stable order."""
    files = [
        entry for entry in input_dir.iterdir()
        if entry.suffix.lower() == ".txt" and entry.is_file()
    ]
    return sorted(files, key=lambda p: (p.name.casefold(), p.name))


def looks_like_url(token: str) -> bool:
    try:
        parts = urlsplit(token)
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def parse_link_text(text: str) -> tuple[str | None, str | None]:
    """Return (url, None) for valid content, otherwise (None, reason)."""
    tokens = text.split()
    if not tokens:
        return None, "the file is empty. Put exactly one video link in it."
    if len(tokens) > 1:
        url_count = sum(1 for token in tokens if looks_like_url(token))
        if url_count > 1:
            return None, f"the file contains {url_count} links. Put exactly one link per file."
        return None, "the file contains extra text besides the link. It must contain only one link."
    token = tokens[0]
    if not looks_like_url(token):
        return None, (
            f"\"{_shorten(token)}\" is not a valid web link. "
            "It must start with http:// or https://"
        )
    return token, None


def read_link_file(path: Path) -> LinkFile:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return LinkFile(path.name, path, None, f"the file could not be read ({exc.strerror or exc}).")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return LinkFile(path.name, path, None, "the file is not UTF-8 text. Save it as UTF-8 in your editor.")
    url, error = parse_link_text(text)
    return LinkFile(path.name, path, url, error)


def _shorten(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."
