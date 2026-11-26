"""AI clip selection: ask the model, then check its answer against reality.

The model proposes times; they are snapped to real sentence boundaries,
kept within the requested duration, and never overlap each other or clips
already made. If it proposes fewer good clips than asked, the non-AI
selection fills the rest, so the user always gets what they asked for.
"""
import numpy as np

from app.ai import AIError
from app.ai.captions import clean_tags, heuristic_caption
from app.ai.prompts import SYSTEM, format_transcript, selection_request
from app.ai.providers import AIResult, ask
from app.ai.schema import Selection
from app.media.moments import Moment, _overlaps, _text, audio_reasons, peak_time, select_moments


def _nearest(t: float, options: np.ndarray, limit: float) -> float | None:
    if len(options) == 0:
        return None
    i = int(np.argmin(np.abs(options - t)))
    return float(options[i]) if abs(options[i] - t) <= limit else None


def ai_select(provider: str, transcript: dict, analysis: dict, count: int, duration: float, total: float,
              exclude: list[tuple[float, float]], user_rules: str = "", hashtags: int = 5) -> tuple[list[Moment], AIResult]:
    result = ask(provider, SYSTEM, format_transcript(transcript, analysis),
                 selection_request(count, int(duration), total, exclude, user_rules, hashtags), Selection)
    segs = transcript.get("segments", [])
    starts = np.array([s["start"] for s in segs], np.float32)
    ends = np.array([s["end"] for s in segs], np.float32)
    taken = list(exclude)
    moments: list[Moment] = []
    for idea in sorted(result.data.clips, key=lambda c: -c.score):
        if len(moments) >= count:
            break
        a = _nearest(idea.start, starts, 2.5)
        b = _nearest(idea.end, ends, 2.5)
        if a is None or b is None:
            continue                      # times that don't exist in the transcript
        a, b = max(0.0, a), min(total, b)
        if b - a > 1.6 * duration:        # too long: end at the last sentence that fits
            fits = ends[(ends > a + 0.6 * duration) & (ends <= a + 1.4 * duration)]
            if len(fits) == 0:
                continue
            b = float(fits.max())
        if b - a < 0.5 * duration or _overlaps(a, b, taken, gap=0.5):
            continue
        cover = idea.cover_time - a if a <= idea.cover_time <= b else peak_time(analysis, a, b)
        moments.append(Moment(
            round(a, 2), round(b, 2), round(max(1, min(100, idea.score)) / 100, 3),
            ["IA"] + audio_reasons(analysis, transcript, a, b), _text(transcript, a, b),
            title=idea.title.strip()[:80], caption=idea.caption.strip()[:600],
            hashtags=clean_tags(idea.hashtags, hashtags), cover_t=round(cover, 2), ai=True))
        taken.append((a, b))
    return moments, result


def fill_captions(moments: list[Moment], hashtags: int = 5) -> None:
    for m in moments:
        if not m.title:
            m.title, m.caption, m.hashtags = heuristic_caption(m.text, hashtags)


def choose(provider: str | None, transcript: dict, analysis: dict, count: int, duration: float, total: float,
           exclude: list[tuple[float, float]], user_rules: str, hashtags: int,
           focus=None) -> tuple[list[Moment], AIResult | None, str | None]:
    """AI first (when enabled), the non-AI selection for anything missing.
    Returns (moments, ai result or None, failure code or None)."""
    moments: list[Moment] = []
    result, failure = None, None
    if provider and focus is None:
        try:
            moments, result = ai_select(provider, transcript, analysis, count, duration, total, exclude,
                                        user_rules, hashtags)
        except AIError as e:
            failure = e.code
    if len(moments) < count:
        more = select_moments(analysis, transcript, count - len(moments), duration, total,
                              exclude=list(exclude) + [(m.start, m.end) for m in moments], focus=focus)
        for m in more:
            m.cover_t = peak_time(analysis, m.start, m.end)
        moments += more
    fill_captions(moments, hashtags)
    moments.sort(key=lambda m: -m.score)
    return moments, result, failure
