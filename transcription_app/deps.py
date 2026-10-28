"""Locating and checking external tools (FFmpeg, ffprobe, JavaScript runtime)."""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import Paths

EXE = ".exe" if os.name == "nt" else ""

# From the yt-dlp EJS wiki: minimum JavaScript runtime versions for YouTube.
DENO_MIN = (2, 3, 0)
NODE_MIN = (22, 0, 0)

REQUIRED_MODULES = {
    "yt_dlp": "yt-dlp",
    "yt_dlp_ejs": "yt-dlp-ejs (YouTube challenge solver)",
    "faster_whisper": "faster-whisper",
    "ctranslate2": "ctranslate2",
    "av": "av (PyAV audio decoding)",
}


@dataclass
class Tool:
    name: str
    path: str | None = None
    version: str | None = None
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None and self.problem is None


def _run_version(exe: str, arg: str = "-version") -> tuple[str | None, str | None]:
    """Execute a tool to prove it works. Returns (first output line, problem)."""
    try:
        result = subprocess.run(
            [exe, arg], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not be started ({exc})"
    if result.returncode != 0:
        return None, f"exited with code {result.returncode}"
    line = (result.stdout or result.stderr).strip().splitlines()
    return (line[0] if line else ""), None


def _candidates(local: Path, name: str) -> list[str]:
    found = []
    if (local / (name + EXE)).is_file():
        found.append(str(local / (name + EXE)))
    on_path = shutil.which(name)
    if on_path and on_path not in found:
        found.append(on_path)
    return found


def local_ffmpeg_dir(paths: Paths) -> Path:
    return paths.tools / "ffmpeg" / "bin"


def find_ffmpeg_tool(paths: Paths, name: str) -> Tool:
    """Find a working ffmpeg/ffprobe: project-local copy first, then PATH."""
    tool = Tool(name)
    candidates = _candidates(local_ffmpeg_dir(paths), name)
    if not candidates:
        tool.problem = "not found"
        return tool
    problems = []
    for exe in candidates:
        version, problem = _run_version(exe)
        if problem is None:
            tool.path, tool.version = exe, version
            return tool
        problems.append(f"{exe} {problem}")
    tool.problem = "; ".join(problems)
    return tool


def _parse_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in match.groups()) if match else None


def find_js_runtime() -> Tool:
    """Find a JavaScript runtime that yt-dlp can use for YouTube.

    Deno (recommended by yt-dlp) is installed into the virtual environment by
    requirements.txt; Node.js 22+ on PATH is accepted as a fallback.
    """
    problems = []
    deno_paths = []
    try:
        import deno  # type: ignore

        deno_paths.append(deno.find_deno_bin())
    except Exception:
        pass
    if shutil.which("deno") and shutil.which("deno") not in deno_paths:
        deno_paths.append(shutil.which("deno"))
    for exe in deno_paths:
        version, problem = _run_version(exe, "--version")
        parsed = _parse_version(version or "")
        if problem:
            problems.append(f"deno at {exe} {problem}")
        elif parsed and parsed < DENO_MIN:
            problems.append(f"deno at {exe} is version {version}, need 2.3.0 or newer")
        else:
            return Tool("deno", exe, version)
    node = shutil.which("node")
    if node:
        version, problem = _run_version(node, "--version")
        parsed = _parse_version(version or "")
        if problem:
            problems.append(f"node at {node} {problem}")
        elif parsed and parsed < NODE_MIN:
            problems.append(f"node at {node} is version {version}, need 22 or newer")
        else:
            return Tool("node", node, version)
    return Tool("deno", problem="; ".join(problems) or "no JavaScript runtime (deno or node 22+) found")


def missing_modules() -> list[str]:
    missing = []
    for module, label in REQUIRED_MODULES.items():
        try:
            importlib.import_module(module)
        except Exception as exc:
            missing.append(f"{label} ({exc.__class__.__name__}: {exc})")
    return missing


@dataclass
class RuntimeTools:
    ffmpeg: Tool
    ffprobe: Tool
    js: Tool


def check_runtime(paths: Paths) -> tuple[RuntimeTools, list[str]]:
    """Check everything needed to process videos. Returns (tools, problems)."""
    problems = []
    for item in missing_modules():
        problems.append(f"Python package missing or broken: {item}. Run setup.bat again.")
    ffmpeg = find_ffmpeg_tool(paths, "ffmpeg")
    ffprobe = find_ffmpeg_tool(paths, "ffprobe")
    for tool in (ffmpeg, ffprobe):
        if not tool.ok:
            problems.append(
                f"{tool.name} is not available ({tool.problem}). Run setup.bat, which installs a "
                f"project-local copy, or install FFmpeg yourself and add its 'bin' folder to PATH."
            )
    js = find_js_runtime()
    if not js.ok:
        problems.append(
            f"No JavaScript runtime for YouTube downloads ({js.problem}). Run setup.bat again "
            f"(it installs Deno into the project), or install Deno from https://deno.com."
        )
    return RuntimeTools(ffmpeg, ffprobe, js), problems
