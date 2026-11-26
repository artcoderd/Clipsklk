"""Incremental (live) moment detection.

Audio arrives segment by segment; features are computed per second as soon
as they're complete (laughter needs ~2 s of context, so seconds become final
with a 2 s delay). Signals use a *causal* baseline (the previous 5 minutes)
because the future isn't known yet. Chat activity per second is compared with
the chat's own recent normal rate.

AutoClipper turns that into "make a clip now" decisions: a peak above the
sensitivity threshold, then wait for the payoff to be recorded, with a
cooldown and an hourly cap so a long stream doesn't produce hundreds of clips.
"""
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from app.media.analysis import FPS, HOP, SR, _laugh_periodicity, signals

THRESHOLDS = {"low": 0.8, "normal": 0.6, "high": 0.45}


def combine(laugh, scream, volume, chat):
    """One strong signal is enough (a scream, or chat exploding); audio and
    chat reacting together reinforce each other."""
    audio = np.maximum(np.maximum(laugh, scream), 0.8 * volume)
    return np.clip(np.maximum(audio, chat) + 0.25 * np.minimum(audio, chat), 0, 1)


class LiveAudio:
    """Turns a stream of 16 kHz mono samples into per-second features."""

    def __init__(self):
        self._rest = np.zeros(0, np.float32)
        self._rms: list[np.ndarray] = []
        self._cent: list[np.ndarray] = []
        self.rms = np.zeros(0, np.float32)
        self.cent = np.zeros(0, np.float32)
        self.loud: list[float] = []
        self.centroid: list[float] = []
        self.periodic: list[float] = []
        self._win = np.hanning(HOP).astype(np.float32)
        self._freqs = np.fft.rfftfreq(HOP, 1 / SR).astype(np.float32)

    def add(self, samples: np.ndarray) -> int:
        """Adds samples; returns how many seconds are now final."""
        x = np.concatenate([self._rest, samples.astype(np.float32)])
        n = len(x) // HOP
        self._rest = x[n * HOP:]
        if n:
            fr = x[: n * HOP].reshape(n, HOP)
            mag = np.abs(np.fft.rfft(fr * self._win, axis=1))
            self.rms = np.concatenate([self.rms, np.sqrt(np.mean(fr ** 2, axis=1))])
            self.cent = np.concatenate([self.cent, (mag * self._freqs).sum(axis=1) / (mag.sum(axis=1) + 1e-10)])
        # A second is final once 2 s of frames after it exist (laughter context).
        ready = max(0, len(self.rms) // FPS - 2)
        for s in range(len(self.loud), ready):
            rms = self.rms[s * FPS:(s + 1) * FPS]
            db = 20 * np.log10(rms + 1e-5)
            loud = float(np.percentile(db, 90))
            active = db > loud - 10
            cent = self.cent[s * FPS:(s + 1) * FPS]
            self.loud.append(loud)
            self.centroid.append(float((cent * active).sum() / max(1, active.sum())))
            lo = max(0, s - 1)
            per = _laugh_periodicity(self.rms[lo * FPS:(s + 3) * FPS], (s - lo) + 1)
            self.periodic.append(float(per[s - lo]))
        return len(self.loud)


def chat_scores(rates: np.ndarray, hype: np.ndarray, connected: np.ndarray) -> np.ndarray:
    """0..1 per second: how far chat is above its own recent normal."""
    n = len(rates)
    if n == 0:
        return np.zeros(0, np.float32)
    k = np.ones(5) / 5
    rate5 = np.convolve(rates, k, mode="full")[:n]       # causal 5 s average
    hype5 = np.convolve(hype, np.ones(5), mode="full")[:n]
    out = np.zeros(n, np.float32)
    for i in range(n):
        if not connected[i]:
            continue
        past = rate5[max(0, i - 300):i]
        past = past[connected[max(0, i - 300):i]]
        base = float(np.median(past)) if len(past) >= 30 else float(rate5[i]) / 2
        surge = np.clip((rate5[i] / (base + 0.3) - 2.0) / 3.0, 0, 1)
        out[i] = min(1.0, surge + (0.3 if hype5[i] >= 3 else 0.0))
    return out


@dataclass
class Peak:
    second: int
    score: float
    reasons: list[str]


@dataclass
class AutoClipper:
    clip_seconds: float = 45.0
    sensitivity: str = "normal"
    cooldown: float = 60.0
    max_per_hour: int = 12
    last_peak: float = -1e9
    made: list[float] = field(default_factory=list)
    _candidate: Peak | None = None

    def scan(self, t_now: int, interest: np.ndarray, parts: dict[str, np.ndarray]) -> Peak | None:
        """Looks at seconds up to t_now; returns a peak once it is confirmed
        (the score has come down again, or 6 s passed)."""
        th = THRESHOLDS.get(self.sensitivity, THRESHOLDS["normal"])
        if t_now >= len(interest):
            return None
        v = float(interest[t_now])
        if v >= th and t_now - self.last_peak >= max(self.cooldown, self.clip_seconds):
            if self._candidate is None or v > self._candidate.score:
                reasons = [name for name, arr, lim in (("risas", parts["laugh"], 0.5), ("gritos", parts["scream"], 0.5),
                                                       ("volumen", parts["volume"], 0.6), ("chat", parts["chat"], 0.5))
                           if arr[max(0, t_now - 3):t_now + 1].max() >= lim] or ["emoción"]
                self._candidate = Peak(t_now, v, reasons)
        c = self._candidate
        if c and (t_now - c.second >= 6 or v < th * 0.7):
            self._candidate = None
            hour_ago = c.second - 3600
            self.made = [m for m in self.made if m > hour_ago]
            if len(self.made) >= self.max_per_hour:
                return None
            self.last_peak = c.second
            self.made.append(c.second)
            return c
        return None

    def window(self, peak: Peak) -> tuple[float, float]:
        """Clip around a peak: build-up before, the payoff in the last third."""
        start = max(0.0, peak.second - 0.7 * self.clip_seconds)
        return start, start + self.clip_seconds


class LiveDetector:
    """Glues audio features, chat and the auto-clipper together."""

    def __init__(self, chat_at: Callable[[int], tuple[int, int, bool]], clipper: AutoClipper):
        self.audio = LiveAudio()
        self.chat_at = chat_at       # timeline second -> (messages, hype, chat connected)
        self.clipper = clipper
        self.scanned = 0
        self.last: dict[str, np.ndarray] = {}

    def series(self) -> dict[str, np.ndarray]:
        n = len(self.audio.loud)
        lo = max(0, n - 600)   # the last 10 minutes are enough for 5-minute baselines
        sig = signals(np.array(self.audio.loud[lo:], np.float32), np.array(self.audio.centroid[lo:], np.float32),
                      np.array(self.audio.periodic[lo:], np.float32), causal=True)
        chat = [self.chat_at(s) for s in range(lo, n)]
        rates = np.array([c[0] for c in chat], np.float32)
        hype = np.array([c[1] for c in chat], np.float32)
        conn = np.array([c[2] for c in chat], bool)
        parts = {k: np.array(sig[k], np.float32) for k in ("laugh", "scream", "volume")}
        parts["chat"] = chat_scores(rates, hype, conn)
        raw = combine(parts["laugh"], parts["scream"], parts["volume"], parts["chat"])
        # Causal 2 s average: a single noisy second doesn't fire a clip.
        parts["interest"] = np.convolve(raw, np.ones(2) / 2, mode="full")[:len(raw)]
        parts["rate"] = rates
        parts["offset"] = np.array([lo])
        self.last = parts
        return parts

    def feed(self, samples: np.ndarray) -> list[Peak]:
        n = self.audio.add(samples)
        if n <= self.scanned:
            return []
        parts = self.series()
        lo = int(parts["offset"][0])
        peaks = []
        for s in range(self.scanned, n):
            rel = {k: v for k, v in parts.items() if k not in ("offset",)}
            p = self.clipper.scan(s - lo, rel["interest"], rel)
            if p:
                peaks.append(Peak(p.second + lo, p.score, p.reasons))
        self.scanned = n
        return peaks
