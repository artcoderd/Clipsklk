"""Web editor endpoints. The browser plays a light preview copy of the clip's
stretch (already framed, no subtitles) and draws subtitles, texts and images
on top; saving stores an EditDoc; rendering applies it on the server."""
import gzip
import hashlib
import json
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.media.edit import normalize_ranges
from app.models import Clip, Job, LibraryItem, MediaAsset, User
from app.routers.clips import _own_clip, _serve
from app.schemas import ClipOut, EditDoc
from app.storage import get_storage
from app.worker import enqueue_proxy

router = APIRouter(prefix="/clips", tags=["editor"])

MARGIN = 20.0   # seconds of source available around the clip for extending the trim


def _asset(db: Session, clip: Clip) -> MediaAsset:
    job = db.get(Job, clip.job_id)
    asset = db.get(MediaAsset, clip.asset_id or (job.asset_id if job else None))
    if asset is None or not asset.duration:
        raise HTTPException(status.HTTP_409_CONFLICT, "job_not_ready")
    return asset


def _bounds(clip: Clip, asset: MediaAsset) -> tuple[float, float]:
    return max(0.0, clip.start - MARGIN), min(asset.duration, clip.end + MARGIN)


def _proxy_sig(clip: Clip, lo: float, hi: float) -> str:
    """The preview depends on framing options and the stretch it covers (not on subtitles, music...)."""
    r = clip.render or {}
    keys = {k: r.get(k) for k in ("format", "layout", "framing", "gameplay", "person")}
    raw = json.dumps([keys, round(lo, 1), round(hi, 1)], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _load_json(key: str | None) -> dict:
    if not key:
        return {}
    storage = get_storage()
    local = storage.local_path(key)
    if local is None:
        tmp = Path(tempfile.mkdtemp()) / "f.json.gz"
        storage.get_file(key, tmp)
        local = tmp
    with gzip.open(local, "rt", encoding="utf-8") as f:
        return json.load(f)


def _default_edit(clip: Clip) -> dict:
    return {"trim": [clip.start, clip.end], "cuts": [], "texts": [], "images": [], "sounds": [], "markers": [],
            "music": None}


@router.get("/{clip_id}/editor")
def editor_data(clip_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clip = _own_clip(db, user, clip_id)
    asset = _asset(db, clip)
    lo, hi = _bounds(clip, asset)
    if clip.proxy_start is not None and clip.proxy_status in ("ready", "preparing"):
        lo, hi = clip.proxy_start, clip.proxy_end
    transcript = _load_json(asset.transcript_key)
    words = [{"start": round(w["start"], 3), "end": round(w["end"], 3), "word": w["word"].strip()}
             for seg in transcript.get("segments", []) for w in (seg.get("words") or [])
             if lo <= w["start"] < hi and w["word"].strip()]
    analysis = _load_json(asset.analysis_key)
    loud = analysis.get("loudness_db") or []
    level = [round(max(0.0, min(1.0, (v + 50) / 45)), 2) for v in loud[int(lo):int(hi) + 1]]
    return {
        "clip": ClipOut.model_validate(clip).model_dump(mode="json"),
        "edit": {k: v for k, v in (clip.edit or _default_edit(clip)).items() if k != "orig"},
        "bounds": [lo, hi],
        "duration": asset.duration,
        "words": words,
        "level": level,          # loudness per second from `bounds[0]`, 0..1 (for the timeline waveform)
        "proxy": {"status": clip.proxy_status, "start": clip.proxy_start,
                  "url": f"/api/clips/{clip.id}/proxy?s={clip.proxy_sig}" if clip.proxy_status == "ready" else None},
    }


@router.post("/{clip_id}/editor/prepare")
def prepare(clip_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Makes the preview copy if it's missing or out of date (framing changed, trim beyond it)."""
    clip = _own_clip(db, user, clip_id)
    asset = _asset(db, clip)
    lo, hi = _bounds(clip, asset)
    if clip.edit:   # keep room around the current trim
        lo, hi = min(lo, max(0.0, clip.edit["trim"][0] - MARGIN)), max(hi, min(asset.duration, clip.edit["trim"][1] + MARGIN))
    sig = _proxy_sig(clip, lo, hi)
    covered = (clip.proxy_start is not None and clip.proxy_start <= lo + 0.5 and clip.proxy_end >= hi - 0.5
               and clip.proxy_sig == _proxy_sig(clip, clip.proxy_start, clip.proxy_end))
    if clip.proxy_status == "ready" and covered:
        return {"status": "ready"}
    if clip.proxy_status != "preparing":
        clip.proxy_status, clip.proxy_start, clip.proxy_end, clip.proxy_sig = "preparing", lo, hi, sig
        db.commit()
        enqueue_proxy(clip.id)
    return {"status": "preparing"}


@router.get("/{clip_id}/proxy")
def proxy_file(clip_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clip = _own_clip(db, user, clip_id)
    if clip.proxy_status != "ready" or not clip.proxy_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _serve(clip.proxy_key, "video/mp4")


def _check_library(db: Session, user: User, item_id: uuid.UUID, kinds: tuple[str, ...]) -> None:
    item = db.get(LibraryItem, item_id)
    if item is None or item.user_id != user.id or item.kind not in kinds:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_library_item")


@router.put("/{clip_id}/edit", response_model=EditDoc)
def save_edit(clip_id: uuid.UUID, doc: EditDoc, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Saves the edit (normalised). Rendering is a separate step (POST /clips/{id}/render)."""
    clip = _own_clip(db, user, clip_id)
    asset = _asset(db, clip)
    a, b = sorted(doc.trim)
    a, b = max(0.0, a), min(asset.duration, b)
    if b - a < 1.0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "edit_too_short")
    cuts = normalize_ranges(doc.cuts, a, b)
    if (b - a) - sum(y - x for x, y in cuts) < 1.0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "edit_too_short")
    for im in doc.images:
        _check_library(db, user, im.library_id, ("image", "logo"))
    for sd in doc.sounds:
        _check_library(db, user, sd.library_id, ("sound", "music"))
    if doc.music:
        _check_library(db, user, doc.music.library_id, ("music",))

    def span(x, y):
        """Inside the trim, keeping the item's length (an item placed outside is moved in, not dropped)."""
        x, y = sorted((x, y))
        if y <= a or x >= b:   # entirely outside: move it in, keeping its length
            length = min(b - a, max(0.3, y - x))
            x = a if y <= a else b - length
            return round(x, 3), round(x + length, 3)
        x, y = max(a, x), min(b, y)   # partly outside: cut to the clip
        return round(x, 3), round(max(y, x + 0.1), 3)

    out = doc.model_copy(update={"trim": [round(a, 3), round(b, 3)], "cuts": [list(c) for c in cuts]})
    for item in (*out.texts, *out.images):
        item.start, item.end = span(item.start, item.end)
    for sd in out.sounds:
        sd.t = min(max(sd.t, a), b)
    for m in out.markers:
        m.t = min(max(m.t, a), b)
    # Remember the clip's original range the first time it's edited, for "Deshacer todo".
    orig = (clip.edit or {}).get("orig") or [clip.start, clip.end]
    clip.edit = {**out.model_dump(mode="json"), "orig": orig}
    clip.start, clip.end = round(a, 2), round(b, 2)   # "más clips" won't overlap the edited range
    db.commit()
    return out


@router.delete("/{clip_id}/edit", status_code=204)
def reset_edit(clip_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clip = _own_clip(db, user, clip_id)
    if clip.edit:
        clip.start, clip.end = clip.edit.get("orig") or clip.edit["trim"]   # back to the original clip
    clip.edit = None
    db.commit()
