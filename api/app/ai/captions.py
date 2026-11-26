"""Titles, captions and hashtags. No numpy here: the API imports this to
regenerate a caption on demand."""
import re
from collections import Counter

from app.ai.prompts import CAPTION_SYSTEM, caption_request
from app.ai.providers import AIResult, ask
from app.ai.schema import Caption


def clean_tags(tags: list[str], n: int) -> list[str]:
    out = []
    for t in tags:
        t = re.sub(r"[^\w]", "", t.lstrip("#"), flags=re.UNICODE)
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:n]


STOP = set("""a al algo alguien así aunque cada como con cosa cuando de del desde donde dos el ella ellas ellos
en entonces era eres es esa ese eso esta este esto estoy fue fueron ha hay hace hacer la las le les lo los
más me mi mis mucho muy nada ni no nos nosotros o otra otro para pero poco por porque que qué quien se ser si
sí sin sobre solo son su sus también tan te tengo tiene todo todos tu tú un una uno unos usted va vamos ver vez
y ya yo the and you that this with for are was have not but just like what they there""".split())


def heuristic_caption(text: str, hashtags: int = 5) -> tuple[str, str, list[str]]:
    """Title/caption/hashtags without AI, from the clip's own words."""
    words = re.findall(r"[\wáéíóúñü']+", text.lower(), flags=re.UNICODE)
    first = re.split(r"(?<=[.!?¿¡])\s+", text.strip())[0] if text.strip() else ""
    title = " ".join(first.split()[:6]).strip(" ,.;:") or "Clip"
    caption = first[:200]
    common = [w for w, _ in Counter(w for w in words if len(w) >= 5 and w not in STOP).most_common(hashtags)]
    return title, caption, common


def ai_caption(provider: str, clip_text: str, user_rules: str, hashtags: int) -> tuple[Caption, AIResult]:
    result = ask(provider, CAPTION_SYSTEM, f"Transcripción del clip:\n{clip_text}",
                 caption_request("(arriba)", user_rules, hashtags), Caption)
    data = result.data
    data.hashtags = clean_tags(data.hashtags, hashtags)
    return data, result
