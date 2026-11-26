import subprocess
import wave

import numpy as np
import pytest

from app.media import ffmpeg as F
from app.media.render import (OUTPUT_SIZES, RenderSettings, choose_layout, crop_rect, plan_follow,
                              render_clip)
from app.media.subtitles import PRESETS, build_ass, chunk_words, clip_words
from app.media.vision import Face, _cluster, _Track, speaker_track


def face(p, cx, act=0.0, cy=0.4, w=0.1, h=0.18):
    return Face(p, cx, cy, w, h, act)


def two_people(seconds=20, fps=4, turns=((0, 10, 0), (10, 20, 1))):
    out = []
    for i in range(seconds * fps):
        t = i / fps
        talking = next(p for a, b, p in turns if a <= t < b)
        out.append((t, [face(0, 0.25, 0.3 if talking == 0 else 0.02),
                        face(1, 0.75, 0.3 if talking == 1 else 0.02)]))
    return out


# --- speaker detection ------------------------------------------------------

def test_speaker_track_follows_turns_with_hysteresis():
    s = two_people()
    who = speaker_track(s, lambda t: True)
    assert all(p == 0 for (t, _), p in zip(s, who) if 1 <= t < 10)
    assert all(p == 1 for (t, _), p in zip(s, who) if 12 <= t < 20)
    # A short burst from the listener doesn't steal the camera.
    blip = [(t, [face(0, 0.25, 0.3), face(1, 0.75, 0.6 if 5 <= t < 5.25 else 0.02)]) for t, _ in s[:40]]
    assert set(speaker_track(blip, lambda t: True)) == {0}


def test_single_visible_person_is_the_speaker():
    s = [(i / 4, [face(3, 0.5, 0.0)]) for i in range(20)]
    assert set(speaker_track(s, lambda t: True)) == {3}


def test_cluster_groups_tracks_by_face():
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=128), rng.normal(size=128)
    tracks = {}
    for i, base in enumerate([a, b, a, b, a]):   # the same two people across 5 shots (camera cuts)
        tr = _Track(i, (0, 0, 10, 10), 0)
        tr.samples = 40
        tr.embeds = [base + 0.2 * rng.normal(size=128) for _ in range(5)]
        tr.best = (100.0 + i, float(i), (0.1, 0.1, 0.2, 0.2))
        tracks[i] = tr
    blip = _Track(9, (0, 0, 5, 5), 0)
    blip.samples, blip.embeds = 2, [rng.normal(size=128)]   # 0.5 s false positive
    tracks[9] = blip
    people, tp = _cluster(tracks, fps=4)
    assert len(people) == 2
    assert tp[0] == tp[2] == tp[4] != tp[1] == tp[3]
    assert 9 not in tp
    assert people[0]["seconds"] == 30.0   # 3 tracks x 40 samples / 4 fps, most visible first


# --- camera planning ----------------------------------------------------------

def test_crop_rect_stays_inside_and_keeps_aspect():
    for cx in (0.0, 0.3, 1.0):
        x, y, w, h = crop_rect(1920, 1080, 9 / 16, cx, 0.4, 1.0)
        assert 0 <= x and x + w <= 1920 and 0 <= y and y + h <= 1080
        assert abs(w / h - 9 / 16) < 0.01 and h == 1080
    x, y, w, h = crop_rect(1920, 1080, 9 / 16, 0.5, 0.4, 1.3)
    assert h == round(1080 / 1.3)
    # Landscape output from a landscape source: full width.
    assert crop_rect(1920, 1080, 16 / 9, 0.5, 0.5, 1.0) == (0, 0, 1920, 1080)


@pytest.mark.parametrize("framing", ["fixed", "smooth", "dynamic", "zoom"])
def test_plan_follow_modes(framing):
    s = two_people()
    who = speaker_track(s, lambda t: True)
    p = plan_follow(s, who, 0, 20, 30, framing)
    assert len(p.cx) == 600
    first, second = p.cx[100:280], p.cx[420:590]
    if framing == "fixed":
        assert np.ptp(p.cx) == 0
    else:
        assert abs(first.mean() - 0.25) < 0.03 and abs(second.mean() - 0.75) < 0.03
    jump = np.abs(np.diff(p.cx)).max()
    if framing in ("dynamic", "zoom"):
        assert jump > 0.4             # hard cut to the new speaker
    elif framing == "smooth":
        assert jump < 0.05            # glides
    if framing == "zoom":
        assert set(np.unique(p.zoom)) == {1.0, 1.3}
    else:
        assert np.all(p.zoom == 1.0)


def test_choose_layout():
    s = two_people()
    assert choose_layout(s, 1920, 1080, 9 / 16, True) == ("split", [0, 1])
    close = [(t, [face(0, 0.45), face(1, 0.55)]) for t, _ in s]
    assert choose_layout(close, 1920, 1080, 9 / 16, True)[0] == "follow"
    one = [(t, [face(0, 0.5)]) for t, _ in s]
    assert choose_layout(one, 1920, 1080, 9 / 16, True) == ("follow", [0])
    assert choose_layout([(0.0, [])] * 10, 1920, 1080, 9 / 16, True)[0] == "fit"
    assert choose_layout([], 0, 0, 9 / 16, False)[0] == "audio"
    # 16:9 output: both people fit in the full frame.
    assert choose_layout(s, 1920, 1080, 16 / 9, True)[0] == "follow"


# --- subtitles ------------------------------------------------------------------

def _words(n, step=0.4):
    return [{"start": i * step, "end": i * step + 0.3, "word": f"palabra{i}"} for i in range(n)]


def test_chunking_rules():
    ws = _words(7)
    ws[3]["word"] = "fin."
    ws[5]["start"] += 1.0   # a pause
    ws[5]["end"] += 1.0
    chunks = chunk_words(ws, 3)
    assert [len(c) for c in chunks] == [3, 1, 1, 2]


def test_ass_karaoke(tmp_path):
    ws = _words(6)
    ws[1]["word"] = "{hack}\\"
    path = build_ass(ws, PRESETS["clasico"], OUTPUT_SIZES["9:16"], "9:16", tmp_path / "s.ass")
    text = path.read_text()
    events = [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]
    assert len(events) == 6                       # one per word (highlight moves)
    assert "\\c&H0000E6FF" in events[0]           # yellow #FFE600 in ASS BGR
    assert "\\fscx115" in events[0]               # pop animation
    assert "PALABRA0" in events[0]                # uppercase
    assert "{HACK}" not in text and "(HACK)" in text   # user text can't inject ASS tags
    style = next(ln for ln in text.splitlines() if ln.startswith("Style:"))
    assert style.split(",")[1] == "Montserrat Black"
    assert int(style.split(",")[-2]) == int(1920 * 0.27)   # clear of TikTok/Reels UI


def test_ass_static_and_positions(tmp_path):
    minimal = build_ass(_words(6), PRESETS["minimal"], (1080, 1080), "1:1", tmp_path / "m.ass").read_text()
    assert len([ln for ln in minimal.splitlines() if ln.startswith("Dialogue:")]) == 2   # 5 + 1 words
    top = build_ass(_words(2), {**PRESETS["neon"], "position": "top", "margin_top": 700},
                    (1080, 1920), "9:16", tmp_path / "t.ass").read_text()
    style = next(ln for ln in top.splitlines() if ln.startswith("Style:")).split(",")
    assert style[-5] == "8" and style[-2] == "700"


def test_clip_words_relative():
    tr = {"segments": [{"start": 9, "end": 12, "words": [{"start": 9.5, "end": 9.8, "word": " hola"},
                                                       {"start": 10.2, "end": 10.6, "word": " mundo"},
                                                       {"start": 30, "end": 31, "word": "fuera"}]}]}
    ws = clip_words(tr, 10, 20)
    assert [w["word"] for w in ws] == [" mundo"] and abs(ws[0]["start"] - 0.2) < 1e-6


# --- real renders -----------------------------------------------------------------

def _probe_size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return tuple(int(v) for v in out.stdout.strip().split(","))


@pytest.fixture(scope="module")
def small_video(tmp_path_factory):
    p = tmp_path_factory.mktemp("r") / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=6",
                    "-f", "lavfi", "-i", "sine=f=220:d=6", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(p)], check=True)
    return p


@pytest.mark.parametrize("fmt", list(OUTPUT_SIZES))
def test_render_formats(small_video, tmp_path, fmt):
    meta = F.probe(small_video)
    tr = {"segments": [{"start": 0, "end": 6, "words": _words(12)}]}
    out = tmp_path / "o.mp4"
    layout = render_clip(small_video, 1, 4, out, RenderSettings(format=fmt), meta, {"samples": []}, tr)
    assert layout == "fit"   # no faces
    assert _probe_size(out) == OUTPUT_SIZES[fmt]
    assert abs(F.probe(out)["duration"] - 3.0) < 0.15


def test_music_ducks_under_speech(tmp_path):
    """Voice (220 Hz) for the first 3 s, then silence; music at 1 kHz. The
    music must be quieter while the voice is present."""
    sr = 48000
    t = np.arange(6 * sr) / sr
    voice = np.where(t < 3, 0.5 * np.sin(2 * np.pi * 220 * t), 0.0)
    src = tmp_path / "voice.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((voice * 32767).astype(np.int16).tobytes())
    music = tmp_path / "music.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=1000:d=10", str(music)], check=True)
    out = tmp_path / "o.mp4"
    render_clip(src, 0, 6, out, RenderSettings(subtitles=False, music_path=music, music_volume=0.5),
                F.probe(src), None, None)
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(pcm, np.int16).astype(np.float32)

    def music_level(lo, hi):
        seg = a[int(lo * 8000):int(hi * 8000)]
        spec = np.abs(np.fft.rfft(seg))
        f = np.fft.rfftfreq(len(seg), 1 / 8000)
        return spec[(f > 950) & (f < 1050)].sum()

    assert music_level(1.0, 2.5) < 0.5 * music_level(4.0, 5.5)


def test_person_focus_keeps_camera_on_them():
    from app.media.render import Composer
    s = two_people()   # person 0 talks first, then person 1
    who = speaker_track(s, lambda t: True)
    comp = Composer(RenderSettings(person=0), 1920, 1080, 30, 0, 20, s, who, True)
    assert comp.layout == "follow"
    assert np.all(np.abs(comp.path.cx - 0.25) < 0.03)   # even while person 1 talks
