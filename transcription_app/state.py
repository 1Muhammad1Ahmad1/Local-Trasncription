"""Job records kept in state/jobs.json, separate from the transcripts."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .output import sha256_file

STATE_VERSION = 1

log = logging.getLogger("transcription")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.jobs: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            jobs = data.get("jobs", {})
            if not isinstance(jobs, dict):
                raise ValueError("'jobs' is not an object")
            self.jobs = jobs
        except (OSError, ValueError, AttributeError) as exc:
            backup = self.path.with_name(f"jobs.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
            log.warning("State file unreadable (%s); moved to %s", exc, backup.name)
            print(f"Warning: the job record file was damaged and has been set aside as state/{backup.name}.")
            print("         All link files will be treated as new.")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            self.jobs = {}

    def get(self, name: str) -> dict[str, Any] | None:
        record = self.jobs.get(name)
        return record if isinstance(record, dict) else None

    def put(self, name: str, record: dict[str, Any]) -> None:
        record = dict(record, input_file=name, updated_at=now_iso())
        self.jobs[name] = record
        self.save()

    def save(self) -> None:
        payload = json.dumps({"version": STATE_VERSION, "jobs": self.jobs}, indent=2, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".jobs-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def check_output(path: Path) -> tuple[bool, str | None]:
    """Basic integrity check for a finished transcript. Returns (ok, problem)."""
    if not path.is_file():
        return False, "the transcript file is missing"
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as exc:
        return False, f"the transcript cannot be read ({exc.strerror or exc})"
    except UnicodeDecodeError:
        return False, "the transcript is not valid UTF-8 text"
    if not text.strip():
        return False, "the transcript is empty"
    return True, None


def decide(
    record: dict[str, Any] | None,
    url: str,
    settings: dict[str, Any],
    output_path: Path,
    force: bool,
) -> tuple[bool, str]:
    """Return (process?, reason). A job is skipped only when a matching completed
    record exists and its transcript still passes the integrity check."""
    if force:
        return True, "forced reprocessing (--force)"
    if record is None:
        return True, "new link file"
    status = record.get("status")
    if record.get("url") != url:
        return True, "the link in this file changed"
    if record.get("settings") != settings:
        return True, "transcription settings changed"
    if status != "completed":
        return True, {
            "failed": "the previous attempt failed",
            "empty": "the previous attempt found no speech",
            "interrupted": "the previous attempt was interrupted",
            "in_progress": "the previous attempt did not finish",
        }.get(status, "no completed result recorded")
    ok, problem = check_output(output_path)
    if not ok:
        return True, f"{problem}, so it will be recreated"
    if record.get("output_sha256") and sha256_file(output_path) != record["output_sha256"]:
        # Edited by hand after creation: keep the user's version.
        return False, "already done (transcript was edited after creation; use --force to regenerate)"
    return False, "already done"
