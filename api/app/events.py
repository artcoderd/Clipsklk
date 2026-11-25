"""Job progress events, published by workers over Redis pub/sub and streamed to
browsers with Server-Sent Events."""
import json
import uuid

from app.redis_client import get_redis


def channel(job_id: uuid.UUID | str) -> str:
    return f"job:{job_id}"


def publish(job_id: uuid.UUID | str, payload: dict) -> None:
    get_redis().publish(channel(job_id), json.dumps(payload, default=str))
