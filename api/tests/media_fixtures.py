"""Synthetic test media with known events, so detection can be checked exactly."""
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 16000
SCREAM_AT = (60.0, 63.0)
LAUGH_AT = (120.0, 124.0)


def _voice(t: np.ndarray, f0: float, top: float) -> np.ndarray:
    """Harmonic "voice": harmonics of f0 up to `top` Hz, falling off like speech."""
    k = np.arange(1, int(top // f0) + 1)
    return (np.sin(2 * np.pi * f0 * np.outer(t, k)) / k).sum(axis=1) / np.log(len(k) + 1)


def synth_audio(seconds: float = 180.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    # "Conversation": a low voice with an irregular syllable envelope.
    syll = np.repeat(rng.uniform(0.2, 1.0, int(seconds * 4) + 1), SR // 4)[: len(t)]
    x = 0.05 * syll * _voice(t, 140, 3000) + 0.001 * rng.standard_normal(len(t))
    s0, s1 = (int(v * SR) for v in SCREAM_AT)
    # Scream: loud and high-pitched with lots of bright harmonics + breath noise.
    x[s0:s1] = 0.45 * _voice(t[s0:s1], 900, 7000) + 0.05 * rng.standard_normal(s1 - s0)
    l0, l1 = (int(v * SR) for v in LAUGH_AT)
    # Laughter: "ha-ha-ha" voiced pulses at 5 Hz, a bit louder than the talk.
    tl = t[l0:l1]
    pulses = (np.sin(2 * np.pi * 5 * tl) > 0.2).astype(np.float32)
    x[l0:l1] = 0.15 * pulses * _voice(tl, 260, 4000)
    return np.clip(x, -1, 1).astype(np.float32)


def write_wav(path: Path, x: np.ndarray) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    return path


def make_video(path: Path, seconds: float = 180.0) -> Path:
    wav = write_wav(path.with_suffix(".wav"), synth_audio(seconds))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=15:d={seconds}",
                    "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                    str(path)], check=True)
    wav.unlink()
    return path


def fake_transcript(seconds: float = 180.0) -> dict:
    """A sentence every 5 s; laughter written out around LAUGH_AT like Whisper does."""
    segs = []
    for start in np.arange(0, seconds, 5.0):
        words = []
        for i, w in enumerate(["esto", "es", "una", "frase", "de", "prueba"]):
            ws = float(start + i * 0.7)
            words.append({"start": ws, "end": ws + 0.5, "word": w, "p": 0.9})
        if LAUGH_AT[0] <= start < LAUGH_AT[1]:
            words.append({"start": float(start + 4.3), "end": float(start + 4.8), "word": "jajaja", "p": 0.9})
        segs.append({"start": float(start), "end": float(start + 4.8),
                     "text": " ".join(w["word"] for w in words), "words": words})
    return {"language": "es", "duration": seconds, "segments": segs}
