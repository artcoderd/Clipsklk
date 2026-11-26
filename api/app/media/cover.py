"""Cover image: the clip's most expressive frame with its title on top,
drawn by libass with the same fonts as the subtitles."""
import subprocess
from pathlib import Path

from app.media import PipelineError
from app.media.render import FONTS_DIR, OUTPUT_SIZES
from app.media.subtitles import FONTS, _ass_color, _clean


def make_cover(video: Path, at: float, title: str, fmt: str, dest: Path, style: dict | None = None) -> None:
    ow, oh = OUTPUT_SIZES.get(fmt, OUTPUT_SIZES["9:16"])
    st = style or {}
    scale = min(ow, oh) / 1080
    font = FONTS.get(st.get("font", "montserrat-black"), FONTS["montserrat-black"])
    size = int(112 * scale)
    words = _clean(title, True).split()
    # Last word highlighted, like the karaoke subtitles.
    hl = _ass_color(st.get("highlight_color", "#FFE600"))
    text = " ".join(words[:-1] + [f"{{\\c{hl}}}{words[-1]}{{\\r}}"]) if words else ""
    margin_v = int(oh * (0.12 if oh > ow else 0.08))
    ass = dest.with_suffix(".ass")
    ass.write_text("\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {ow}", f"PlayResY: {oh}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: T,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,"
        f"{9 * scale:.1f},{4 * scale:.1f},8,{int(ow * 0.07)},{int(ow * 0.07)},{margin_v},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,0:00:00.00,0:01:00.00,T,,0,0,0,,{text}", ""]), encoding="utf-8")
    vf = f"ass=filename={ass}:fontsdir={FONTS_DIR}" if text else "null"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{max(0.0, at):.3f}", "-i", str(video),
                        "-frames:v", "1", "-vf", vf, "-q:v", "3", str(dest)], capture_output=True, text=True, timeout=60)
    ass.unlink(missing_ok=True)
    if r.returncode != 0 or not dest.exists():
        raise PipelineError("ffmpeg_failed", r.stderr[-300:])
