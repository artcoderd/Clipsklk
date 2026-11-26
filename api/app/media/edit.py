"""A clip's edit: what the web editor saves and the renderer applies.

All times are in *source* seconds (the original video), so cuts, texts,
images and sounds stay attached to the content: deleting a sentence later
doesn't move a title that sits on another sentence. The output timeline is
derived: `out_t()` maps a source time to its position in the final clip.
"""
from dataclasses import dataclass, field
from pathlib import Path

MIN_KEEP = 0.05   # pieces shorter than this are dropped (sub-frame)


def normalize_ranges(ranges: list[list[float]], lo: float, hi: float) -> list[tuple[float, float]]:
    """Sorted, merged, clipped to [lo, hi]."""
    out: list[list[float]] = []
    for a, b in sorted((min(r), max(r)) for r in ranges if len(r) == 2):
        a, b = max(lo, a), min(hi, b)
        if b - a <= 0.01:
            continue
        if out and a <= out[-1][1] + 0.02:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


@dataclass
class TextItem:
    text: str
    start: float
    end: float
    x: float
    y: float
    size: int = 80
    color: str = "#FFFFFF"
    style: str = "title"          # title | label | outline
    box_color: str = "#000000"


@dataclass
class ImageItem:
    path: Path
    start: float
    end: float
    x: float
    y: float
    w: float


@dataclass
class SoundItem:
    path: Path
    t: float
    volume: float = 1.0


@dataclass
class EditPlan:
    start: float
    end: float
    cuts: list[tuple[float, float]] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    images: list[ImageItem] = field(default_factory=list)
    sounds: list[SoundItem] = field(default_factory=list)
    music_path: Path | None = None
    music_volume: float = 0.15

    def __post_init__(self):
        self.cuts = normalize_ranges([list(c) for c in self.cuts], self.start, self.end)
        self.keeps = self._keeps()

    def _keeps(self) -> list[tuple[float, float]]:
        keeps, t = [], self.start
        for a, b in self.cuts:
            if a - t >= MIN_KEEP:
                keeps.append((t, a))
            t = max(t, b)
        if self.end - t >= MIN_KEEP:
            keeps.append((t, self.end))
        return keeps

    @property
    def total(self) -> float:
        return sum(b - a for a, b in self.keeps)

    def kept(self, t: float) -> bool:
        return any(a <= t < b for a, b in self.keeps)

    def out_t(self, t: float) -> float:
        """Position in the final clip of source time t (cut stretches collapse)."""
        return sum(max(0.0, min(b, t) - a) for a, b in self.keeps)

    def remap_words(self, words: list[dict]) -> list[dict]:
        """Absolute-time words -> words on the output timeline, minus cut ones."""
        out = []
        for w in words:
            mid = (w["start"] + w["end"]) / 2
            if not self.kept(mid):
                continue
            a, b = self.out_t(w["start"]), self.out_t(w["end"])
            out.append({**w, "start": a, "end": max(a + 0.05, b)})
        return out

    def out_range(self, a: float, b: float) -> tuple[float, float] | None:
        oa, ob = self.out_t(a), self.out_t(b)
        return (oa, ob) if ob - oa >= 0.05 else None


def plan_from_doc(doc: dict, files: dict[str, Path]) -> EditPlan:
    """EditDoc (validated JSON) + local paths of the library items it uses."""
    a, b = doc["trim"]
    music = doc.get("music") or None
    return EditPlan(
        start=a, end=b, cuts=[tuple(c) for c in doc.get("cuts", [])],
        texts=[TextItem(t["text"], t["start"], t["end"], t["x"], t["y"], t.get("size", 80), t.get("color", "#FFFFFF"),
                        t.get("style", "title"), t.get("box_color", "#000000")) for t in doc.get("texts", [])],
        images=[ImageItem(files[i["library_id"]], i["start"], i["end"], i["x"], i["y"], i["w"])
                for i in doc.get("images", []) if i["library_id"] in files],
        sounds=[SoundItem(files[s["library_id"]], s["t"], s.get("volume", 1.0))
                for s in doc.get("sounds", []) if s["library_id"] in files],
        music_path=files.get(music["library_id"]) if music else None,
        music_volume=music.get("volume", 0.15) if music else 0.15,
    )
