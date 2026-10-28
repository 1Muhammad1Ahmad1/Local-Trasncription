"""Writing transcripts safely: temp file first, then an atomic replace."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

PARTIAL_PREFIX = ".partial-"
PARTIAL_SUFFIX = ".tmp"


class OutputReplaceError(Exception):
    """The finished transcript could not be moved into place."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cleanup_stale_partials(output_dir: Path) -> int:
    """Remove temporary transcript files left behind by a crashed run."""
    removed = 0
    for entry in output_dir.glob(PARTIAL_PREFIX + "*" + PARTIAL_SUFFIX):
        try:
            entry.unlink()
            removed += 1
        except OSError:
            pass
    return removed


class TranscriptWriter:
    """Streams segments into a hidden temp file in the output folder.

    Nothing appears under the final filename until commit() succeeds.
    """

    def __init__(self, final_path: Path, paragraph_pause: float = 3.0):
        self.final_path = final_path
        self.paragraph_pause = paragraph_pause
        fd, tmp = tempfile.mkstemp(dir=final_path.parent, prefix=PARTIAL_PREFIX, suffix=PARTIAL_SUFFIX)
        self.temp_path = Path(tmp)
        self._fh = os.fdopen(fd, "w", encoding="utf-8")
        self._prev_end: float | None = None
        self._committed = False
        self.segment_count = 0

    @property
    def has_text(self) -> bool:
        return self.segment_count > 0

    def add_segment(self, start: float, end: float, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        if (
            self._prev_end is not None
            and self.paragraph_pause > 0
            and start - self._prev_end >= self.paragraph_pause
        ):
            self._fh.write("\n")
        self._fh.write(text + "\n")
        self._prev_end = end
        self.segment_count += 1

    def commit(self) -> str:
        """Atomically move the transcript into place. Returns its SHA-256."""
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        digest = sha256_file(self.temp_path)
        try:
            os.replace(self.temp_path, self.final_path)
        except OSError as exc:
            self.discard()
            raise OutputReplaceError(
                f"could not save {self.final_path.name} ({exc.strerror or exc}). "
                "Is the file open in another program?"
            ) from exc
        self._committed = True
        return digest

    def discard(self) -> None:
        if not self._fh.closed:
            self._fh.close()
        try:
            self.temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def __enter__(self) -> "TranscriptWriter":
        return self

    def __exit__(self, *exc_info) -> None:
        if not self._committed:
            self.discard()
