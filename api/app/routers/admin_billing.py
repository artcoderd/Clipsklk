"""Owner/admin panel: plans and minutes per user, manual (WhatsApp) payments,
plans editor, active jobs, monthly costs and margin."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import activity
from app.billing import (DEFAULT_COSTS, balance, get_setting, grant, period_after, set_setting, signup_open,
                         subscription)
from app.db import get_db
from app.deps import require_admin, require_owner
from app.models import (AIUsage, CreditEntry, CreditPack, Job, JobStatus, MediaAsset, Plan, Subscription,
                        User)

router = APIRouter(prefix="/admin", tags=["admin"])


def _month_start() -> datetime:
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("/usage")
def users_usage(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Plan, minutes and consumption of every user."""
    since = datetime.now(timezone.utc) - timedelta(days=30)
    processed = dict(db.execute(select(MediaAsset.user_id, func.coalesce(func.sum(MediaAsset.duration), 0))
                                .where(MediaAsset.created_at >= since).group_by(MediaAsset.user_id)).all())
    jobs = dict(db.execute(select(Job.user_id, func.count()).where(Job.created_at >= since)
                           .group_by(Job.user_id)).all())
    active = dict(db.execute(select(Job.user_id, func.count()).where(
        Job.status.in_([JobStatus.queued, JobStatus.running])).group_by(Job.user_id)).all())
    out = []
    for u in db.scalars(select(User).order_by(User.created_at)).all():
        b = balance(db, u.id)
        sub = subscription(db, u.id)
        out.append({
            "user_id": str(u.id), "email": u.email, "name": u.name, "role": u.role.value, "is_active": u.is_active,
            "unlimited": u.is_admin, "plan_id": b.plan.id, "plan_name": b.plan.name, "source": sub.source,
            "status": sub.status, "period_end": b.period_end, "allowance": b.allowance, "used": round(b.used, 1),
            "credits": round(b.credits, 1), "left": round(b.total_left, 1),
            "minutes_30d": round(float(processed.get(u.id, 0)) / 60, 1), "jobs_30d": jobs.get(u.id, 0),
            "active_jobs": active.get(u.id, 0),
        })
    db.commit()
    return out


class ManualPlanIn(BaseModel):
    plan_id: str = Field(max_length=32)
    months: int = Field(default=1, ge=1, le=24)
    amount: str = Field(default="", max_length=40)       # what was paid, for your records ("MXN 350")
    note: str = Field(default="", max_length=200)


@router.post("/users/{user_id}/plan")
def set_plan(user_id: uuid.UUID, data: ManualPlanIn, request: Request, owner: User = Depends(require_owner),
             db: Session = Depends(get_db)):
    """Payment received by hand (e.g. WhatsApp transfer): activate a plan for N months."""
    target = db.get(User, user_id)
    plan = db.get(Plan, data.plan_id)
    if target is None or plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    sub = subscription(db, user_id)
    if sub.source == "stripe" and sub.stripe_subscription_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "has_stripe_subscription")
    now = datetime.now(timezone.utc)
    if plan.id == "free":
        sub.plan_id, sub.source, sub.period_end = "free", "free", None
        sub.period_start = _month_start()
    else:
        # Renewing the same plan before it ends extends it; anything else starts a fresh period.
        end = sub.period_end if sub.period_end and sub.period_end.tzinfo else (
            sub.period_end.replace(tzinfo=timezone.utc) if sub.period_end else None)
        if sub.source == "manual" and sub.plan_id == plan.id and end and end > now:
            sub.period_end = period_after(end, data.months)
        else:
            sub.period_start, sub.period_end = now, period_after(now, data.months)
        sub.plan_id, sub.source, sub.status = plan.id, "manual", "active"
    activity.log(db, "manual_plan", owner.id, request, target=str(user_id), plan=plan.id, months=data.months,
                 amount=data.amount, note=data.note)
    db.commit()
    return {"plan_id": sub.plan_id, "period_end": sub.period_end}


class MinutesIn(BaseModel):
    minutes: int = Field(ge=-100000, le=100000)
    amount: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=200)


@router.post("/users/{user_id}/minutes")
def add_minutes(user_id: uuid.UUID, data: MinutesIn, request: Request, owner: User = Depends(require_owner),
                db: Session = Depends(get_db)):
    if db.get(User, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if data.minutes == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "zero")
    grant(db, user_id, data.minutes, "manual", f"manual:{uuid.uuid4()}",
          data.note or ("Minutos añadidos" if data.minutes > 0 else "Minutos retirados"))
    activity.log(db, "manual_minutes", owner.id, request, target=str(user_id), minutes=data.minutes,
                 amount=data.amount, note=data.note)
    db.commit()
    return {"left": round(balance(db, user_id).total_left, 1)}


@router.get("/jobs/active")
def active_jobs(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(Job, User.email).join(User, User.id == Job.user_id)
                      .where(Job.status.in_([JobStatus.queued, JobStatus.running]))
                      .order_by(Job.created_at).limit(200)).all()
    return [{"id": str(j.id), "email": email, "title": j.title or j.original_filename or j.source_url,
             "source": j.source.value, "status": j.status.value, "stage": j.stage, "progress": j.progress,
             "created_at": j.created_at} for j, email in rows]


class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    price_label: str = Field(default="", max_length=40)
    price_cents: int = Field(default=0, ge=0, le=10_000_000)
    monthly_minutes: int = Field(ge=0, le=1_000_000)
    live: bool = False
    ai: bool = True
    max_active_jobs: int = Field(default=2, ge=1, le=50)
    stripe_price_id: str | None = Field(default=None, max_length=64)
    active: bool = True
    sort: int = 0


class PackIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    minutes: int = Field(ge=1, le=1_000_000)
    price_label: str = Field(default="", max_length=40)
    price_cents: int = Field(default=0, ge=0, le=10_000_000)
    stripe_price_id: str | None = Field(default=None, max_length=64)
    active: bool = True




def _cols(obj, names):
    return {n: getattr(obj, n) for n in names}


@router.get("/plans")
def list_plans(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {
        "plans": [{"id": p.id, **_cols(p, PlanIn.model_fields)} for p in db.scalars(select(Plan).order_by(Plan.sort))],
        "packs": [{"id": k.id, **_cols(k, PackIn.model_fields)} for k in
                  db.scalars(select(CreditPack).order_by(CreditPack.minutes))],
    }


def _check_id(item_id: str):
    import re
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", item_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_id")


@router.put("/plans/{plan_id}")
def save_plan(plan_id: str, data: PlanIn, request: Request, owner: User = Depends(require_owner),
              db: Session = Depends(get_db)):
    _check_id(plan_id)
    if plan_id == "free" and not data.active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "free_required")
    p = db.get(Plan, plan_id) or Plan(id=plan_id)
    for k, v in data.model_dump().items():
        setattr(p, k, (v or None) if k == "stripe_price_id" else v)
    db.add(p)
    activity.log(db, "plan_saved", owner.id, request, plan=plan_id)
    db.commit()
    return {"id": plan_id}


@router.put("/packs/{pack_id}")
def save_pack(pack_id: str, data: PackIn, request: Request, owner: User = Depends(require_owner),
              db: Session = Depends(get_db)):
    _check_id(pack_id)
    k = db.get(CreditPack, pack_id) or CreditPack(id=pack_id)
    for f, v in data.model_dump().items():
        setattr(k, f, (v or None) if f == "stripe_price_id" else v)
    db.add(k)
    activity.log(db, "pack_saved", owner.id, request, pack=pack_id)
    db.commit()
    return {"id": pack_id}


def _costs(db: Session) -> dict:
    return {**DEFAULT_COSTS, **(get_setting(db, "costs", {}) or {})}


@router.get("/settings")
def get_app_settings(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {"public_signup": signup_open(db), "costs": _costs(db)}


class SettingsIn(BaseModel):
    public_signup: bool | None = None
    costs: dict[str, float] | None = None


@router.put("/settings")
def put_app_settings(data: SettingsIn, request: Request, owner: User = Depends(require_owner),
                     db: Session = Depends(get_db)):
    if data.public_signup is not None:
        set_setting(db, "public_signup", data.public_signup)
    if data.costs is not None:
        clean = {k: max(0.0, float(v)) for k, v in data.costs.items() if k in DEFAULT_COSTS}
        set_setting(db, "costs", {**_costs(db), **clean})
    activity.log(db, "settings_saved", owner.id, request, **data.model_dump(exclude_none=True))
    db.commit()
    return get_app_settings(owner, db)


@router.get("/finance")
def finance(_: User = Depends(require_owner), db: Session = Depends(get_db)):
    """This month: estimated income (paid plans + packs), costs (server, AI...) and margin, in USD."""
    month = _month_start()
    costs = _costs(db)
    paid = db.execute(select(Plan.id, Plan.name, Plan.price_cents, Subscription.source, func.count())
                      .join(Subscription, Subscription.plan_id == Plan.id)
                      .where(Subscription.source.in_(("stripe", "manual")), Subscription.status == "active")
                      .group_by(Plan.id, Plan.name, Plan.price_cents, Subscription.source)).all()
    plans_income = sum(c * n for _, _, c, _, n in paid) / 100
    packs = {k.minutes: k.price_cents for k in db.scalars(select(CreditPack))}
    bought = db.execute(select(CreditEntry.minutes, func.count()).where(
        CreditEntry.kind == "purchase", CreditEntry.created_at >= month).group_by(CreditEntry.minutes)).all()
    packs_income = sum(packs.get(int(m), 0) * n for m, n in bought) / 100
    stripe_income = sum(c * n for _, _, c, src, n in paid if src == "stripe") / 100 + packs_income
    tok_in, tok_out = db.execute(select(func.coalesce(func.sum(AIUsage.input_tokens), 0),
                                        func.coalesce(func.sum(AIUsage.output_tokens), 0))
                                 .where(AIUsage.created_at >= month)).one()
    ai_cost = tok_in / 1e6 * costs["ai_per_million_input"] + tok_out / 1e6 * costs["ai_per_million_output"]
    fixed = costs["gpu_server"] + costs["storage"] + costs["domain_other"]
    fees = stripe_income * costs["stripe_percent"] / 100
    income = plans_income + packs_income
    processed = db.scalar(select(func.coalesce(func.sum(MediaAsset.duration), 0))
                          .where(MediaAsset.created_at >= month)) or 0
    total_cost = fixed + ai_cost + fees
    return {
        "month": month.strftime("%Y-%m"),
        "subscribers": [{"plan": n, "source": src, "count": c, "price": p / 100} for _, n, p, src, c in paid],
        "income": {"plans": round(plans_income, 2), "packs": round(packs_income, 2), "total": round(income, 2)},
        "costs": {"fixed": round(fixed, 2), "ai": round(ai_cost, 2), "payment_fees": round(fees, 2),
                  "total": round(total_cost, 2)},
        "margin": round(income - total_cost, 2),
        "minutes_processed": round(float(processed) / 60, 1),
        "cost_per_minute": round(total_cost / (float(processed) / 60), 4) if processed else None,
    }
