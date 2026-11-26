"""Word-by-word ("karaoke") subtitles as an ASS file, burned in by libass.

Words are grouped into short lines (2-4 words is the norm on Shorts/Reels);
while a line is on screen, the word being spoken is highlighted, optionally
with a quick "pop". Positions keep clear of each app's interface: TikTok and
Reels cover roughly the bottom fifth and the right edge of a 9:16 video.
"""
import re
from pathlib import Path

FONTS = {
    "montserrat-black": "Montserrat Black",
    "montserrat-extrabold": "Montserrat ExtraBold",
    "montserrat-bold": "Montserrat",
    "bebas": "Bebas Neue Bold",
}

PRESETS: dict[str, dict] = {
    "clasico": {"name": "Clásico", "font": "montserrat-black", "size": 78, "text_color": "#FFFFFF",
                "highlight_color": "#FFE600", "outline_color": "#000000", "outline": 6, "shadow": 2,
                "uppercase": True, "words_per_line": 3, "animation": "pop", "position": "bottom"},
    "neon": {"name": "Neón", "font": "montserrat-extrabold", "size": 74, "text_color": "#FFFFFF",
             "highlight_color": "#FF3DF2", "outline_color": "#1A0026", "outline": 7, "shadow": 0,
             "uppercase": False, "words_per_line": 2, "animation": "pop", "position": "bottom"},
    "bebas": {"name": "Bebas", "font": "bebas", "size": 100, "text_color": "#FFFFFF",
              "highlight_color": "#3DFF8B", "outline_color": "#000000", "outline": 5, "shadow": 3,
              "uppercase": True, "words_per_line": 3, "animation": "highlight", "position": "middle"},
    "minimal": {"name": "Minimal", "font": "montserrat-bold", "size": 58, "text_color": "#FFFFFF",
                "highlight_color": "#FFFFFF", "outline_color": "#000000", "outline": 3, "shadow": 1,
                "uppercase": False, "words_per_line": 5, "animation": "none", "position": "bottom"},
}
DEFAULT_STYLE = PRESETS["clasico"]

# Distance from the bottom edge (fraction of height) that stays clear of app UI.
SAFE_BOTTOM = {"9:16": 0.27, "4:5": 0.14, "1:1": 0.10, "16:9": 0.08}
SAFE_TOP = {"9:16": 0.14, "4:5": 0.07, "1:1": 0.06, "16:9": 0.06}


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(word: str, upper: bool) -> str:
    w = word.strip().replace("\\", "").replace("{", "(").replace("}", ")")
    return w.upper() if upper else w


def chunk_words(words: list[dict], max_words: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > 0.6
                    or cur[-1]["word"].rstrip().endswith((".", "?", "!", "…"))
                    or w["end"] - cur[0]["start"] > 2.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def clip_words(transcript: dict, start: float, end: float) -> list[dict]:
    """Words inside [start, end], with times relative to the clip start."""
    out = []
    for seg in transcript.get("segments", []):
        if seg["end"] < start or seg["start"] > end:
            continue
        for w in seg.get("words") or []:
            if w["start"] >= start - 0.05 and w["end"] <= end + 0.3 and w["word"].strip():
                out.append({"start": max(0.0, w["start"] - start), "end": min(end - start, w["end"] - start),
                            "word": w["word"]})
    return out


def build_ass(words: list[dict], style: dict | None, out_size: tuple[int, int], fmt: str, path: Path) -> Path:
    st = {**DEFAULT_STYLE, **(style or {})}
    ow, oh = out_size
    scale = min(ow, oh) / 1080
    size = int(max(20, min(160, int(st["size"]))) * scale)
    font = FONTS.get(st["font"], FONTS["montserrat-black"])
    outline = max(0, min(14, int(st["outline"]))) * scale
    shadow = max(0, min(10, int(st["shadow"]))) * scale
    pos = st.get("position", "bottom")
    align, margin_v = {"bottom": (2, int(oh * SAFE_BOTTOM.get(fmt, 0.1))),
                       "middle": (5, 0),
                       "top": (8, int(oh * SAFE_TOP.get(fmt, 0.06)))}.get(pos, (2, int(oh * 0.1)))
    if pos == "top" and st.get("margin_top") is not None:
        margin_v = int(st["margin_top"])
    margin_lr = int(ow * 0.08)
    hl = _ass_color(st["highlight_color"])
    upper = bool(st.get("uppercase"))
    anim = st.get("animation", "pop")

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {ow}", f"PlayResY: {oh}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{size},{_ass_color(st['text_color'])},{hl},{_ass_color(st['outline_color'])},"
        f"{_ass_color('#000000', 0x60)},0,0,0,0,100,100,0,0,1,{outline:.1f},{shadow:.1f},{align},"
        f"{margin_lr},{margin_lr},{margin_v},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    chunks = chunk_words(words, max(1, min(8, int(st.get("words_per_line", 3)))))
    for ci, ch in enumerate(chunks):
        next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        line_end = ch[-1]["end"] + 0.25
        if next_start is not None:
            line_end = min(line_end, next_start)
        texts = [_clean(w["word"], upper) for w in ch]
        if anim == "none":
            lines.append(f"Dialogue: 0,{_ts(ch[0]['start'])},{_ts(line_end)},Default,,0,0,0,,{' '.join(texts)}")
            continue
        for k, w in enumerate(ch):
            a = ch[0]["start"] if k == 0 else w["start"]
            b = ch[k + 1]["start"] if k + 1 < len(ch) else line_end
            if b <= a:
                continue
            fx = f"\\c{hl}"
            if anim == "pop":
                fx += "\\t(0,80,\\fscx115\\fscy115)\\t(80,170,\\fscx100\\fscy100)"
            parts = [f"{{{fx}}}{t}{{\\r}}" if j == k else t for j, t in enumerate(texts)]
            lines.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},Default,,0,0,0,,{' '.join(parts)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
