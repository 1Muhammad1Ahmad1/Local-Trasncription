"""Project folder layout, always resolved relative to the application files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    root: Path = PROJECT_ROOT

    @property
    def input(self) -> Path:
        return self.root / "input"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def tools(self) -> Path:
        # Only used when setup installs a project-local FFmpeg.
        return self.root / "tools"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def state_file(self) -> Path:
        return self.state / "jobs.json"

    def ensure(self) -> None:
        for folder in (self.input, self.output, self.models, self.logs, self.state):
            folder.mkdir(parents=True, exist_ok=True)
