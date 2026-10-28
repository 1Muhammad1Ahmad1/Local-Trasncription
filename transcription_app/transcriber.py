"""Local speech recognition with faster-whisper."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import MODEL_SIZES, Config

log = logging.getLogger("transcription")

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")


class ModelLoadError(Exception):
    """The speech model could not be downloaded or loaded (stops the batch)."""


class TranscriptionFailed(Exception):
    """A single recording could not be transcribed; the batch continues."""


@dataclass
class TranscriptionInfo:
    language: str | None
    language_probability: float | None
    duration: float | None
    segments: int


def load_whisper_model(config: Config, models_dir: Path, announce: Callable[[str], None] = print):
    """Load the configured model, downloading it once into models_dir if needed."""
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise ModelLoadError(f"faster-whisper is not installed correctly ({exc}). Run setup.bat again.") from exc

    kwargs = dict(device=config.device, compute_type=config.compute_type, download_root=str(models_dir))
    try:
        model = WhisperModel(config.model, local_files_only=True, **kwargs)
        announce(f"Speech model '{config.model}' loaded from the local cache.")
        return model
    except Exception as exc:
        log.info("Model not in local cache (%s); downloading", exc)

    size = MODEL_SIZES.get(config.model, "a few hundred MB or more")
    announce(
        f"Downloading speech model '{config.model}' (about {size}, one-time download into models/)...\n"
        f"This is the speech-recognition model, not a video."
    )
    try:
        model = WhisperModel(config.model, local_files_only=False, **kwargs)
    except Exception as exc:
        hint = ""
        text = str(exc).lower()
        if config.device != "cpu" and ("cuda" in text or "cudnn" in text or "cublas" in text):
            hint = " GPU libraries are missing; set \"device\": \"cpu\" in config.json."
        elif any(w in text for w in ("connection", "resolve", "timed out", "network", "offline", "max retries")):
            hint = " Check your internet connection; the model must be downloaded once."
        raise ModelLoadError(f"Could not load speech model '{config.model}': {exc}.{hint}") from exc
    announce(f"Speech model '{config.model}' downloaded and loaded.")
    return model


class WhisperTranscriber:
    def __init__(self, model, config: Config):
        self.model = model
        self.config = config

    def transcribe(
        self,
        audio_path: Path,
        on_segment: Callable[[float, float, str], None],
        on_progress: Callable[[float, float | None], None] | None = None,
    ) -> TranscriptionInfo:
        try:
            segments, info = self.model.transcribe(
                str(audio_path),
                task="transcribe",                   # never translate
                language=self.config.whisper_language,
                beam_size=self.config.beam_size,
                vad_filter=self.config.vad_filter,   # skip silence -> fewer invented phrases
                vad_parameters={"min_silence_duration_ms": 1000},
                condition_on_previous_text=False,    # avoids runaway repetition loops
                without_timestamps=False,
            )
            log.info("Detected language %s (p=%.2f), duration %.0fs",
                     info.language, info.language_probability or 0, info.duration or 0)
            count = 0
            # segments is a lazy generator: transcription happens while iterating it,
            # so it must be consumed completely to cover the entire recording.
            for segment in segments:
                on_segment(segment.start, segment.end, segment.text)
                count += 1
                if on_progress:
                    on_progress(segment.end, info.duration)
        except (KeyboardInterrupt, MemoryError):
            raise
        except Exception as exc:
            raise TranscriptionFailed(f"transcription failed ({exc.__class__.__name__}: {exc}).") from exc
        if on_progress:
            on_progress(info.duration or 0.0, info.duration)
        return TranscriptionInfo(info.language, info.language_probability, info.duration, count)
