from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai.prefs import get_prefs
from app.ai.prompts import SYSTEM
from app.ai.providers import LABELS, configured, model_for
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import AIPrefs, User
from app.schemas import AIPrefsIO

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
def status(user: User = Depends(get_current_user)):
    """Which AI providers the server has keys for (keys themselves never leave the server)."""
    avail = configured()
    return {
        "available": [{"id": p, "label": LABELS[p], "model": model_for(p)} for p in avail],
        "default": get_settings().ai_provider if get_settings().ai_provider in avail else (avail[0] if avail else None),
        "viral_rules": SYSTEM,
    }


@router.get("/prefs", response_model=AIPrefsIO)
def read_prefs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_prefs(db, user.id)


@router.put("/prefs", response_model=AIPrefsIO)
def save_prefs(data: AIPrefsIO, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefs = db.get(AIPrefs, user.id) or AIPrefs(user_id=user.id)
    prefs.use_ai, prefs.provider, prefs.rules, prefs.hashtags = data.use_ai, data.provider, data.rules.strip(), data.hashtags
    db.add(prefs)
    db.commit()
    return prefs
