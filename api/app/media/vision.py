"""Face analysis for reframing and speaker detection.

One pass over the source (2-4 frames per second, 640 px wide):

- YuNet finds faces and 5 landmarks (eyes, nose, mouth corners).
- A simple IoU tracker links detections over time into tracks.
- SFace embeddings (about 1 per second per track) group tracks into people,
  so the same person is recognised after a camera cut.
- Mouth activity: change of the landmark-aligned mouth patch between samples,
  minus the change of the eye patch (head motion), i.e. "is the mouth moving".

Stored per asset (like the transcript), then reused by every render and by
"clips of one person". Positions are normalised to 0..1 of the frame.
"""
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

WORK_WIDTH = 640
YUNET = ("opencv/face_detection_yunet", "face_detection_yunet_2023mar.onnx")
SFACE = ("opencv/face_recognition_sface", "face_recognition_sface_2021dec.onnx")
SAME_PERSON = 0.40      # cosine similarity (SFace's own threshold is 0.363)
MIN_TRACK_SECONDS = 1.0


@lru_cache
def _model_path(repo: str, filename: str) -> str:
    from huggingface_hub import hf_hub_download
    return hf_hub_download(repo, filename)


@lru_cache
def _models():
    import cv2

    det = cv2.FaceDetectorYN.create(_model_path(*YUNET), "", (WORK_WIDTH, 360), 0.6, 0.3, 50)
    rec = cv2.FaceRecognizerSF.create(_model_path(*SFACE), "")
    return det, rec


def work_size(width: int, height: int) -> tuple[int, int]:
    h = int(round(height * WORK_WIDTH / width / 2) * 2)
    return WORK_WIDTH, max(2, h)


def iter_frames(src: str | Path, fps: float, size: tuple[int, int], start: float = 0.0,
                duration: float | None = None, hwaccel: bool = False):
    """Yields (t, BGR frame) at `fps` scaled to `size`, read from an ffmpeg pipe."""
    w, h = size
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-nostdin"]
    if hwaccel:
        cmd += ["-hwaccel", "cuda"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-an", "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    n, frame_bytes = 0, w * h * 3
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield start + n / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            n += 1
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def _patch(gray: np.ndarray, cx: float, cy: float, half_w: float, half_h: float, out=(32, 16)) -> np.ndarray | None:
    import cv2

    x0, x1 = int(cx - half_w), int(cx + half_w)
    y0, y1 = int(cy - half_h), int(cy + half_h)
    if x0 < 0 or y0 < 0 or x1 > gray.shape[1] or y1 > gray.shape[0] or x1 - x0 < 4 or y1 - y0 < 4:
        return None
    p = cv2.resize(gray[y0:y1, x0:x1], out, interpolation=cv2.INTER_AREA).astype(np.float32)
    return (p - p.mean()) / (p.std() + 8.0)  # contrast-normalised: lighting changes don't count


def _iou(a, b) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0.0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0.0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    return inter / (aw * ah + bw * bh - inter + 1e-9)


@dataclass
class _Track:
    id: int
    box: tuple
    last_i: int
    mouth: np.ndarray | None = None
    eyes: np.ndarray | None = None
    embeds: list = field(default_factory=list)
    last_embed_i: int = -10_000
    samples: int = 0
    lm: np.ndarray | None = None
    pending: list = field(default_factory=list)
    best: tuple = (0.0, 0.0, None)  # (area, t, box) for the person thumbnail


def analyze_video(src: str | Path, width: int, height: int, duration: float,
                  on_progress: Callable[[float], None] | None = None, hwaccel: bool = False) -> dict:
    import cv2

    det, rec = _models()
    fps = 4.0 if duration <= 3600 else 2.0     # face detection rate
    sub = 2                                     # mouth measured `sub` times per detection
    size = work_size(width, height)
    W, H = size
    det.setInputSize(size)
    min_face = 0.03 * W

    tracks: dict[int, _Track] = {}
    samples: list = []
    next_id = 0
    max_gap = int(fps * 1.5)
    prev_thumb = None

    def mouth_eyes(gray, lm, h):
        reye, leye, rmouth, lmouth = lm[0:2], lm[2:4], lm[6:8], lm[8:10]
        span = max(4.0, float(np.linalg.norm(lmouth - rmouth)))
        mc, ec = (rmouth + lmouth) / 2, (reye + leye) / 2
        return (_patch(gray, mc[0], mc[1] + 0.1 * h, 0.9 * span, 0.45 * span),
                _patch(gray, ec[0], ec[1], 1.1 * span, 0.4 * span))

    def diff(tr, mouth, eyes) -> float | None:
        if mouth is None or tr.mouth is None:
            return None
        m = float(np.abs(mouth - tr.mouth).mean())
        e = float(np.abs(eyes - tr.eyes).mean()) if eyes is not None and tr.eyes is not None else 0.0
        return max(0.0, m - 0.8 * e)   # mouth change minus head/eye change

    for j, (t, frame) in enumerate(iter_frames(src, fps * sub, size, hwaccel=hwaccel)):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Camera cut: the whole picture changes at once -> start fresh tracks.
        thumb = cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32)
        if prev_thumb is not None and float(np.abs(thumb - prev_thumb).mean()) > 12:
            for tr in tracks.values():
                tr.last_i, tr.mouth, tr.eyes = -10_000, None, None
        prev_thumb = thumb
        i = j // sub

        if j % sub:  # in-between frame: mouth only, at the last known landmarks
            for tr in tracks.values():
                if tr.last_i == i and tr.lm is not None:
                    mouth, eyes = mouth_eyes(gray, tr.lm, tr.box[3])
                    d = diff(tr, mouth, eyes)
                    if d is not None:
                        tr.pending.append(d)
                    tr.mouth, tr.eyes = mouth, eyes
            continue

        _, faces = det.detect(frame)
        live = {tid: tr for tid, tr in tracks.items() if i - tr.last_i <= max_gap}
        used: set[int] = set()
        row = []
        for f in (faces if faces is not None else []):
            x, y, w, h = (float(v) for v in f[:4])
            if w < min_face:
                continue
            box = (x, y, w, h)
            # Greedy match to the best overlapping live track.
            best_id, best_iou = None, 0.25
            for tid, tr in live.items():
                if tid in used:
                    continue
                o = _iou(tr.box, box)
                if o > best_iou:
                    best_id, best_iou = tid, o
            if best_id is None:
                best_id = next_id
                next_id += 1
                tracks[best_id] = _Track(best_id, box, i)
            used.add(best_id)
            tr = tracks[best_id]

            lm = np.asarray(f[4:14], np.float32)
            mouth, eyes = mouth_eyes(gray, lm, h)
            d = diff(tr, mouth, eyes) if tr.last_i == i - 1 else None
            parts = tr.pending + ([d] if d is not None else [])
            act = float(np.mean(parts)) if parts else 0.0
            tr.pending = []
            tr.mouth, tr.eyes, tr.box, tr.lm, tr.last_i = mouth, eyes, box, lm, i
            tr.samples += 1
            if w * h > tr.best[0]:
                tr.best = (w * h, t, (x / W, y / H, w / W, h / H))
            if len(tr.embeds) < 12 and i - tr.last_embed_i >= fps:
                try:
                    tr.embeds.append(rec.feature(rec.alignCrop(frame, f)).flatten())
                    tr.last_embed_i = i
                except cv2.error:
                    pass
            row.append([best_id, round((x + w / 2) / W, 4), round((y + h / 2) / H, 4),
                        round(w / W, 4), round(h / H, 4), round(act, 3)])
        for tr in tracks.values():   # tracks not seen this time lose their mouth history
            if tr.last_i != i:
                tr.pending = []
        samples.append([round(t, 3), row])
        if on_progress and duration > 0 and i % 8 == 0:
            on_progress(min(1.0, t / duration))

    people, track_person = _cluster(tracks, fps)
    return {"fps": fps, "size": [W, H], "samples": samples,
            "track_person": {str(k): v for k, v in track_person.items()}, "people": people}


def _cluster(tracks: dict[int, _Track], fps: float) -> tuple[list, dict[int, int]]:
    """Groups tracks into people by face embedding (greedy, longest tracks first)."""
    centroids: list[np.ndarray] = []
    members: list[list[_Track]] = []
    track_person: dict[int, int] = {}
    for tr in sorted(tracks.values(), key=lambda x: -x.samples):
        if tr.samples / fps < MIN_TRACK_SECONDS or not tr.embeds:
            continue  # false positives and blips are ignored
        e = np.mean(tr.embeds, axis=0)
        e /= np.linalg.norm(e) + 1e-9
        sims = [float(c @ e) for c in centroids]
        if sims and max(sims) >= SAME_PERSON:
            pid = int(np.argmax(sims))
            members[pid].append(tr)
            n = len(members[pid])
            c = centroids[pid] * (n - 1) + e
            centroids[pid] = c / (np.linalg.norm(c) + 1e-9)
        else:
            pid = len(centroids)
            centroids.append(e)
            members.append([tr])
        track_person[tr.id] = pid
    people = []
    for pid, trs in enumerate(members):
        best = max(trs, key=lambda x: x.best[0]).best
        people.append({"id": pid, "seconds": round(sum(x.samples for x in trs) / fps, 1),
                       "thumb_t": best[1], "thumb_box": [round(v, 4) for v in best[2]]})
    # Most visible first, ids renumbered in that order.
    order = sorted(range(len(people)), key=lambda p: -people[p]["seconds"])
    remap = {old: new for new, old in enumerate(order)}
    people = [{**people[old], "id": new} for new, old in enumerate(order)]
    return people, {t: remap[p] for t, p in track_person.items()}


# --- Queries used by selection and rendering --------------------------------

@dataclass
class Face:
    person: int | None
    cx: float
    cy: float
    w: float
    h: float
    act: float


def faces_between(vision: dict, start: float, end: float) -> list[tuple[float, list[Face]]]:
    tp = vision.get("track_person", {})
    out = []
    for t, row in vision.get("samples", []):
        if start - 1.0 <= t <= end + 1.0:
            out.append((t, [Face(tp.get(str(r[0])), *r[1:6]) for r in row]))
    return out


def speaker_track(samples: list[tuple[float, list[Face]]], speech: Callable[[float], bool],
                  smooth_s: float = 1.0, switch_ratio: float = 1.4, min_hold_s: float = 1.0) -> list[int | None]:
    """Who is speaking at each sample (person id), with hysteresis so the
    choice doesn't flicker. Seconds without speech keep the last speaker; if
    only one person is visible, that's the speaker."""
    if not samples:
        return []
    dt = (samples[-1][0] - samples[0][0]) / max(1, len(samples) - 1) or 0.25
    k = max(1, int(round(smooth_s / dt)))
    persons = sorted({f.person for _, fs in samples for f in fs if f.person is not None})
    act = {p: np.zeros(len(samples)) for p in persons}
    for i, (_, fs) in enumerate(samples):
        for f in fs:
            if f.person is not None:
                act[f.person][i] = max(act[f.person][i], f.act)
    kernel = np.ones(k) / k
    sm = {p: np.convolve(a, kernel, mode="same") for p, a in act.items()}

    out: list[int | None] = []
    cur, hold = None, 0.0
    for i, (t, fs) in enumerate(samples):
        visible = [f.person for f in fs if f.person is not None]
        if not visible:
            out.append(cur)
            continue
        if len(set(visible)) == 1:
            cand = visible[0]
        else:
            cand = max(set(visible), key=lambda p: sm[p][i])
        if cur is None or cur not in visible:
            cur, hold = cand, 0.0
        elif cand != cur and speech(t):
            hold += dt
            if sm[cand][i] > switch_ratio * max(sm[cur][i], 1e-3) and hold >= min_hold_s * 0.5:
                cur, hold = cand, 0.0
        else:
            hold = 0.0
        out.append(cur)
    return out


def person_speaking_seconds(vision: dict, transcript: dict, n_seconds: int) -> dict[int, np.ndarray]:
    """For every person: 1.0 on seconds where they are the one talking."""
    speech = np.zeros(n_seconds + 2, bool)
    for seg in transcript.get("segments", []):
        for w in seg.get("words") or []:
            speech[int(w["start"]): int(w["end"]) + 1] = True
    samples = faces_between(vision, 0, n_seconds)
    who = speaker_track(samples, lambda t: bool(speech[min(len(speech) - 1, int(t))]))
    out = {p["id"]: np.zeros(n_seconds, np.float32) for p in vision.get("people", [])}
    counts = np.zeros(n_seconds, np.float32)
    for (t, _), p in zip(samples, who):
        s = int(t)
        if 0 <= s < n_seconds:
            counts[s] += 1
            if p is not None and p in out and speech[s]:
                out[p][s] += 1
    for p in out:
        out[p] = np.where(counts > 0, out[p] / np.maximum(counts, 1), 0)
    return out
