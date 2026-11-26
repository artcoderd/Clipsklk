"""Vertical (or square/landscape) clip renderer.

ffmpeg decodes the clip range -> each frame is composed in Python (crop that
follows the speaker, split screen, gameplay, blurred background) -> a second
ffmpeg burns the karaoke subtitles (libass), mixes music with ducking,
normalises loudness and encodes (NVENC or x264).

Layouts
  follow    one crop that follows whoever is talking
  split     2 or 3 people stacked (or side by side for 16:9)
  gameplay  streamer camera on top, game below (manual rectangles)
  fit       whole video over a blurred copy of itself (+ logo)
  auto      picks one of the above from what's on screen
Framing modes (follow): fixed, smooth, dynamic, zoom (jump cuts with zoom).
"""
import logging
import math
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.media import PipelineError
from app.media.ffmpeg import _encoder_args, video_encoder
from app.media.subtitles import build_ass, clip_words
from app.media.vision import Face, faces_between, iter_frames, speaker_track

log = logging.getLogger(__name__)

OUTPUT_SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080), "16:9": (1920, 1080)}
FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


@dataclass
class RenderSettings:
    format: str = "9:16"
    layout: str = "auto"
    framing: str = "smooth"
    subtitles: bool = True
    style: dict | None = None
    music_path: Path | None = None
    music_volume: float = 0.15
    logo_path: Path | None = None
    logo_position: str = "top-right"
    gameplay: dict | None = None       # {"cam": [x,y,w,h], "game": [x,y,w,h]} normalised
    person: int | None = None           # keep the camera on this person (follow)
    scale: float = 1.0                  # <1: editor preview copy (smaller, faster)


# ---------------------------------------------------------------------------
# Camera planning (pure functions; unit-tested without video)
# ---------------------------------------------------------------------------

@dataclass
class CameraPath:
    cx: np.ndarray     # per output frame, normalised centre of the crop
    cy: np.ndarray
    zoom: np.ndarray   # 1.0 = largest crop that fits the source


def _ema(values: np.ndarray, dt: float, tau: float, cuts: set[int] | None = None) -> np.ndarray:
    out = np.empty_like(values)
    a = 1 - math.exp(-dt / tau) if tau > 0 else 1.0
    x = values[0]
    for i, v in enumerate(values):
        x = v if (cuts and i in cuts) else x + a * (v - x)
        out[i] = x
    return out


def _deadzone(values: np.ndarray, dz: float, cuts: set[int] | None = None) -> np.ndarray:
    out = np.empty_like(values)
    held = values[0]
    for i, v in enumerate(values):
        if (cuts and i in cuts) or abs(v - held) > dz:
            held = v
        out[i] = held
    return out


def plan_follow(samples: list[tuple[float, list[Face]]], who: list[int | None], start: float, end: float,
                fps: float, framing: str, person: int | None = None) -> CameraPath:
    n = max(1, int(round((end - start) * fps)))
    times = start + np.arange(n) / fps
    # Target per sample: the face of whoever is talking (or the chosen person).
    st, sx, sy, sp = [], [], [], []
    last = (0.5, 0.42, None)
    for (t, faces), p in zip(samples, who):
        target_p = person if person is not None and any(f.person == person for f in faces) else p
        face = next((f for f in faces if f.person == target_p and target_p is not None), None)
        if face is None and faces and last[2] is None:
            face = max(faces, key=lambda f: f.w * f.h)
        if face is not None:
            last = (face.cx, face.cy, face.person)
        st.append(t)
        sx.append(last[0])
        sy.append(last[1])
        sp.append(last[2])
    if not st:
        return CameraPath(np.full(n, 0.5), np.full(n, 0.42), np.ones(n))
    # Hold the value of the latest sample at each frame (step function).
    idx = np.clip(np.searchsorted(np.array(st), times, side="right") - 1, 0, len(st) - 1)
    tx, ty = np.array(sx)[idx], np.array(sy)[idx]
    tp = [sp[i] for i in idx]
    cuts = {i for i in range(1, n) if tp[i] != tp[i - 1]}   # speaker changes
    dt = 1 / fps
    zoom = np.ones(n)

    if framing == "fixed":
        # One position for the whole clip: where the main speaker sits.
        main = max(set(tp), key=tp.count)
        sel = [i for i in range(n) if tp[i] == main] or list(range(n))
        cx = np.full(n, float(np.median(tx[sel])))
        cy = np.full(n, float(np.median(ty[sel])))
    elif framing == "dynamic":
        cx = _ema(_deadzone(tx, 0.02, cuts), dt, 0.35, cuts)
        cy = _ema(_deadzone(ty, 0.02, cuts), dt, 0.35, cuts)
    elif framing == "zoom":
        cx, cy = _deadzone(tx, 0.03, cuts), _deadzone(ty, 0.03, cuts)
        # Punch in/out on every speaker change and every 4 s within a long turn.
        level, since = 1.0, 0.0
        for i in range(n):
            since += dt
            if i in cuts or since >= 4.0:
                level = 1.3 if level == 1.0 else 1.0
                since = 0.0
            zoom[i] = level
    else:  # smooth
        cx = _ema(_deadzone(tx, 0.05), dt, 0.9)
        cy = _ema(_deadzone(ty, 0.05), dt, 0.9)
    return CameraPath(cx, cy, zoom)


def crop_rect(src_w: int, src_h: int, aspect: float, cx: float, cy: float, zoom: float,
              face_y: float = 0.40) -> tuple[int, int, int, int]:
    """Largest crop of `aspect` (w/h) inside the source, zoomed, centred on the
    face horizontally and with the face at `face_y` of the crop height."""
    ch = src_h / zoom
    cw = ch * aspect
    if cw > src_w / zoom:
        cw = src_w / zoom
        ch = cw / aspect
    x = min(max(cx * src_w - cw / 2, 0), src_w - cw)
    y = min(max(cy * src_h - face_y * ch, 0), src_h - ch)
    return int(round(x)), int(round(y)), max(2, int(round(cw))), max(2, int(round(ch)))


def choose_layout(samples: list[tuple[float, list[Face]]], src_w: int, src_h: int, aspect: float,
                  has_video: bool) -> tuple[str, list[int]]:
    """auto: who is on screen most of the clip decides the layout."""
    if not has_video:
        return "audio", []
    if not samples:
        return "fit", []
    seen: dict[int, list[float]] = {}
    for _, faces in samples:
        for f in faces:
            if f.person is not None:
                seen.setdefault(f.person, []).append(f.cx)
    present = [p for p, xs in seen.items() if len(xs) >= 0.5 * len(samples)]
    if not present:
        return ("follow", []) if seen else ("fit", [])
    if len(present) == 1:
        return "follow", present
    present.sort(key=lambda p: float(np.median(seen[p])))   # left to right
    xs = [float(np.median(seen[p])) for p in present]
    crop_w = min(1.0, src_h * aspect / src_w)
    if max(xs) - min(xs) + 0.1 < crop_w * 0.9:
        return "follow", present            # everyone fits in one vertical crop
    return "split", present[:3]


# ---------------------------------------------------------------------------
# Frame composition
# ---------------------------------------------------------------------------

def _cover(img: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2

    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    nw, nh = max(w, int(round(iw * s))), max(h, int(round(ih * s)))
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    x, y = (nw - w) // 2, (nh - h) // 2
    return r[y:y + h, x:x + w]


def _blurred_bg(img: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2

    small = _cover(img, max(8, w // 12), max(8, h // 12))
    small = cv2.GaussianBlur(small, (0, 0), 3)
    bg = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return (bg * 0.55).astype(np.uint8)


def _crop_to(img: np.ndarray, rect: tuple[int, int, int, int], w: int, h: int) -> np.ndarray:
    import cv2

    x, y, cw, ch = rect
    crop = img[y:y + ch, x:x + cw]
    interp = cv2.INTER_AREA if cw > w else cv2.INTER_LINEAR
    return cv2.resize(crop, (w, h), interpolation=interp)


def _rect_px(r: list[float], W: int, H: int, aspect: float) -> tuple[int, int, int, int]:
    """A normalised rectangle, adjusted (centred) to `aspect` and kept in frame."""
    x, y, w, h = r[0] * W, r[1] * H, r[2] * W, r[3] * H
    if w / max(h, 1) > aspect:
        nw = h * aspect
        x += (w - nw) / 2
        w = nw
    else:
        nh = w / aspect
        y += (h - nh) / 2
        h = nh
    w, h = max(2, min(w, W)), max(2, min(h, H))
    x, y = min(max(x, 0), W - w), min(max(y, 0), H - h)
    return int(x), int(y), int(w), int(h)


class Composer:
    def __init__(self, settings: RenderSettings, src_w: int, src_h: int, fps: float, start: float, end: float,
                 samples: list[tuple[float, list[Face]]], who: list[int | None], has_video: bool):
        self.s = settings
        self.W, self.H = src_w, src_h
        bw, bh = OUTPUT_SIZES.get(settings.format, OUTPUT_SIZES["9:16"])
        self.ow, self.oh = int(bw * settings.scale) // 2 * 2, int(bh * settings.scale) // 2 * 2
        self.aspect = self.ow / self.oh
        self.fps, self.start = fps, start
        layout = settings.layout
        people: list[int] = []
        if layout == "auto" and settings.person is not None:
            layout = "follow"   # clips of one person: the camera stays on them
        if layout == "auto" or (layout == "split" and has_video):
            auto, people = choose_layout(samples, src_w, src_h, self.aspect, has_video)
            layout = auto if layout == "auto" else ("split" if len(people) >= 2 else auto)
        if not has_video:
            layout = "audio"
        self.layout = layout
        self.logo = self._load_logo()

        if layout == "follow":
            self.path = plan_follow(samples, who, start, end, fps, settings.framing, settings.person)
        elif layout == "split":
            self._plan_split(samples, people, start, end)
        elif layout == "gameplay":
            self._plan_gameplay(samples)

    # -- layouts ---------------------------------------------------------
    def _plan_split(self, samples, people, start, end):
        n = len(people)
        portrait = self.oh >= self.ow
        self.panel = (self.ow, self.oh // n) if portrait else (self.ow // n, self.oh)
        self.panels = []
        for p in people:
            only = [(t, [f for f in fs if f.person == p]) for t, fs in samples]
            path = plan_follow(only, [p] * len(only), start, end, self.fps, "smooth")
            sizes = [f.h for _, fs in only for f in fs]
            face_h = float(np.median(sizes)) if sizes else 0.3
            # Head and shoulders: crop about 3x the face height.
            pw, ph = self.panel
            # Crop height at zoom 1 vs. the wanted ~3 face heights (head and shoulders).
            full_h = min(self.H, self.W * ph / pw)
            zoom = max(1.0, min(4.0, full_h / (3.0 * face_h * self.H)))
            self.panels.append((path, zoom))
        self.n_panels = n

    def _plan_gameplay(self, samples):
        g = self.s.gameplay or {}
        portrait = self.oh >= self.ow
        self.cam_h = int(self.oh * 0.38) if portrait else self.oh // 3
        cam_size = (self.ow, self.cam_h) if portrait else (int(self.ow * 0.28), self.cam_h)
        game_size = (self.ow, self.oh - self.cam_h) if portrait else (self.ow, self.oh)
        cam = g.get("cam")
        if not cam:
            faces = [f for _, fs in samples for f in fs]
            if faces:
                f = max(faces, key=lambda f: f.w * f.h)
                side = 3.2 * f.h
                cam = [f.cx - side * 0.9, f.cy - side / 2, side * 1.8, side]
            else:
                cam = [0.0, 0.0, 0.3, 0.3]
        game = g.get("game") or [0, 0, 1, 1]
        self.cam_rect = _rect_px(cam, self.W, self.H, cam_size[0] / cam_size[1])
        self.game_rect = _rect_px(game, self.W, self.H, game_size[0] / game_size[1])
        self.cam_size, self.game_size, self.portrait = cam_size, game_size, portrait

    def _load_logo(self):
        import cv2

        if not self.s.logo_path:
            return None
        img = cv2.imread(str(self.s.logo_path), cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        if img.shape[2] == 3:
            img = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
        target_w = int(self.ow * 0.18)
        h = int(img.shape[0] * target_w / img.shape[1])
        return cv2.resize(img, (target_w, max(1, h)), interpolation=cv2.INTER_AREA)

    def _overlay_logo(self, out: np.ndarray) -> None:
        if self.logo is None:
            return
        lh, lw = self.logo.shape[:2]
        m = int(self.ow * 0.05)
        top = int(self.oh * 0.06) if self.oh > self.ow else m   # below phone status bar / app header
        x = m if self.s.logo_position == "top-left" else self.ow - lw - m
        roi = out[top:top + lh, x:x + lw]
        a = self.logo[:, :, 3:4].astype(np.float32) / 255 * 0.9
        roi[:] = (roi * (1 - a) + self.logo[:, :, :3] * a).astype(np.uint8)

    def frame(self, i: int, img: np.ndarray | None) -> np.ndarray:
        ow, oh = self.ow, self.oh
        if self.layout == "audio" or img is None:
            out = self._audio_bg()
        elif self.layout == "follow":
            p = self.path
            k = min(i, len(p.cx) - 1)
            out = _crop_to(img, crop_rect(self.W, self.H, self.aspect, p.cx[k], p.cy[k], p.zoom[k]), ow, oh)
        elif self.layout == "split":
            pw, ph = self.panel
            out = np.zeros((oh, ow, 3), np.uint8)
            for j, (path, zoom) in enumerate(self.panels):
                k = min(i, len(path.cx) - 1)
                rect = crop_rect(self.W, self.H, pw / ph, path.cx[k], path.cy[k], zoom, face_y=0.45)
                tile = _crop_to(img, rect, pw, ph)
                if oh >= ow:
                    out[j * ph:(j + 1) * ph] = tile
                else:
                    out[:, j * pw:(j + 1) * pw] = tile
            # Thin divider between panels.
            for j in range(1, len(self.panels)):
                if oh >= ow:
                    out[j * ph - 2:j * ph + 2] = 15
                else:
                    out[:, j * pw - 2:j * pw + 2] = 15
        elif self.layout == "gameplay":
            out = np.zeros((oh, ow, 3), np.uint8)
            cam = _crop_to(img, self.cam_rect, *self.cam_size)
            game = _crop_to(img, self.game_rect, *self.game_size)
            if self.portrait:
                out[:self.cam_h] = cam
                out[self.cam_h:] = game
                out[self.cam_h - 2:self.cam_h + 2] = 15
            else:
                out[:] = game
                m = int(oh * 0.04)
                cw, ch = self.cam_size
                out[m:m + ch, ow - cw - m:ow - m] = cam
        else:  # fit
            out = _blurred_bg(img, ow, oh)
            ih, iw = img.shape[:2]
            s = min(ow / iw, oh / ih)
            fw, fh = int(iw * s) // 2 * 2, int(ih * s) // 2 * 2
            import cv2
            fg = cv2.resize(img, (fw, fh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
            # Slightly above centre in portrait, leaving room for subtitles below.
            y = int((oh - fh) * (0.42 if oh > ow else 0.5))
            x = (ow - fw) // 2
            out[y:y + fh, x:x + fw] = fg
        self._overlay_logo(out)
        return out

    def _audio_bg(self) -> np.ndarray:
        if not hasattr(self, "_bg"):
            yy = np.linspace(0, 1, self.oh)[:, None, None]
            top, bottom = np.array([60, 20, 45]), np.array([20, 8, 12])   # BGR: plum -> near black
            self._bg = (top * (1 - yy) + bottom * yy).astype(np.uint8).repeat(self.ow, axis=1)
        return self._bg.copy()


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def _audio_filter(has_music: bool, volume: float, duration: float,
                  keeps: list[tuple[float, float]] | None = None,
                  sounds: list[tuple[int, float, float]] | None = None, music_input: int = 2) -> str:
    """Voice (input 1) -> only the kept pieces, joined; + music that ducks under
    the voice; + sound effects (input index, output time, volume); loudness -14 LUFS."""
    norm = "loudnorm=I=-14:TP=-1.5:LRA=11"   # social-media loudness
    parts = []
    if keeps and len(keeps) > 1:
        n = len(keeps)
        parts.append("[1:a]aresample=48000,asplit=" + str(n) + "".join(f"[p{i}]" for i in range(n)))
        for i, (a, b) in enumerate(keeps):
            parts.append(f"[p{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[k{i}]")
        parts.append("".join(f"[k{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[voice0]")
    elif keeps:
        a, b = keeps[0]
        parts.append(f"[1:a]aresample=48000,atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[voice0]")
    else:
        parts.append("[1:a]aresample=48000[voice0]")
    mix = ["[voice]"]
    if has_music:
        fade_out = max(0.0, duration - 1.0)
        parts.append("[voice0]asplit=2[voice][sc]")
        parts.append(f"[{music_input}:a]aresample=48000,volume={volume:.3f},afade=t=in:d=0.6,"
                     f"afade=t=out:st={fade_out:.2f}:d=1[mus]")
        # Music ducks automatically while someone talks.
        parts.append("[mus][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[duck]")
        mix.append("[duck]")
    else:
        parts.append("[voice0]anull[voice]")
    for j, (idx, at, vol) in enumerate(sounds or []):
        ms = int(max(0.0, at) * 1000)
        parts.append(f"[{idx}:a]aresample=48000,volume={vol:.2f},adelay={ms}:all=1[fx{j}]")
        mix.append(f"[fx{j}]")
    if len(mix) > 1:
        parts.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0,{norm}[a]")
    else:
        parts.append(f"[voice]{norm}[a]")
    return ";".join(parts)


def _text_ass(texts, plan, out_size: tuple[int, int], path: Path) -> Path | None:
    """Text overlays from the editor as positioned libass events."""
    from app.media.subtitles import FONTS, _ass_color, _clean, _ts

    ow, oh = out_size
    scale = min(ow, oh) / 1080
    styles, events = [], []
    looks = {"title": ("montserrat-black", 1), "label": ("montserrat-extrabold", 3), "outline": ("bebas", 1)}
    for i, t in enumerate(texts):
        rng = plan.out_range(t.start, t.end)
        if rng is None or not t.text.strip():
            continue
        font_key, border = looks.get(t.style, looks["title"])
        size = int(max(20, min(220, t.size)) * scale)
        if border == 3:   # opaque box behind the text
            outline, back, width = _ass_color(t.box_color), _ass_color(t.box_color), 12 * scale
        else:
            outline, back, width = "&H00000000", "&H80000000", 6 * scale
        styles.append(f"Style: T{i},{FONTS[font_key]},{size},{_ass_color(t.color)},{_ass_color(t.color)},{outline},"
                      f"{back},0,0,0,0,100,100,0,0,{border},{width:.1f},{2 * scale:.1f},5,0,0,0,1")
        x, y = int(min(1, max(0, t.x)) * ow), int(min(1, max(0, t.y)) * oh)
        events.append(f"Dialogue: 1,{_ts(rng[0])},{_ts(rng[1])},T{i},,0,0,0,,{{\\pos({x},{y})}}{_clean(t.text, False)}")
    if not events:
        return None
    path.write_text("\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {ow}", f"PlayResY: {oh}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding", *styles, "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", *events, ""]), encoding="utf-8")
    return path


class _Images:
    """Editor image overlays (PNG with transparency), drawn on output frames."""

    def __init__(self, items, out_size):
        import cv2

        self.items = []
        ow, oh = out_size
        for it in items:
            img = cv2.imread(str(it.path), cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
            if img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
            if img.shape[2] == 3:
                img = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
            w = max(8, int(min(1.0, max(0.03, it.w)) * ow))
            h = max(8, int(img.shape[0] * w / img.shape[1]))
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            x0, y0 = int(it.x * ow - w / 2), int(it.y * oh - h / 2)
            self.items.append((it.start, it.end, img, x0, y0))

    def draw(self, out: np.ndarray, t: float) -> None:
        oh, ow = out.shape[:2]
        for a, b, img, x0, y0 in self.items:
            if not a <= t < b:
                continue
            h, w = img.shape[:2]
            xa, ya, xb, yb = max(0, x0), max(0, y0), min(ow, x0 + w), min(oh, y0 + h)
            if xb <= xa or yb <= ya:
                continue
            sub = img[ya - y0:yb - y0, xa - x0:xb - x0]
            alpha = sub[:, :, 3:4].astype(np.float32) / 255
            roi = out[ya:yb, xa:xb]
            roi[:] = (roi * (1 - alpha) + sub[:, :, :3] * alpha).astype(np.uint8)


def render_clip(src: Path, start: float, end: float, out_path: Path, settings: RenderSettings,
                meta: dict, vision: dict | None, transcript: dict | None,
                on_progress: Callable[[float], None] | None = None, edit=None) -> str:
    """Renders one clip; returns the layout actually used. With an EditPlan,
    its trim replaces start/end and its cuts, texts, images, sounds and music apply."""
    if edit is not None:
        start, end = edit.start, edit.end
    dur = max(0.2, end - start)
    out_dur = edit.total if edit is not None else dur
    fps = float(min(30.0, meta.get("fps") or 30.0))
    has_video = bool(meta.get("has_video"))
    W, H = meta.get("width") or 1280, meta.get("height") or 720
    scale = min(1.0, (1920 * max(0.25, settings.scale)) / max(W, H))
    W, H = int(W * scale) // 2 * 2, int(H * scale) // 2 * 2

    samples = faces_between(vision, start, end) if vision else []
    words = [(w["start"], w["end"]) for seg in (transcript or {}).get("segments", [])
             for w in (seg.get("words") or []) if w["end"] >= start and w["start"] <= end]
    speech = lambda t: any(a - 0.3 <= t <= b + 0.3 for a, b in words)  # noqa: E731
    who = speaker_track(samples, speech) if samples else []
    comp = Composer(settings, W, H, fps, start, end, samples, who, has_video)
    ow, oh = comp.ow, comp.oh

    filters = []
    temp_files = []
    if settings.subtitles and transcript:
        style = dict(settings.style or {})
        if comp.layout == "split":
            style["position"] = "middle"   # on the divider, never over a face
        elif comp.layout == "gameplay" and comp.portrait:
            # Just under the streamer's camera, over the top edge of the game.
            style["position"] = "top"
            style["margin_top"] = comp.cam_h + int(oh * 0.025)
        ass_path = out_path.with_suffix(".ass")
        cw = clip_words(transcript, start, end)
        if edit is not None:   # the editor deleted words: retime what's left
            cw = edit.remap_words([{**w, "start": w["start"] + start, "end": w["end"] + start} for w in cw])
        build_ass(cw, style, (ow, oh), settings.format, ass_path)
        filters.append(f"ass=filename={ass_path}:fontsdir={FONTS_DIR}")
        temp_files.append(ass_path)
    images = None
    if edit is not None:
        tp = _text_ass(edit.texts, edit, (ow, oh), out_path.with_suffix(".texts.ass"))
        if tp:
            filters.append(f"ass=filename={tp}:fontsdir={FONTS_DIR}")
            temp_files.append(tp)
        images = _Images(edit.images, (ow, oh)) if edit.images else None

    nv = video_encoder() == "h264_nvenc"
    music = edit.music_path if (edit is not None and edit.music_path) else settings.music_path
    music_vol = edit.music_volume if (edit is not None and edit.music_path) else settings.music_volume
    vf = "[0:v]" + ",".join(filters + ["format=yuv420p"]) + "[v]"
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{ow}x{oh}", "-r", f"{fps:g}", "-i", "pipe:0",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    next_input = 2
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        next_input += 1
    sounds = []
    for snd in (edit.sounds if edit is not None else []):
        if edit.kept(snd.t):
            cmd += ["-i", str(snd.path)]
            sounds.append((next_input, edit.out_t(snd.t), snd.volume))
            next_input += 1
    keeps_rel = [(a - start, b - start) for a, b in edit.keeps] if edit is not None else None
    enc_args = (["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30"] if settings.scale < 1
                else _encoder_args(video_encoder()))
    cmd += ["-filter_complex", vf + ";" + _audio_filter(bool(music), music_vol, out_dur, keeps_rel, sounds),
            "-map", "[v]", "-map", "[a]", *enc_args,
            "-c:a", "aac", "-b:a", "128k" if settings.scale < 1 else "192k", "-ar", "48000",
            "-t", f"{out_dur:.3f}", "-movflags", "+faststart", str(out_path)]

    with tempfile.TemporaryFile() as err:
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        assert enc.stdin is not None
        total = int(round(dur * fps))
        written = 0
        try:
            frames = iter_frames(src, fps, (W, H), start, dur, hwaccel=nv) if has_video else iter([])
            for i in range(total):
                item = next(frames, None)
                img = item[1] if item is not None else None
                if has_video and img is None:
                    break     # source ended slightly early
                t = start + i / fps
                if edit is not None and not edit.kept(t):
                    continue  # cut in the editor
                frame = comp.frame(i, img)
                if images is not None:
                    images.draw(frame, t)
                enc.stdin.write(frame.tobytes())
                written += 1
                if on_progress and i % 15 == 0:
                    on_progress(i / total)
        except BrokenPipeError:
            pass
        finally:
            try:
                enc.stdin.close()
            except BrokenPipeError:
                pass
        rc = enc.wait()
        for f in temp_files:
            f.unlink(missing_ok=True)
        if rc != 0 or written == 0:
            err.seek(0)
            msg = err.read().decode(errors="replace")[-1500:]
            log.error("render failed: %s", msg)
            raise PipelineError("render_failed", msg[-300:])
    return comp.layout
