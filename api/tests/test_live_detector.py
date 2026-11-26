import numpy as np

from app.live.detector import AutoClipper, LiveAudio, LiveDetector, chat_scores
from app.media.analysis import analyze
from tests.media_fixtures import LAUGH_AT, SCREAM_AT, synth_audio, write_wav


def feed_in_chunks(det, x, chunk=16000 * 10):
    peaks = []
    for i in range(0, len(x), chunk):
        peaks += det.feed(x[i:i + chunk])
    return peaks


def test_live_audio_matches_offline_features(tmp_path):
    x = synth_audio()
    live = LiveAudio()
    for i in range(0, len(x), 12345):          # odd chunk sizes, like real segments
        live.add(x[i:i + 12345])
    from app.media.analysis import second_features
    off = second_features(write_wav(tmp_path / "a.wav", x))
    n = len(live.loud)
    assert n >= 177
    assert np.allclose(live.loud, off["loud"][:n], atol=0.05)
    assert np.allclose(live.periodic[5:n - 5], off["periodic"][5:n - 5], atol=0.05)


def test_auto_clips_fire_on_audio_events_only():
    det = LiveDetector(lambda s: (0, 0, False), AutoClipper(clip_seconds=30))
    peaks = feed_in_chunks(det, synth_audio())
    secs = [p.second for p in peaks]
    assert len(peaks) == 2, secs
    assert SCREAM_AT[0] - 1 <= secs[0] <= SCREAM_AT[1] + 2 and "gritos" in peaks[0].reasons
    assert LAUGH_AT[0] - 1 <= secs[1] <= LAUGH_AT[1] + 2 and "risas" in peaks[1].reasons


def test_chat_surge_triggers_clip():
    """Quiet audio, but chat explodes at 100 s."""
    rng = np.random.default_rng(0)
    x = synth_audio()
    x[55 * 16000:65 * 16000] = x[40 * 16000:50 * 16000]      # remove the scream
    x[118 * 16000:126 * 16000] = x[40 * 16000:48 * 16000]    # and the laugh
    chat = {s: (int(rng.poisson(1)), 0, True) for s in range(200)}
    for s in range(100, 106):
        chat[s] = (12, 8, True)
    det = LiveDetector(lambda s: chat.get(s, (0, 0, True)), AutoClipper(clip_seconds=30))
    peaks = feed_in_chunks(det, x)
    assert [p.second for p in peaks if "chat" in p.reasons], peaks
    assert all(98 <= p.second <= 110 for p in peaks)


def test_chat_scores_relative_to_channel():
    # A busy chat (5 msg/s) isn't "hype" just for being busy.
    busy = np.full(400, 5.0)
    assert chat_scores(busy, np.zeros(400), np.ones(400, bool))[100:].max() < 0.1
    busy[350:356] = 25
    assert chat_scores(busy, np.zeros(400), np.ones(400, bool))[352:357].max() > 0.5


def test_cooldown_and_hourly_cap():
    c = AutoClipper(clip_seconds=30, cooldown=60, max_per_hour=2)
    interest = np.zeros(4000)
    parts = {k: np.zeros(4000) for k in ("laugh", "scream", "volume", "chat")}
    for s in (100, 130, 300, 600, 900):        # 130 is inside the cooldown
        interest[s:s + 3] = 0.9
    made = [p.second for t in range(4000) if (p := c.scan(t, interest, parts))]
    assert made == [100, 300]                  # cap of 2 per hour reached


def test_offline_analysis_unchanged(tmp_path):
    a = analyze(write_wav(tmp_path / "a.wav", synth_audio()))
    assert max(a["scream"][60:63]) > 0.5 and max(a["laugh"][120:124]) > 0.5
