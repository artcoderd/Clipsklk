"""Which provider and rules apply to a user's request."""
import uuid

from sqlalchemy.orm import Session

from app.ai.providers import configured
from app.config import get_settings
from app.models import AIPrefs, AIUsage


def get_prefs(db: Session, user_id: uuid.UUID) -> AIPrefs:
    p = db.get(AIPrefs, user_id)
    return p or AIPrefs(user_id=user_id, use_ai=True, provider=None, rules="", hashtags=5)


def resolve(db: Session, user_id: uuid.UUID, job_ai: dict | None = None) -> tuple[str | None, str, int]:
    """(provider or None when AI is off/unavailable, combined rules, hashtag count)."""
    prefs = get_prefs(db, user_id)
    job_ai = job_ai or {}
    use = job_ai.get("use_ai")
    use = prefs.use_ai if use is None else bool(use)
    available = configured()
    from app.billing import plan_of
    from app.models import User
    user = db.get(User, user_id)
    if user is not None and not user.is_admin and not plan_of(db, user_id).ai:
        available = []    # the plan doesn't include AI
    provider = None
    if use and available:
        for p in (prefs.provider, get_settings().ai_provider, *available):
            if p in available:
                provider = p
                break
    rules = "\n".join(r for r in (prefs.rules.strip(), (job_ai.get("rules") or "").strip()) if r)
    return provider, rules, prefs.hashtags


def record(db: Session, user_id: uuid.UUID, result, purpose: str) -> None:
    if result is None:
        return
    db.add(AIUsage(user_id=user_id, provider=result.provider, model=result.model[:64], purpose=purpose,
                   input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                   cached_tokens=result.cached_tokens))
