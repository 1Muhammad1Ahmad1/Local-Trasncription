"""Loading and validating config.json plus command-line overrides."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Bump when transcript formatting changes so earlier outputs get regenerated.
OUTPUT_FORMAT_VERSION = 1

DEFAULTS: dict[str, Any] = {
    "model": "small",
    "language": "auto",
    "device": "cpu",
    "compute_type": "int8",
    "beam_size": 5,
    "vad_filter": True,
    "paragraph_pause_seconds": 3.0,
    "download_retries": 3,
    "socket_timeout_seconds": 30,
}

DEVICES = ("cpu", "cuda", "auto")
COMPUTE_TYPES = (
    "int8", "int8_float32", "int8_float16", "int8_bfloat16",
    "int16", "float16", "bfloat16", "float32", "default", "auto",
)

# Approximate one-time download sizes, shown so a model download is not
# mistaken for a stuck video download.
MODEL_SIZES = {
    "tiny": "75 MB", "base": "145 MB", "small": "480 MB", "medium": "1.5 GB",
    "large-v1": "3 GB", "large-v2": "3 GB", "large-v3": "3 GB", "large": "3 GB",
    "large-v3-turbo": "1.6 GB", "turbo": "1.6 GB",
}

_FALLBACK_MODELS = (
    "tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
    "large-v1", "large-v2", "large-v3", "large", "large-v3-turbo", "turbo",
    "distil-large-v2", "distil-large-v3", "distil-large-v3.5",
    "distil-medium.en", "distil-small.en",
)

LANGUAGE_ALIASES = {"english": "en", "urdu": "ur", "hindi": "hi", "arabic": "ar"}


class ConfigError(Exception):
    """Raised when config.json or a command-line value is invalid."""


def known_models() -> tuple[str, ...]:
    try:
        from faster_whisper.utils import available_models

        return tuple(available_models())
    except Exception:
        return _FALLBACK_MODELS


def known_languages() -> tuple[str, ...]:
    try:
        from faster_whisper.tokenizer import _LANGUAGE_CODES

        return tuple(_LANGUAGE_CODES)
    except Exception:
        return ("en", "ur", "hi", "ar", "fa", "pa", "bn", "es", "fr", "de", "zh", "ja")


def is_english_only(model: str) -> bool:
    return model.endswith(".en")


@dataclass(frozen=True)
class Config:
    model: str
    language: str  # "auto" or a Whisper language code
    device: str
    compute_type: str
    beam_size: int
    vad_filter: bool
    paragraph_pause_seconds: float
    download_retries: int
    socket_timeout_seconds: int

    @property
    def whisper_language(self) -> str | None:
        return None if self.language == "auto" else self.language

    def transcription_settings(self) -> dict[str, Any]:
        """Settings that change the transcript; a change triggers reprocessing."""
        return {
            "model": self.model,
            "language": self.language,
            "task": "transcribe",
            "compute_type": self.compute_type,
            "beam_size": self.beam_size,
            "vad_filter": self.vad_filter,
            "paragraph_pause_seconds": self.paragraph_pause_seconds,
            "output_format": OUTPUT_FORMAT_VERSION,
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_default_config(path: Path) -> None:
    doc = {
        "_help": {
            "model": "Whisper model: tiny, base, small, medium, large-v3, turbo. Bigger = slower but usually more accurate. Avoid '.en' models for Urdu.",
            "language": "'auto' to detect, or a code such as 'en' (English) or 'ur' (Urdu). The spoken language is kept; nothing is translated.",
            "device": "'cpu' (reliable default). 'cuda' needs an NVIDIA GPU with CUDA 12 + cuDNN 9 installed separately.",
            "compute_type": "'int8' is fastest on CPU. Use 'float16' with cuda.",
            "beam_size": "1-10. Higher can be slightly more accurate but slower.",
            "vad_filter": "true skips long silences, which reduces invented text in silent parts.",
            "paragraph_pause_seconds": "Start a new paragraph after a pause this long (0 = never).",
            "download_retries": "How many times to retry a failed download (0-20).",
            "socket_timeout_seconds": "Network timeout for downloads (1-600).",
        },
        **DEFAULTS,
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_config(path: Path, overrides: dict[str, Any] | None = None) -> Config:
    if not path.exists():
        write_default_config(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path.name} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}.\n"
            f"Fix the file, or delete it to recreate the defaults."
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} must contain a JSON object {{ ... }}.")

    values = dict(DEFAULTS)
    unknown = []
    for key, value in raw.items():
        if key.startswith("_"):
            continue  # documentation entries
        if key not in DEFAULTS:
            unknown.append(key)
            continue
        values[key] = value
    if unknown:
        raise ConfigError(
            f"Unknown setting(s) in {path.name}: {', '.join(unknown)}.\n"
            f"Valid settings: {', '.join(DEFAULTS)}."
        )
    for key, value in (overrides or {}).items():
        if value is not None:
            values[key] = value
    return validate(values)


def validate(values: dict[str, Any]) -> Config:
    errors: list[str] = []

    model = values["model"]
    if not isinstance(model, str) or not model.strip():
        errors.append("model must be a model name such as \"small\".")
    else:
        model = model.strip()
        if model not in known_models() and not Path(model).is_dir():
            errors.append(
                f"model \"{model}\" is not a known faster-whisper model. "
                f"Choose one of: {', '.join(known_models())}."
            )

    language = values["language"]
    if not isinstance(language, str) or not language.strip():
        errors.append("language must be \"auto\" or a language code such as \"en\" or \"ur\".")
    else:
        language = LANGUAGE_ALIASES.get(language.strip().lower(), language.strip().lower())
        if language != "auto" and language not in known_languages():
            errors.append(
                f"language \"{values['language']}\" is not recognised. "
                f"Use \"auto\", \"en\" (English), \"ur\" (Urdu) or another Whisper language code."
            )
        elif isinstance(model, str) and is_english_only(model) and language not in ("auto", "en"):
            errors.append(
                f"model \"{model}\" only understands English but language is \"{language}\". "
                f"Use a multilingual model such as \"small\"."
            )

    device = values["device"]
    if device not in DEVICES:
        errors.append(f"device must be one of {', '.join(DEVICES)} (got {device!r}).")

    compute_type = values["compute_type"]
    if compute_type not in COMPUTE_TYPES:
        errors.append(f"compute_type must be one of {', '.join(COMPUTE_TYPES)} (got {compute_type!r}).")

    beam_size = values["beam_size"]
    if not _is_int(beam_size) or not 1 <= beam_size <= 10:
        errors.append(f"beam_size must be a whole number from 1 to 10 (got {beam_size!r}).")

    vad_filter = values["vad_filter"]
    if not isinstance(vad_filter, bool):
        errors.append(f"vad_filter must be true or false (got {vad_filter!r}).")

    pause = values["paragraph_pause_seconds"]
    if isinstance(pause, bool) or not isinstance(pause, (int, float)) or not 0 <= pause <= 60:
        errors.append(f"paragraph_pause_seconds must be a number from 0 to 60 (got {pause!r}).")

    retries = values["download_retries"]
    if not _is_int(retries) or not 0 <= retries <= 20:
        errors.append(f"download_retries must be a whole number from 0 to 20 (got {retries!r}).")

    timeout = values["socket_timeout_seconds"]
    if not _is_int(timeout) or not 1 <= timeout <= 600:
        errors.append(f"socket_timeout_seconds must be a whole number from 1 to 600 (got {timeout!r}).")

    if errors:
        raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(errors))

    return Config(
        model=model,
        language=language,
        device=device,
        compute_type=compute_type,
        beam_size=beam_size,
        vad_filter=vad_filter,
        paragraph_pause_seconds=float(pause),
        download_retries=retries,
        socket_timeout_seconds=timeout,
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
