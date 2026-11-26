import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import activity
from app.config import get_settings
from app.db import get_db
from app.deps import require_admin
from app.models import ActivityLog, AIUsage, Invitation, Job, JobStatus, Role, User
from app.schemas import (ActivityOut, InvitationCreatedOut, InvitationCreateIn, InvitationOut,
                         UserOut, UserUpdateIn)
from app.security import new_invitation_token

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats")
def stats(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    from sqlalchemy import func
    return {
        "users": db.scalar(select(func.count()).select_from(User)),
        "active_jobs": db.scalar(select(func.count()).select_from(Job)
                                 .where(Job.status.in_([JobStatus.queued, JobStatus.running]))),
        "jobs_total": db.scalar(select(func.count()).select_from(Job)),
        # AI tokens in the last 30 days, per provider (to watch the API bill).
        "ai_usage_30d": [
            {"provider": p, "model": m, "calls": n, "input_tokens": i or 0, "output_tokens": o or 0, "cached_tokens": c or 0}
            for p, m, n, i, o, c in db.execute(
                select(AIUsage.provider, AIUsage.model, func.count(), func.sum(AIUsage.input_tokens),
                       func.sum(AIUsage.output_tokens), func.sum(AIUsage.cached_tokens))
                .where(AIUsage.created_at >= datetime.now(timezone.utc) - timedelta(days=30))
                .group_by(AIUsage.provider, AIUsage.model))
        ],
    }


@router.get("/invitations", response_model=list[InvitationOut])
def list_invitations(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(200)).all()


@router.post("/invitations", response_model=InvitationCreatedOut, status_code=201)
def create_invitation(data: InvitationCreateIn, request: Request,
                      admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if data.role == Role.owner:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot_invite_owner")
    if data.role == Role.admin and admin.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only")
    email = data.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email_taken")

    s = get_settings()
    raw, token_hash = new_invitation_token()
    inv = Invitation(email=email, role=data.role, token_hash=token_hash, invited_by_id=admin.id,
                     expires_at=datetime.now(timezone.utc) + timedelta(days=s.invitation_ttl_days))
    db.add(inv)
    db.flush()
    activity.log(db, "invitation_created", admin.id, request, email=email, role=data.role.value)
    db.commit()
    link = f"{s.web_url.rstrip('/')}/register?token={raw}"
    # TODO(phase 8): send the link by email (SMTP / Resend). For now the admin copies it.
    return InvitationCreatedOut(**InvitationOut.model_validate(inv).model_dump(), link=link)


@router.delete("/invitations/{inv_id}", status_code=204)
def revoke_invitation(inv_id: uuid.UUID, request: Request, admin: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    inv = db.get(Invitation, inv_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if inv.used_at is None and inv.revoked_at is None:
        inv.revoked_at = datetime.now(timezone.utc)
        activity.log(db, "invitation_revoked", admin.id, request, email=inv.email)
        db.commit()


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.created_at)).all()


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, data: UserUpdateIn, request: Request,
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    # Nobody can change or disable the owner, and nobody can become owner.
    if target.role == Role.owner or data.role == Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_protected")
    # Only the owner manages admins (promoting, demoting or pausing them).
    touches_admin = target.role == Role.admin or data.role == Role.admin
    if touches_admin and admin.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only")

    changes = {}
    if data.role is not None and data.role != target.role:
        changes["role"] = [target.role.value, data.role.value]
        target.role = data.role
    if data.is_active is not None and data.is_active != target.is_active:
        changes["is_active"] = [target.is_active, data.is_active]
        target.is_active = data.is_active
    if changes:
        activity.log(db, "user_updated", admin.id, request, target=str(target.id), changes=changes)
    db.commit()
    return target


@router.get("/activity", response_model=list[ActivityOut])
def list_activity(limit: int = 100, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    limit = max(1, min(limit, 500))
    return db.scalars(select(ActivityLog).order_by(ActivityLog.id.desc()).limit(limit)).all()
