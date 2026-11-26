"""Audio analysis without AI: per-second signals for volume peaks, screams and
laughter. Streams the WAV in chunks, so multi-hour sources use little memory.

All signals are 0..1 and *relative to the surrounding minutes* (rolling
baseline), so a loud gameplay and a quiet podcast both get meaningful peaks.

- volume: loudness above the local baseline.
- scream: loud AND brighter than usual (high spectral centroid).
- laugh:  energy pulsing regularly at 3.5-7 Hz ("ja-ja-ja"), above baseline.
These are heuristics; the transcript (e.g. "jajaja", "[risas]") reinforces
them in moments.py and phase 5 adds an AI pass on top.
"""
import wave
from pathlib import Path

import warnings

import numpy as np

SR = 16000
HOP = 800                 # 50 ms frames -> 20 frames per second
FPS = SR // HOP
_EPS = 1e-10


def _frames(wav_path: Path, chunk_seconds: int = 60):
    with wave.open(str(wav_path), "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("expected 16 kHz mono 16-bit WAV")
        while True:
            raw = w.readframes(SR * chunk_seconds)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            n = len(x) // HOP
            if n:
                yield x[: n * HOP].reshape(n, HOP)


def frame_features(wav_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Per 50 ms frame: (rms linear, spectral centroid Hz)."""
    window = np.hanning(HOP).astype(np.float32)
    freqs = np.fft.rfftfreq(HOP, 1 / SR).astype(np.float32)
    rms_parts, cent_parts = [], []
    for fr in _frames(wav_path):
        rms_parts.append(np.sqrt(np.mean(fr ** 2, axis=1)))
        mag = np.abs(np.fft.rfft(fr * window, axis=1))
        cent_parts.append((mag * freqs).sum(axis=1) / (mag.sum(axis=1) + _EPS))
    if not rms_parts:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    return np.concatenate(rms_parts), np.concatenate(cent_parts)


def _rolling_median(x: np.ndarray, win: int, ignore: np.ndarray | None = None,
                    causal: bool = False) -> np.ndarray:
    """Median of the values within +-win/2 seconds. Windows are truncated at the
    edges (never padded) and `ignore`d seconds, such as digital silence, don't
    count, so pauses can't drag the baseline down."""
    if len(x) == 0:
        return x
    vals = x.astype(np.float32).copy()
    if ignore is not None:
        vals[ignore] = np.nan
    if np.all(np.isnan(vals)):
        return np.full(len(x), np.nan_to_num(np.median(x)), np.float32)
    if causal:   # live: only the past is known
        padded = np.pad(vals, (win - 1, 0), mode="constant", constant_values=np.nan)
        view = np.lib.stride_tricks.sliding_window_view(padded, win)
    else:
        half = win // 2
        padded = np.pad(vals, half, mode="constant", constant_values=np.nan)
        view = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    out = np.empty(len(x), dtype=np.float32)
    step = 2000  # chunked to bound memory on multi-hour inputs
    with warnings.catch_warnings():  # all-NaN windows are handled just below
        warnings.simplefilter("ignore", RuntimeWarning)
        for i in range(0, len(x), step):
            out[i:i + step] = np.nanmedian(view[i:i + step], axis=1)
    # Seconds whose whole window was ignored: fall back to the global baseline.
    return np.where(np.isnan(out), np.nanmedian(vals), out).astype(np.float32)


def _laugh_periodicity(rms: np.ndarray, n_sec: int) -> np.ndarray:
    """For each second, how concentrated the 2 s loudness envelope's modulation
    spectrum is around 3.5-7 Hz (rhythmic "ha-ha-ha" bursts)."""
    win = 2 * FPS
    padded = np.pad(rms, (FPS // 2, win), mode="constant")
    freqs = np.fft.rfftfreq(win, 1 / FPS)
    band = (freqs >= 3.5) & (freqs <= 7.0)
    ref = (freqs >= 1.0) & (freqs <= 10.0)
    out = np.zeros(n_sec, dtype=np.float32)
    for s in range(n_sec):
        env = padded[s * FPS: s * FPS + win]
        if env.max() < 1e-3:
            continue
        env = (env - env.mean()) / (env.std() + _EPS)
        p = np.abs(np.fft.rfft(env)) ** 2
        total = p[ref].sum()
        if total > 0:
            # Peakiness: the strongest in-band bin vs. everything 1-10 Hz.
            out[s] = p[band].max() / total
    return out


def second_features(wav_path: str | Path) -> dict:
    """Per-second raw features: loudness (dB), spectral centroid, laugh periodicity."""
    rms, cent = frame_features(Path(wav_path))
    n_sec = int(np.ceil(len(rms) / FPS))
    if n_sec == 0:
        return {"loud": np.zeros(0, np.float32), "centroid": np.zeros(0, np.float32),
                "periodic": np.zeros(0, np.float32)}
    pad = n_sec * FPS - len(rms)
    rms_p = np.pad(rms, (0, pad)).reshape(n_sec, FPS)
    cent_p = np.pad(cent, (0, pad)).reshape(n_sec, FPS)
    db = 20 * np.log10(rms_p + 1e-5)
    loud = np.percentile(db, 90, axis=1).astype(np.float32)
    active = db > (loud[:, None] - 10)
    centroid = ((cent_p * active).sum(axis=1) / np.maximum(active.sum(axis=1), 1)).astype(np.float32)
    return {"loud": loud, "centroid": centroid, "periodic": _laugh_periodicity(rms, n_sec)}


def signals(loud: np.ndarray, centroid: np.ndarray, periodic: np.ndarray, causal: bool = False) -> dict:
    """Features -> 0..1 signals relative to the surrounding (or, live, past) ~5 minutes."""
    n_sec = len(loud)
    if n_sec == 0:
        return {"seconds": 0, "loudness_db": [], "volume": [], "scream": [], "laugh": [], "silence": []}
    quiet = loud < -55                         # pauses / digital silence
    base = _rolling_median(loud, 301, ignore=quiet, causal=causal)   # ~5 min baseline
    cbase = _rolling_median(centroid, 301, ignore=quiet, causal=causal)
    above = loud - base

    volume = np.clip((above - 3) / 9, 0, 1)
    brightness = np.clip((centroid - cbase - 250) / 1200, 0, 1)
    scream = volume * brightness * (loud > -32)
    periodic = np.clip((periodic - 0.25) / 0.35, 0, 1)
    # Laughter is often no louder than the talk around it: only clearly quieter is penalised.
    laugh = periodic * np.clip((above + 6) / 8, 0, 1) * (loud > -40)
    silence = (loud < -50) | (above < -15)

    r = lambda a: [round(float(v), 3) for v in a]  # noqa: E731
    return {
        "seconds": n_sec,
        "loudness_db": r(loud),
        "volume": r(volume),
        "scream": r(scream),
        "laugh": r(laugh),
        "silence": [bool(v) for v in silence],
    }


def analyze(wav_path: str | Path) -> dict:
    f = second_features(wav_path)
    return signals(f["loud"], f["centroid"], f["periodic"])
