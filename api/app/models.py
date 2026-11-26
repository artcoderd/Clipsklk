import enum
import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    user = "user"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    canceled = "canceled"


class JobSource(str, enum.Enum):
    url = "url"
    upload = "upload"
    live = "live"     # clips made during a live stream / its final recording


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), default=Role.user)
    is_active: Mapped[bool] = mapped_column(default=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    jobs: Mapped[list["Job"]] = relationship(back_populates="user")

    @property
    def is_admin(self) -> bool:
        return self.role in (Role.owner, Role.admin)


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), default=Role.user)
    # Only the SHA-256 of the token is stored; the raw token lives only in the link.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MediaAsset(Base):
    """A source video stored once. Every job/clip request for the same link (per
    user) reuses it, together with its transcript and audio analysis."""
    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("user_id", "source_key", name="uq_asset_user_source"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # Canonical URL for links, "upload:<job id>" for uploads.
    source_key: Mapped[str] = mapped_column(String(600))
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_video: Mapped[bool] = mapped_column(default=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    transcript_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    analysis_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    vision_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # People found on screen: [{id, seconds, thumb_key}], most visible first.
    people: Mapped[list | None] = mapped_column(JSON, nullable=True)
    preview_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[JobSource] = mapped_column(Enum(JobSource, name="job_source"))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id"), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    clip_count: Mapped[int] = mapped_column(Integer, default=5)
    clip_duration: Mapped[int] = mapped_column(Integer, default=45)
    # Default render options for this project's clips (see schemas.RenderOptions).
    render: Mapped[dict] = mapped_column(JSON, default=dict)
    # "Clips of one person": the person id the next clips must focus on.
    focus_person: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # AI options for this project: {"use_ai": bool, "rules": str}
    ai: Mapped[dict] = mapped_column(JSON, default=dict)
    # How the last selection went: "ai:<provider>" or "fallback:<reason>" (shown to the user).
    ai_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status"), default=JobStatus.queued)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="jobs")
    asset: Mapped[MediaAsset | None] = relationship()
    clips: Mapped[list["Clip"]] = relationship(back_populates="job", order_by="Clip.index")


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    text: Mapped[str] = mapped_column(Text, default="")
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    render: Mapped[dict] = mapped_column(JSON, default=dict)
    layout: Mapped[str | None] = mapped_column(String(16), nullable=True)   # the one actually used
    render_status: Mapped[str] = mapped_column(String(16), default="ready")  # ready | rendering | failed
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Live clips have their own short source (the stretch of the stream around them)...
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id"), nullable=True)
    # ...and `offset` is where that source starts inside the whole broadcast.
    offset: Mapped[float] = mapped_column(Float, default=0.0)
    # Publishing: cover title, text and hashtags (AI or from the clip's words).
    title: Mapped[str] = mapped_column(String(120), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    cover_t: Mapped[float | None] = mapped_column(Float, nullable=True)   # seconds from the clip start
    cover_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ai_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Web editor: the saved edit (schemas.EditDoc) and the light preview copy it plays.
    edit: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proxy_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    proxy_start: Mapped[float | None] = mapped_column(Float, nullable=True)   # source seconds it covers
    proxy_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    proxy_sig: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proxy_status: Mapped[str] = mapped_column(String(16), default="none")       # none | preparing | ready | failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="clips")

    @property
    def edited(self) -> bool:
        return bool(self.edit)

    @property
    def cover_rev(self) -> str | None:
        """Changes whenever the cover image is redrawn (for cache-busting URLs)."""
        return hashlib.sha1(self.cover_key.encode()).hexdigest()[:10] if self.cover_key else None


class SubtitleStyle(Base):
    __tablename__ = "subtitle_styles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LibraryItem(Base):
    """A user's reusable files: logos (watermark) and background music."""
    __tablename__ = "library_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))   # logo | music | image | sound
    name: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class LiveSession(Base):
    __tablename__ = "live_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # starting | waiting | recording | reconnecting | finishing | ended | failed
    status: Mapped[str] = mapped_column(String(16), default="starting")
    chat_status: Mapped[str] = mapped_column(String(16), default="none")   # none | connected | reconnecting
    auto_clips: Mapped[bool] = mapped_column(default=True)
    ai_review: Mapped[bool] = mapped_column(default=True)   # AI looks at the last minutes periodically
    sensitivity: Mapped[str] = mapped_column(String(8), default="normal")   # low | normal | high
    clip_duration: Mapped[int] = mapped_column(Integer, default=45)          # automatic clips
    final_clips: Mapped[int] = mapped_column(Integer, default=5)            # best clips at the end
    render: Mapped[dict] = mapped_column(JSON, default=dict)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))        # holds the live clips
    final_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    recorded_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PushSubscription(Base):
    """A browser/phone that accepted notifications (Web Push)."""
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIPrefs(Base):
    """Each user's AI preferences: their own rules are added to the viral ones."""
    __tablename__ = "ai_prefs"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    use_ai: Mapped[bool] = mapped_column(default=True)
    provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rules: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[int] = mapped_column(Integer, default=5)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AIUsage(Base):
    """Tokens spent per AI call, for the admin panel's cost view."""
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(16))      # select | caption | live
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
