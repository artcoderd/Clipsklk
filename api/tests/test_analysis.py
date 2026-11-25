import numpy as np

from app.media.analysis import analyze
from app.media.moments import select_moments
from tests.media_fixtures import LAUGH_AT, SCREAM_AT, fake_transcript, synth_audio, write_wav


def _argmax_in(arr, lo, hi):
    return lo + int(np.argmax(arr[lo:hi]))


def test_detects_scream_and_laugh(tmp_path):
    a = analyze(write_wav(tmp_path / "a.wav", synth_audio()))
    assert a["seconds"] == 180
    scream, laugh, volume = (np.array(a[k]) for k in ("scream", "laugh", "volume"))

    s0, s1 = int(SCREAM_AT[0]), int(SCREAM_AT[1])
    l0, l1 = int(LAUGH_AT[0]), int(LAUGH_AT[1])
    assert scream[s0:s1].max() > 0.5
    assert volume[s0:s1].max() > 0.8
    assert laugh[l0:l1].max() > 0.5
    # Quiet elsewhere: no false screams/laughs in normal conversation.
    calm = np.ones(180, bool)
    calm[s0 - 2:s1 + 2] = calm[l0 - 2:l1 + 2] = False
    assert scream[calm].max() < 0.2
    assert laugh[calm].max() < 0.35
    # The scream is not mistaken for laughter.
    assert laugh[s0:s1].max() < 0.35


def test_long_audio_is_streamed(tmp_path):
    # 20 minutes of audio analyses fine and quickly (chunks, not one big array).
    a = analyze(write_wav(tmp_path / "long.wav", np.tile(synth_audio(), 7)[: 16000 * 1200]))
    assert a["seconds"] == 1200


def test_select_moments_finds_events_without_overlap(tmp_path):
    a = analyze(write_wav(tmp_path / "a.wav", synth_audio()))
    moments = select_moments(a, fake_transcript(), count=4, clip_seconds=30, total=180.0)
    assert len(moments) == 4
    spans = sorted((m.start, m.end) for m in moments)
    for (_, e1), (s2, _) in zip(spans, spans[1:]):
        assert s2 >= e1  # no overlap
    for m in moments:
        assert 0 <= m.start < m.end <= 180
        assert 22.5 <= m.end - m.start <= 36  # within tolerance of 30 s
    top_two = moments[:2]
    covered = lambda lo, hi: any(m.start <= lo and m.end >= hi for m in top_two)  # noqa: E731
    assert covered(*SCREAM_AT) and covered(*LAUGH_AT)
    reasons = {r for m in top_two for r in m.reasons}
    assert {"gritos", "risas"} <= reasons
    # Peaks land in the second half of the clip (payoff near the end).
    scream_clip = next(m for m in top_two if "gritos" in m.reasons)
    assert (SCREAM_AT[0] - scream_clip.start) / (scream_clip.end - scream_clip.start) > 0.5
    # Edges snap to sentence boundaries (segments start every 5 s).
    assert all(m.start % 5 == 0 for m in top_two)


def test_select_moments_respects_exclusions(tmp_path):
    a = analyze(write_wav(tmp_path / "a.wav", synth_audio()))
    first = select_moments(a, fake_transcript(), 2, 30, 180.0)
    more = select_moments(a, fake_transcript(), 2, 30, 180.0, exclude=[(m.start, m.end) for m in first])
    for m in more:
        assert all(m.end <= f.start or m.start >= f.end for f in first)


def test_short_video_returns_whole_thing():
    moments = select_moments({"seconds": 20}, {"segments": []}, 5, 45, 20.0)
    assert len(moments) == 1 and moments[0].end == 20.0


def test_baseline_ignores_digital_silence_and_short_files(tmp_path):
    """Regression: on a 3-min file with pure-silence pauses, edge padding used to
    drag the 5-min baseline to -100 dB, flagging all normal speech as screams."""
    x = synth_audio()
    sr = 16000
    for s in range(0, 180, 7):              # 1.5 s of digital silence every 7 s
        x[s * sr: int((s + 1.5) * sr)] = 0.0
    x[:2 * sr] = 0.0
    x[-2 * sr:] = 0.0
    a = analyze(write_wav(tmp_path / "gaps.wav", x))
    scream, volume = np.array(a["scream"]), np.array(a["volume"])
    calm = np.ones(180, bool)
    calm[int(SCREAM_AT[0]) - 2:int(SCREAM_AT[1]) + 2] = False
    calm[int(LAUGH_AT[0]) - 2:int(LAUGH_AT[1]) + 2] = False
    assert scream[calm].max() < 0.2 and volume[calm].max() < 0.3
    assert scream[int(SCREAM_AT[0]):int(SCREAM_AT[1])].max() > 0.5


def test_laugh_at_speech_volume_is_detected(tmp_path):
    from tests.media_fixtures import _voice
    sr = 16000
    x = synth_audio()
    t = np.arange(4 * sr) / sr
    pulses = (np.sin(2 * np.pi * 5 * t) > 0.2).astype(np.float32)
    # Same level as the surrounding conversation (0.05 * voice).
    x[int(LAUGH_AT[0]) * sr: int(LAUGH_AT[1]) * sr] = 0.05 * pulses * _voice(t, 260, 4000)
    a = analyze(write_wav(tmp_path / "soft.wav", x))
    assert np.array(a["laugh"])[int(LAUGH_AT[0]):int(LAUGH_AT[1])].max() > 0.5
