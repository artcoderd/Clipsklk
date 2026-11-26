import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import activity, payments
from app.billing import balance, subscription, unlimited
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import CreditEntry, CreditPack, Plan, User

router = APIRouter(prefix="/billing", tags=["billing"])


def _plan(p: Plan) -> dict:
    return {"id": p.id, "name": p.name, "price_label": p.price_label, "monthly_minutes": p.monthly_minutes,
            "live": p.live, "ai": p.ai, "max_active_jobs": p.max_active_jobs, "buyable": bool(p.stripe_price_id)}


def _pack(k: CreditPack) -> dict:
    return {"id": k.id, "name": k.name, "minutes": k.minutes, "price_label": k.price_label,
            "buyable": bool(k.stripe_price_id)}


@router.get("/plans")
def public_plans(db: Session = Depends(get_db)):
    """Public (landing page): active plans and minute packs."""
    plans = db.scalars(select(Plan).where(Plan.active).order_by(Plan.sort)).all()
    packs = db.scalars(select(CreditPack).where(CreditPack.active).order_by(CreditPack.minutes)).all()
    return {"plans": [_plan(p) for p in plans], "packs": [_pack(k) for k in packs]}


@router.get("/me")
def my_billing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    b = balance(db, user.id)
    sub = subscription(db, user.id)
    db.commit()
    s = get_settings()
    wa = None
    if s.whatsapp_number:
        text = quote(f"Hola, quiero pagar un plan de Clipeo. Mi cuenta: {user.email}")
        wa = f"https://wa.me/{s.whatsapp_number}?text={text}"
    return {
        "unlimited": unlimited(user),
        "plan": _plan(b.plan), "source": sub.source, "status": sub.status,
        "period_start": b.period_start, "period_end": b.period_end, "cancel_at_period_end": sub.cancel_at_period_end,
        "allowance": b.allowance, "used": round(b.used, 1), "credits": round(b.credits, 1),
        "left": round(b.total_left, 1),
        "stripe": payments.enabled(), "can_manage": bool(sub.stripe_customer_id) and payments.enabled(),
        "whatsapp": wa,
    }


@router.get("/history")
def history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(CreditEntry).where(CreditEntry.user_id == user.id)
                      .order_by(CreditEntry.id.desc()).limit(60)).all()
    return [{"kind": r.kind, "minutes": round(r.minutes, 1), "note": r.note, "created_at": r.created_at} for r in rows]


class CheckoutIn(BaseModel):
    plan_id: str | None = Field(default=None, max_length=32)
    pack_id: str | None = Field(default=None, max_length=32)


@router.post("/checkout")
def checkout(data: CheckoutIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not payments.enabled():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "stripe_not_configured")
    item = db.get(Plan, data.plan_id) if data.plan_id else db.get(CreditPack, data.pack_id) if data.pack_id else None
    if item is None or not item.active or not item.stripe_price_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not_buyable")
    if isinstance(item, Plan):
        sub = subscription(db, user.id)
        if sub.source == "stripe" and sub.stripe_subscription_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "already_subscribed")   # change it in the portal
    try:
        url = payments.checkout_url(db, user, item)
    except Exception:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "stripe_error")
    activity.log(db, "checkout_started", user.id, request, item=item.id)
    db.commit()
    return {"url": url}


@router.post("/portal")
def portal(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = subscription(db, user.id)
    if not payments.enabled() or not sub.stripe_customer_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_stripe_customer")
    return {"url": payments.portal_url(sub.stripe_customer_id)}


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, db: Session = Depends(get_db)):
    """Called by Stripe. The signature proves it really came from Stripe."""
    payload = await request.body()
    try:
        event = payments.parse_event(payload, request.headers.get("stripe-signature"))
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_signature")
    return {"result": payments.handle_event(db, event)}
