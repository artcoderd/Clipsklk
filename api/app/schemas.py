import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

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


class JobFromUrlIn(BaseModel):
    url: str = Field(max_length=2048)
    clip_count: int = Field(default=5, ge=1, le=30)
    clip_duration: int = Field(default=45, ge=10, le=180)


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
    created_at: datetime

    @computed_field
    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)


class MoreClipsIn(BaseModel):
    count: int = Field(default=3, ge=1, le=20)


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
    status: JobStatus
    progress: int
    stage: str
    error: str | None
    created_at: datetime
    updated_at: datetime
