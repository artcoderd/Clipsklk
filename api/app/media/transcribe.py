"""Speech-to-text with per-word timestamps (faster-whisper / CTranslate2)."""
import logging
from collections.abc import Callable
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)
_model = None


def _resolve() -> tuple[str, str, str]:
    s = get_settings()
    device = s.whisper_device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    compute = s.whisper_compute_type
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    model = s.whisper_model
    if model == "auto":
        model = "large-v3" if device == "cuda" else "small"
    return model, device, compute


def get_model():
    """Loaded once per worker process and kept in GPU memory between jobs."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        name, device, compute = _resolve()
        log.info("loading whisper %s on %s (%s)", name, device, compute)
        _model = WhisperModel(name, device=device, compute_type=compute)
    return _model


def _load_wav(path: str | Path):
    """Our own 16 kHz mono WAV (from ffmpeg.extract_audio) as float32 samples.
    Passing samples avoids faster-whisper's PyAV decoding path entirely."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("expected 16 kHz mono 16-bit WAV")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(audio: str | Path, duration: float, on_progress: Callable[[float], None] | None = None,
               language: str | None = None) -> dict:
    segments, info = get_model().transcribe(
        _load_wav(audio),
        language=language,
        word_timestamps=True,
        vad_filter=True,                    # skip silence/music: faster, fewer hallucinations
        beam_size=5,
        condition_on_previous_text=False,   # avoids repetition loops on hours-long audio
    )
    out = []
    for seg in segments:  # generator: decoding happens while iterating
        out.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip(),
                       "p": round(w.probability, 3)} for w in (seg.words or [])],
        })
        if on_progress and duration > 0:
            on_progress(min(1.0, seg.end / duration))
    return {"language": info.language, "duration": duration, "segments": out}
