import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.media import ffmpeg as F
from app.media.edit import EditPlan, ImageItem, SoundItem, TextItem, normalize_ranges
from app.media.render import RenderSettings, render_clip


def test_ranges_and_mapping():
    assert normalize_ranges([[5, 6], [1, 2], [1.5, 3], [9, 20]], 0, 10) == [(1, 3), (5, 6), (9, 10)]
    p = EditPlan(start=10, end=20, cuts=[(12, 13), (15, 17)])
    assert p.keeps == [(10, 12), (13, 15), (17, 20)] and p.total == 7
    assert p.out_t(10) == 0 and p.out_t(12.5) == 2 and p.out_t(14) == 3 and p.out_t(18) == 5
    assert not p.kept(16) and p.kept(19)
    words = [{"start": 11, "end": 11.4, "word": "a"}, {"start": 12.2, "end": 12.6, "word": "cut"},
             {"start": 17.5, "end": 18, "word": "b"}]
    assert [(w["word"], w["start"]) for w in p.remap_words(words)] == [("a", 1), ("b", 4.5)]
    assert p.out_range(15.5, 16.5) is None                       # entirely inside a cut


@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    """6 s video; second N has a 300+100*N Hz tone, so we can tell which seconds survive."""
    p = tmp_path_factory.mktemp("e") / "tones.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=6",
                    "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*(300+100*floor(t))*t)':s=48000:d=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(p)], check=True)
    return p


def dominant_tones(path: Path, step=0.5):
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(pcm, np.int16).astype(np.float32)
    out = []
    n = int(step * 8000)
    for i in range(0, len(a) - n + 1, n):
        seg = a[i:i + n] * np.hanning(n)
        f = np.fft.rfftfreq(n, 1 / 8000)
        spec = np.abs(np.fft.rfft(seg))
        spec[f < 250] = 0
        out.append(int(round(f[np.argmax(spec)] / 100) * 100))
    return out


def probe_frames(path):
    import json
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames,width,height", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    st = json.loads(out)["streams"][0]
    return int(st["nb_read_frames"]), int(st["width"]), int(st["height"])


def test_trim_and_cuts_in_video_and_audio(tones, tmp_path):
    meta = F.probe(tones)
    out = tmp_path / "o.mp4"
    plan = EditPlan(start=1, end=5, cuts=[(2, 3)])
    render_clip(tones, 0, 6, out, RenderSettings(subtitles=False, layout="fit"), meta, None, None, edit=plan)
    dur = F.probe(out)["duration"]
    assert abs(dur - 3.0) < 0.1
    # Seconds 1, 3, 4 remain (400, 600, 700 Hz); second 2 (500 Hz) is gone.
    tones_heard = dominant_tones(out)
    assert tones_heard[:6] == [400, 400, 600, 600, 700, 700], tones_heard
    w, h = probe_frames(out)[1:]
    frames = probe_frames(out)[0]
    assert (w, h) == (1080, 1920) and abs(frames - 75) <= 2   # 3 s * 25 fps: video in sync with audio


def test_texts_images_sounds_and_music(tones, tmp_path):
    import cv2
    meta = F.probe(tones)
    logo = tmp_path / "logo.png"
    img = np.zeros((100, 100, 4), np.uint8)
    img[:, :, 2] = 255          # pure red, opaque
    img[:, :, 3] = 255
    cv2.imwrite(str(logo), img)
    beep = tmp_path / "beep.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=2500:d=0.5", str(beep)], check=True)
    plan = EditPlan(start=0, end=4, cuts=[(1, 2)],
                    texts=[TextItem("HOLA", 0, 4, 0.5, 0.2, 120)],
                    images=[ImageItem(logo, 2, 4, 0.5, 0.8, 0.2)],
                    sounds=[SoundItem(beep, 2.5, 1.0)])
    out = tmp_path / "o.mp4"
    render_clip(tones, 0, 6, out, RenderSettings(subtitles=False, layout="fit"), meta, None, None, edit=plan)
    # Image shows from source t=2 (output 1.0 s); not before.
    def frame_at(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3)
    late, early = frame_at(2.0), frame_at(0.3)
    px = late[int(0.8 * 1920), 540]
    assert px[2] > 200 and px[1] < 60                       # red logo drawn
    assert early[int(0.8 * 1920), 540][2] < 200 or early[int(0.8 * 1920), 540][1] > 60
    # Text "HOLA" (white with outline) near the top centre.
    band = late[int(0.2 * 1920) - 60:int(0.2 * 1920) + 60, 300:780]
    assert (band.min(axis=2) > 230).sum() > 2000
    # The beep lands at output 1.5 s (source 2.5 minus the cut second).
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(pcm, np.int16).astype(np.float32)

    def beep_energy(t0, t1):
        seg = a[int(t0 * 8000):int(t1 * 8000)]
        f = np.fft.rfftfreq(len(seg), 1 / 8000)
        spec = np.abs(np.fft.rfft(seg))
        return spec[(f > 2450) & (f < 2550)].sum() / len(seg)

    assert beep_energy(1.55, 1.95) > 20 * beep_energy(0.4, 1.4)


def test_preview_copy_is_small_and_fast(tones, tmp_path):
    meta = F.probe(tones)
    out = tmp_path / "p.mp4"
    render_clip(tones, 0, 3, out, RenderSettings(subtitles=False, scale=0.5), meta, None, None)
    assert probe_frames(out)[1:] == (540, 960)
