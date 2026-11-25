"""Live updates, published by workers over Redis pub/sub and streamed to
browsers with Server-Sent Events. Messages: {"type": "job", "job": {...}} and
{"type": "clip", "clip": {...}}."""
import json
import uuid

from app.redis_client import get_redis


def channel(job_id: uuid.UUID | str) -> str:
    return f"job:{job_id}"


def publish(job_id: uuid.UUID | str, kind: str, payload: dict) -> None:
    get_redis().publish(channel(job_id), json.dumps({"type": kind, kind: payload}, default=str))
