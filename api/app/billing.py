"""Plans, minutes and limits.

Each user has a plan (free by default) with an allowance of minutes per
period (minutes of source video processed), plus purchased minutes that
don't expire. Usage takes the plan minutes first, then the purchased ones.
Owners and admins are never charged or limited.
"""
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AppSetting, CreditEntry, Plan, Subscription, User

DEFAULT_PLANS = [
    dict(id="free", name="Gratis", price_label="Gratis", price_cents=0, monthly_minutes=60, live=False, ai=True,
         max_active_jobs=1, sort=0),
    dict(id="creador", name="Creador", price_label="US$19/mes", price_cents=1900, monthly_minutes=600, live=True,
         ai=True, max_active_jobs=3, sort=1),
    dict(id="pro", name="Pro", price_label="US$49/mes", price_cents=4900, monthly_minutes=2400, live=True, ai=True,
         max_active_jobs=5, sort=2),
]
DEFAULT_PACKS = [
    dict(id="m60", name="60 minutos", minutes=60, price_label="US$5", price_cents=500),
    dict(id="m300", name="300 minutos", minutes=300, price_label="US$20", price_cents=2000),
]


class NoMinutes(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def unlimited(user: User) -> bool:
    return user.is_admin


def subscription(db: Session, user_id: uuid.UUID) -> Subscription:
    """The user's subscription, rolling the period forward and expiring paid
    plans that ended (back to free) as needed."""
    sub = db.get(Subscription, user_id)
    now = _now()
    if sub is None:
        sub = Subscription(user_id=user_id, plan_id="free", source="free", status="active",
                           period_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
        db.add(sub)
        db.flush()
    end = _aware(sub.period_end)
    if sub.source == "manual" and end and end < now:
        sub.plan_id, sub.source, sub.period_end = "free", "free", None   # paid by hand, time's up
        sub.period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif sub.source == "free":
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if _aware(sub.period_start) < month:
            sub.period_start = month                                       # new month, new allowance
    return sub


def plan_of(db: Session, user_id: uuid.UUID) -> Plan:
    sub = subscription(db, user_id)
    plan = db.get(Plan, sub.plan_id) if sub.status in ("active", "trialing", "past_due") else None
    return plan or db.get(Plan, "free") or Plan(**DEFAULT_PLANS[0])


@dataclass
class Balance:
    plan: Plan
    allowance: int          # plan minutes this period
    used: float             # plan minutes used this period
    credits: float          # purchased/granted minutes left (never expire)
    period_start: datetime
    period_end: datetime | None

    @property
    def plan_left(self) -> float:
        return max(0.0, self.allowance - self.used)

    @property
    def total_left(self) -> float:
        return self.plan_left + self.credits


def balance(db: Session, user_id: uuid.UUID) -> Balance:
    sub = subscription(db, user_id)
    plan = plan_of(db, user_id)
    used = -(db.scalar(select(func.coalesce(func.sum(CreditEntry.minutes), 0)).where(
        CreditEntry.user_id == user_id, CreditEntry.kind == "usage",
        CreditEntry.created_at >= sub.period_start)) or 0)
    credits = db.scalar(select(func.coalesce(func.sum(CreditEntry.minutes), 0)).where(
        CreditEntry.user_id == user_id, CreditEntry.kind.in_(("purchase", "manual", "refund", "usage_credits")))) or 0
    return Balance(plan, plan.monthly_minutes, float(used), max(0.0, float(credits)), _aware(sub.period_start),
                   _aware(sub.period_end))


def charge(db: Session, user: User, minutes: float, ref: str, note: str = "") -> bool:
    """Charges `minutes` (plan first, then credits). Idempotent per `ref`.
    Raises NoMinutes if there isn't enough; returns False if already charged."""
    if unlimited(user) or minutes <= 0:
        return True
    if db.scalar(select(CreditEntry.id).where(CreditEntry.user_id == user.id, CreditEntry.ref.in_(
            (ref, ref + ":credits")))):
        return False
    b = balance(db, user.id)
    if b.total_left + 1e-6 < minutes:
        raise NoMinutes(f"need {minutes:.1f}, have {b.total_left:.1f}")
    from_plan = min(minutes, b.plan_left)
    from_credits = minutes - from_plan
    if from_plan > 0:
        db.add(CreditEntry(user_id=user.id, kind="usage", minutes=-from_plan, ref=ref, note=note[:200]))
    if from_credits > 0:
        db.add(CreditEntry(user_id=user.id, kind="usage_credits", minutes=-from_credits, ref=ref + ":credits",
                           note=note[:200]))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return False
    return True


def minutes_for(seconds: float) -> int:
    return max(1, math.ceil(seconds / 60))


def grant(db: Session, user_id: uuid.UUID, minutes: float, kind: str, ref: str | None, note: str) -> bool:
    """Adds minutes (purchase/manual/refund). Idempotent per (kind, ref)."""
    if ref and db.scalar(select(CreditEntry.id).where(CreditEntry.kind == kind, CreditEntry.ref == ref)):
        return False
    db.add(CreditEntry(user_id=user_id, kind=kind, minutes=minutes, ref=ref, note=note[:200]))
    db.flush()
    return True


def seed(db: Session) -> None:
    for p in DEFAULT_PLANS:
        if db.get(Plan, p["id"]) is None:
            db.add(Plan(**p))
    from app.models import CreditPack
    for k in DEFAULT_PACKS:
        if db.get(CreditPack, k["id"]) is None:
            db.add(CreditPack(**k))
    db.commit()


def get_setting(db: Session, key: str, default=None):
    row = db.get(AppSetting, key)
    return row.value.get("v", default) if row else default


def set_setting(db: Session, key: str, value) -> None:
    row = db.get(AppSetting, key) or AppSetting(key=key, value={})
    row.value = {"v": value}
    db.add(row)


def period_after(start: datetime, months: int) -> datetime:
    return start + timedelta(days=30 * months)


def signup_open(db: Session) -> bool:
    """Public sign-up: off by default; the owner turns it on from the panel."""
    from app.config import get_settings
    return bool(get_setting(db, "public_signup", get_settings().public_signup))


DEFAULT_COSTS = {"gpu_server": 0.0, "storage": 0.0, "domain_other": 0.0, "ai_per_million_input": 3.0,
                 "ai_per_million_output": 15.0, "stripe_percent": 2.9}
