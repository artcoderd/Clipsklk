from app.config import get_settings
from app.redis_client import get_redis


def _key(scope: str, ident: str) -> str:
    return f"rl:{scope}:{ident.lower()}"


def is_blocked(scope: str, ident: str) -> bool:
    s = get_settings()
    count = get_redis().get(_key(scope, ident))
    return count is not None and int(count) >= s.login_max_attempts


def register_failure(scope: str, ident: str) -> None:
    s = get_settings()
    r = get_redis()
    key = _key(scope, ident)
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, s.login_window_seconds)
    pipe.execute()


def reset(scope: str, ident: str) -> None:
    get_redis().delete(_key(scope, ident))
