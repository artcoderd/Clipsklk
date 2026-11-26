"""Stripe: Checkout for plans (subscription) and minute packs (one-time),
the customer portal, and the webhook that keeps plans and minutes in sync."""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import grant, subscription
from app.config import get_settings
from app.models import CreditPack, Plan, Subscription, User

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(get_settings().stripe_secret_key)


def _client():
    import stripe
    return stripe.StripeClient(get_settings().stripe_secret_key, max_network_retries=2)


def checkout_url(db: Session, user: User, item: Plan | CreditPack) -> str:
    s = get_settings()
    sub = subscription(db, user.id)
    web = s.web_url.rstrip("/")
    is_plan = isinstance(item, Plan)
    params = {
        "mode": "subscription" if is_plan else "payment",
        "line_items": [{"price": item.stripe_price_id, "quantity": 1}],
        "success_url": f"{web}/planes?pago=ok",
        "cancel_url": f"{web}/planes?pago=cancelado",
        "client_reference_id": str(user.id),
        "metadata": {"user_id": str(user.id), "kind": "plan" if is_plan else "pack", "item": item.id},
        "allow_promotion_codes": True,
    }
    if sub.stripe_customer_id:
        params["customer"] = sub.stripe_customer_id
    else:
        params["customer_email"] = user.email
    if is_plan:
        params["subscription_data"] = {"metadata": {"user_id": str(user.id), "plan": item.id}}
    session = _client().v1.checkout.sessions.create(params=params)
    return session.url


def portal_url(customer_id: str) -> str:
    web = get_settings().web_url.rstrip("/")
    return _client().v1.billing_portal.sessions.create(
        params={"customer": customer_id, "return_url": f"{web}/planes"}).url


def parse_event(payload: bytes, signature: str | None):
    """Verified event (raises on a bad signature: the request didn't come from Stripe)."""
    import stripe
    return stripe.Webhook.construct_event(payload, signature, get_settings().stripe_webhook_secret)


def _ts(v) -> datetime | None:
    return datetime.fromtimestamp(int(v), tz=timezone.utc) if v else None


def _get(obj, key, default=None):
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return default


def _apply_subscription(db: Session, sub_obj, deleted: bool = False) -> None:
    sub_id = _get(sub_obj, "id")
    row = db.scalar(select(Subscription).where(Subscription.stripe_subscription_id == sub_id))
    if row is None:
        uid = _get(_get(sub_obj, "metadata", {}), "user_id")
        if not uid:
            log.warning("stripe subscription %s without user", sub_id)
            return
        row = subscription(db, uuid.UUID(uid))
        if row.stripe_subscription_id and row.stripe_subscription_id != sub_id:
            return      # an older subscription of a user who already has a newer one
    items = _get(_get(sub_obj, "items", {}), "data", []) or []
    first = items[0] if items else {}
    price_id = _get(_get(first, "price", {}), "id")
    plan = db.scalar(select(Plan).where(Plan.stripe_price_id == price_id)) if price_id else None
    status = "canceled" if deleted else _get(sub_obj, "status", "active")
    row.source, row.stripe_subscription_id = "stripe", sub_id
    row.stripe_customer_id = _get(sub_obj, "customer") or row.stripe_customer_id
    if plan:
        row.plan_id = plan.id
    row.status = "canceled" if status in ("canceled", "incomplete_expired", "unpaid") else status
    # Recent API versions keep the period on the subscription items.
    start = _get(sub_obj, "current_period_start") or _get(first, "current_period_start")
    end = _get(sub_obj, "current_period_end") or _get(first, "current_period_end")
    if start:
        row.period_start = _ts(start)
    row.period_end = _ts(end)
    row.cancel_at_period_end = bool(_get(sub_obj, "cancel_at_period_end", False))
    if row.status == "canceled":
        row.plan_id, row.source, row.stripe_subscription_id = "free", "free", None
        row.period_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        row.period_end = None
        row.status = "active"


def handle_event(db: Session, event) -> str:
    kind = _get(event, "type")
    obj = _get(_get(event, "data", {}), "object", {})
    if kind == "checkout.session.completed":
        meta = _get(obj, "metadata", {}) or {}
        uid = _get(meta, "user_id")
        if not uid:
            return "ignored"
        user_id = uuid.UUID(uid)
        sub = subscription(db, user_id)
        sub.stripe_customer_id = _get(obj, "customer") or sub.stripe_customer_id
        if _get(meta, "kind") == "pack" and _get(obj, "payment_status") == "paid":
            pack = db.get(CreditPack, _get(meta, "item"))
            if pack:
                grant(db, user_id, pack.minutes, "purchase", f"stripe:{_get(obj, 'id')}", f"Compra: {pack.name}")
        elif _get(meta, "kind") == "plan" and _get(obj, "subscription"):
            sub.stripe_subscription_id = _get(obj, "subscription")
            plan = db.get(Plan, _get(meta, "item"))
            if plan:
                sub.plan_id, sub.source, sub.status = plan.id, "stripe", "active"
                sub.period_start = datetime.now(timezone.utc)
        db.commit()
        return "ok"
    if kind in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        _apply_subscription(db, obj, deleted=kind == "customer.subscription.deleted")
        db.commit()
        return "ok"
    return "ignored"
