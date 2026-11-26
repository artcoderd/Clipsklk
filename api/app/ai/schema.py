"""What the model must answer: a list of clip ideas (JSON, schema-enforced)."""
from pydantic import BaseModel, ConfigDict


class ClipIdea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float          # seconds, the start of a transcript line
    end: float            # seconds, the end of a transcript line
    title: str            # 2-6 words for the cover
    caption: str          # text to publish (no hashtags)
    hashtags: list[str]
    cover_time: float     # most expressive moment, inside [start, end]
    reason: str           # one sentence: why it works
    score: int            # 1-100


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clips: list[ClipIdea]


class Caption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    caption: str
    hashtags: list[str]


def json_schema(model: type[BaseModel]) -> dict:
    """Strict JSON schema (every field required, no extra keys) from a model."""
    schema = model.model_json_schema()

    def tighten(node):
        """`node` is always a schema (never the `properties` mapping itself),
        so removing its "title" annotation can't remove a field named title."""
        if isinstance(node, list):
            for v in node:
                tighten(v)
            return
        if not isinstance(node, dict):
            return
        if isinstance(node.get("title"), str):
            node.pop("title")
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
            for sub in node["properties"].values():
                tighten(sub)
        for key in ("items", "anyOf", "allOf", "oneOf"):
            if key in node:
                tighten(node[key])
        for sub in node.get("$defs", {}).values():
            tighten(sub)
    tighten(schema)
    return schema
