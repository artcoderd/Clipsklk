import uuid
from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator

from app.models import JobSource, JobStatus, Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    name: str
    role: Role
    is_active: bool
    totp_enabled: bool
    created_at: datetime
    last_login_at: datetime | None


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default="", max_length=120)
    invitation_token: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)
    totp_code: str | None = Field(default=None, max_length=12)


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str
    qr_data_uri: str


class TotpCodeIn(BaseModel):
    code: str = Field(max_length=12)


class InvitationLookupOut(BaseModel):
    email: str
    expires_at: datetime


class InvitationCreateIn(BaseModel):
    email: EmailStr
    role: Role = Role.user


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationCreatedOut(InvitationOut):
    link: str  # shown once, so the admin can copy it if email is not configured


class UserUpdateIn(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: uuid.UUID | None
    action: str
    detail: dict
    ip: str | None
    created_at: datetime


Rect = list[float]


class GameplayRects(BaseModel):
    cam: Rect | None = None    # [x, y, w, h], 0..1 of the source frame
    game: Rect | None = None

    @field_validator("cam", "game")
    @classmethod
    def _rect(cls, v):
        if v is None:
            return v
        if len(v) != 4 or not all(0 <= x <= 1 for x in v) or v[2] < 0.02 or v[3] < 0.02:
            raise ValueError("rect must be [x, y, w, h] within 0..1")
        return [round(x, 4) for x in v]


class RenderOptions(BaseModel):
    format: Literal["9:16", "4:5", "1:1", "16:9"] = "9:16"
    layout: Literal["auto", "follow", "split", "gameplay", "fit"] = "auto"
    framing: Literal["fixed", "smooth", "dynamic", "zoom"] = "smooth"
    subtitles: bool = True
    style_id: str = "preset:clasico"      # "preset:<key>" or a saved style's id
    music_id: uuid.UUID | None = None
    music_volume: float = Field(default=0.15, ge=0, le=1)
    logo_id: uuid.UUID | None = None
    logo_position: Literal["top-left", "top-right"] = "top-right"
    gameplay: GameplayRects | None = None
    person: int | None = Field(default=None, ge=0)


class AIOptions(BaseModel):
    use_ai: bool | None = None                       # None = the user's preference
    rules: str = Field(default="", max_length=2000)  # extra rules for this project only


class JobFromUrlIn(BaseModel):
    url: str = Field(max_length=2048)
    clip_count: int = Field(default=5, ge=1, le=30)
    clip_duration: int = Field(default=45, ge=10, le=180)
    render: RenderOptions = RenderOptions()
    ai: AIOptions = AIOptions()


_HEX = r"^#[0-9a-fA-F]{6}$"


class StyleConfig(BaseModel):
    font: Literal["montserrat-black", "montserrat-extrabold", "montserrat-bold", "bebas"] = "montserrat-black"
    size: int = Field(default=78, ge=30, le=140)
    text_color: str = Field(default="#FFFFFF", pattern=_HEX)
    highlight_color: str = Field(default="#FFE600", pattern=_HEX)
    outline_color: str = Field(default="#000000", pattern=_HEX)
    outline: int = Field(default=6, ge=0, le=12)
    shadow: int = Field(default=2, ge=0, le=8)
    uppercase: bool = True
    words_per_line: int = Field(default=3, ge=1, le=6)
    animation: Literal["none", "highlight", "pop"] = "pop"
    position: Literal["bottom", "middle", "top"] = "bottom"


class StyleIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    config: StyleConfig


class StyleOut(BaseModel):
    id: str
    name: str
    preset: bool
    config: StyleConfig


class LibraryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    name: str
    size: int
    created_at: datetime


class PersonOut(BaseModel):
    id: int
    seconds: float


class ClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    job_id: uuid.UUID
    index: int
    start: float
    end: float
    score: float
    reasons: list[str]
    text: str
    render: dict
    layout: str | None
    render_status: str
    version: int
    offset: float
    title: str
    caption: str
    hashtags: list[str]
    ai_provider: str | None
    cover_rev: str | None
    proxy_status: str
    edited: bool
    created_at: datetime

    @computed_field
    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)


class MoreClipsIn(BaseModel):
    count: int = Field(default=3, ge=1, le=20)
    person: int | None = Field(default=None, ge=0)   # only moments where this person talks


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str | None
    source: JobSource
    source_url: str | None
    platform: str | None
    original_filename: str | None
    clip_count: int
    clip_duration: int
    render: dict
    focus_person: int | None
    ai: dict
    ai_status: str | None
    status: JobStatus
    progress: int
    stage: str
    error: str | None
    created_at: datetime
    updated_at: datetime


class LiveIn(BaseModel):
    url: str = Field(max_length=2048)
    auto_clips: bool = True
    ai_review: bool = True
    ai: AIOptions = AIOptions()
    sensitivity: Literal["low", "normal", "high"] = "normal"
    clip_duration: int = Field(default=45, ge=15, le=180)
    final_clips: int = Field(default=5, ge=0, le=30)
    render: RenderOptions = RenderOptions()


class LiveUpdateIn(BaseModel):
    auto_clips: bool | None = None
    ai_review: bool | None = None
    sensitivity: Literal["low", "normal", "high"] | None = None


class ClipNowIn(BaseModel):
    seconds: int = Field(default=60, ge=10, le=180)


class LiveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: str
    platform: str | None
    status: str
    chat_status: str
    auto_clips: bool
    ai_review: bool
    sensitivity: str
    clip_duration: int
    final_clips: int
    job_id: uuid.UUID
    final_job_id: uuid.UUID | None
    recorded_seconds: float
    end_reason: str | None
    error: str | None
    started_at: datetime
    ended_at: datetime | None


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=2048)
    keys: dict[str, str]


class AIPrefsIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    use_ai: bool = True
    provider: Literal["anthropic", "openai", "gemini"] | None = None
    rules: str = Field(default="", max_length=2000)
    hashtags: int = Field(default=5, ge=0, le=15)


class ClipTextIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    caption: str | None = Field(default=None, max_length=2200)
    hashtags: list[str] | None = Field(default=None, max_length=30)


_ID = Field(min_length=1, max_length=24)


class EditTextIn(BaseModel):
    id: str = _ID
    text: str = Field(min_length=1, max_length=120)
    start: float
    end: float
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    size: int = Field(default=90, ge=20, le=220)
    color: str = Field(default="#FFFFFF", pattern=_HEX)
    style: Literal["title", "label", "outline"] = "title"
    box_color: str = Field(default="#000000", pattern=_HEX)


class EditImageIn(BaseModel):
    id: str = _ID
    library_id: uuid.UUID
    start: float
    end: float
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0.03, le=1)


class EditSoundIn(BaseModel):
    id: str = _ID
    library_id: uuid.UUID
    t: float
    volume: float = Field(default=1.0, ge=0, le=2)


class EditMusicIn(BaseModel):
    library_id: uuid.UUID
    volume: float = Field(default=0.15, ge=0, le=1)


class EditMarkerIn(BaseModel):
    id: str = _ID
    t: float
    label: str = Field(default="", max_length=40)


class EditDoc(BaseModel):
    """Times are seconds in the source video (see app/media/edit.py)."""
    trim: list[float] = Field(min_length=2, max_length=2)
    cuts: list[list[float]] = Field(default_factory=list, max_length=400)
    texts: list[EditTextIn] = Field(default_factory=list, max_length=40)
    images: list[EditImageIn] = Field(default_factory=list, max_length=20)
    sounds: list[EditSoundIn] = Field(default_factory=list, max_length=60)
    markers: list[EditMarkerIn] = Field(default_factory=list, max_length=100)
    music: EditMusicIn | None = None


class SocialAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    platform: str
    name: str
    picture: str | None
    status: str
    last_error: str | None
    created_at: datetime


class TelegramIn(BaseModel):
    bot_token: str = Field(min_length=10, max_length=200)
    chat_id: str = Field(min_length=1, max_length=64)


class PublishIn(BaseModel):
    account_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    caption: str = Field(default="", max_length=2200)
    scheduled_at: datetime | None = None       # None = now
    # TikTok: privacy_level (required for direct posts), allow_comment/duet/stitch
    options: dict = Field(default_factory=dict)


class PublicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    clip_id: uuid.UUID
    account_id: uuid.UUID
    status: str
    scheduled_at: datetime
    caption: str
    external_url: str | None
    error: str | None
    attempts: int
    published_at: datetime | None
