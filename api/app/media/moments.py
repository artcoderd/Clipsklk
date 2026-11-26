"""Picks the best N clip windows from the audio signals + transcript.

Each clip is built around a peak of "interest" and placed so the peak lands in
the last third (the payoff), with edges snapped to sentence boundaries so the
clip starts and ends cleanly. Windows never overlap each other or the ranges
of clips already made ("more clips" excludes them).
"""
import re
from dataclasses import dataclass, field

import numpy as np

LAUGH_RE = re.compile(r"\b(?:ja(?:ja)+|je(?:je)+|ji(?:ji)+|ha(?:ha)+|he(?:he)+|lol|lmao|xd)\b|risa|laugh", re.I)
CUE_RE = re.compile(r"[\[(](?:risas?|laughs?|laughter|aplausos|applause|gritos?|screams?)[\])]", re.I)

W_LAUGH, W_SCREAM, W_VOLUME, W_EXCLAIM = 0.35, 0.25, 0.25, 0.15


@dataclass
class Moment:
    start: float
    end: float
    score: float
    reasons: list[str] = field(default_factory=list)
    text: str = ""
    title: str = ""
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    cover_t: float | None = None      # seconds from the clip start
    ai: bool = False


def _transcript_signals(transcript: dict, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    laugh = np.zeros(n, np.float32)
    exclaim = np.zeros(n, np.float32)
    speech = np.zeros(n, np.float32)
    for seg in transcript.get("segments", []):
        a, b = int(seg["start"]), min(n - 1, int(seg["end"]))
        if a >= n:
            continue
        if CUE_RE.search(seg["text"]):
            laugh[a:b + 1] = 1.0
        for w in seg.get("words") or []:
            i = min(n - 1, int(w["start"]))
            speech[i: min(n, int(w["end"]) + 1)] = 1.0
            if LAUGH_RE.search(w["word"]):
                laugh[max(0, i - 1): i + 2] = 1.0
            if "!" in w["word"] or "¡" in w["word"]:
                exclaim[max(0, i - 1): i + 2] = 1.0
    return laugh, exclaim, speech


def _boundaries(transcript: dict) -> tuple[np.ndarray, np.ndarray]:
    """Sentence-ish start/end times (segment edges)."""
    segs = transcript.get("segments", [])
    return (np.array([s["start"] for s in segs], np.float32),
            np.array([s["end"] for s in segs], np.float32))


def _snap(target: float, candidates: np.ndarray, lo: float, hi: float) -> float:
    ok = candidates[(candidates >= lo) & (candidates <= hi)]
    if len(ok) == 0:
        return target
    return float(ok[np.argmin(np.abs(ok - target))])


def _overlaps(a: float, b: float, ranges: list[tuple[float, float]], gap: float = 1.0) -> bool:
    return any(a < e + gap and b > s - gap for s, e in ranges)


def _text(transcript: dict, a: float, b: float) -> str:
    words = [w["word"] for seg in transcript.get("segments", []) if seg["end"] >= a and seg["start"] <= b
             for w in (seg.get("words") or []) if a <= w["start"] < b]
    return " ".join(words).strip()


def audio_reasons(analysis: dict, transcript: dict, a: float, b: float) -> list[str]:
    """Which non-AI signals fire inside [a, b] (for labels)."""
    n = max(1, int(np.ceil(b)) + 1)
    t_laugh, _, _ = _transcript_signals(transcript, n)

    def sig(name: str) -> np.ndarray:
        out = np.zeros(n, np.float32)
        v = np.asarray(analysis.get(name) or [], np.float32)[:n]
        out[: len(v)] = v
        return out

    lo, hi = int(a), max(int(a) + 1, int(b))
    found = []
    for label, arr, th in (("risas", np.maximum(sig("laugh"), t_laugh), 0.5),
                           ("gritos", sig("scream"), 0.5), ("volumen", sig("volume"), 0.6)):
        if float(arr[lo:hi].max(initial=0)) >= th:
            found.append(label)
    return found


def peak_time(analysis: dict, a: float, b: float) -> float:
    """Seconds from `a` of the strongest moment in [a, b] (for the cover frame)."""
    lo, hi = int(a), max(int(a) + 1, int(b))
    best, at = -1.0, (b - a) * 0.6
    for name in ("laugh", "scream", "volume"):
        arr = analysis.get(name) or []
        for s in range(lo, min(hi, len(arr))):
            if arr[s] > best:
                best, at = arr[s], s - a + 0.5
    return float(min(max(0.0, at), max(0.0, b - a - 0.1)))


def select_moments(analysis: dict, transcript: dict, count: int, clip_seconds: float, total: float,
                   exclude: list[tuple[float, float]] | None = None,
                   focus: np.ndarray | None = None) -> list[Moment]:
    """`focus` (optional, per second 0..1): where the chosen person is the one
    talking. Only windows where they speak most of the time are kept."""
    exclude = list(exclude or [])
    n = max(1, int(np.ceil(total)))
    if focus is not None:
        f = np.zeros(n, np.float32)
        f[: min(n, len(focus))] = np.asarray(focus, np.float32)[:n]
        focus = f
    if total <= clip_seconds * 1.1 and focus is None:
        if _overlaps(0, total, exclude):
            return []
        return [Moment(0.0, round(total, 2), 0.5, ["video completo"], _text(transcript, 0, total))]

    def sig(name: str) -> np.ndarray:
        a = np.zeros(n, np.float32)
        v = np.asarray(analysis.get(name, []), np.float32)[:n]
        a[: len(v)] = v
        return a

    t_laugh, t_excl, speech = _transcript_signals(transcript, n)
    laugh = np.maximum(sig("laugh"), t_laugh)
    scream, volume = sig("scream"), sig("volume")
    silence = sig("silence")
    raw = W_LAUGH * laugh + W_SCREAM * scream + W_VOLUME * volume + W_EXCLAIM * t_excl
    if focus is not None:
        # Peaks only count where the chosen person is talking; a little base
        # interest keeps calm stretches of them talking eligible.
        raw = (raw + 0.05) * focus
    smooth = np.convolve(raw, np.ones(3) / 3, mode="same")
    starts, ends = _boundaries(transcript)

    chosen: list[Moment] = []
    taken = list(exclude)
    # Visit seconds from most to least interesting (ties: earlier first).
    order = np.lexsort((np.arange(n), -smooth))
    for peak in order:
        if len(chosen) >= count:
            break
        peak = float(peak)
        if _overlaps(peak, peak + 1, taken, gap=0):
            continue
        # Free gap around the peak (between clips already taken).
        lo = max([e + 1.0 for _, e in taken if e <= peak] + [0.0])
        hi = min([st - 1.0 for st, _ in taken if st >= peak + 1] + [total])
        if hi - lo < 0.75 * clip_seconds:
            continue
        end = min(hi, peak + max(3.0, 0.3 * clip_seconds))
        start = max(lo, end - clip_seconds)
        end = min(hi, start + clip_seconds)
        # Clean edges: start at a sentence start, end at a sentence end, keeping the peak inside.
        start = _snap(start, starts, max(lo, start - 0.15 * clip_seconds), min(peak - 1, start + 0.15 * clip_seconds))
        end = _snap(end, ends, max(peak + 1, end - 0.1 * clip_seconds), min(hi, end + 0.15 * clip_seconds))
        length = end - start
        if not (0.75 * clip_seconds <= length <= 1.2 * clip_seconds) or _overlaps(start, end, taken, gap=0):
            continue
        a, b = int(start), max(int(start) + 1, int(end))
        if focus is not None:
            talking = speech[a:b] > 0
            share = float(focus[a:b][talking].mean()) if talking.any() else 0.0
            if share < 0.6:
                continue
        score = (0.6 * float(raw[a:b].max()) + 0.4 * float(smooth[a:b].mean())
                 + 0.1 * float(speech[a:b].mean()) - 0.3 * float(silence[a:b].mean()))
        reasons = [label for label, arr, th in (("risas", laugh, 0.5), ("gritos", scream, 0.5),
                                                ("volumen", volume, 0.6), ("emoción", t_excl, 0.5))
                   if arr[a:b].max() >= th] or ["conversación"]
        if focus is not None:
            reasons.append("persona")
        chosen.append(Moment(round(start, 2), round(end, 2), round(max(0.0, score), 3), reasons,
                             _text(transcript, start, end)))
        taken.append((start, end))
    chosen.sort(key=lambda m: m.score, reverse=True)
    return chosen
