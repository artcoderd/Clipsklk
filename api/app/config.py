from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Clipeo"
    environment: str = "development"  # development | production
    # Public URL of the web app, used to build invitation links.
    web_url: str = "http://localhost:3000"

    database_url: str = "postgresql+psycopg://clipeo:clipeo@localhost:5432/clipeo"
    redis_url: str = "redis://localhost:6379/0"

    # Secrets: always provided via environment / server secrets, never committed.
    secret_key: str = "change-me-in-production"
    session_cookie_name: str = "clipeo_session"
    session_ttl_hours: int = 24 * 7

    # Access is invitation-only unless the owner explicitly opens it.
    public_signup: bool = False
    invitation_ttl_days: int = 7

    # Abuse protection
    login_max_attempts: int = 5
    login_window_seconds: int = 15 * 60
    max_active_jobs_per_user: int = 3

    # Storage: "local" (shared disk/volume) or "s3" (Cloudflare R2, AWS S3, MinIO...)
    storage_backend: str = "local"
    storage_dir: str = "/data/storage"
    s3_bucket: str = ""
    s3_endpoint_url: str | None = None  # R2: https://<account_id>.r2.cloudflarestorage.com
    s3_region: str = "auto"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_presign_ttl_seconds: int = 3600
    max_upload_mb: int = 4096

    # Worker / video pipeline
    work_dir: str = "/data/work"         # scratch space on the worker
    max_source_hours: float = 6.0
    whisper_model: str = "auto"          # auto = large-v3 on GPU, small on CPU
    whisper_device: str = "auto"         # auto | cuda | cpu
    whisper_compute_type: str = "auto"   # auto = float16 on GPU, int8 on CPU
    video_encoder: str = "auto"          # auto = h264_nvenc when it works, else libx264
    ytdlp_cookies_file: str | None = None

    # Live mode
    live_dir: str = "/data/live"         # segments, shared by live and video workers
    live_max_hours: float = 12.0
    live_offline_grace_minutes: float = 10.0   # give up after this long without video
    live_max_auto_clips_per_hour: int = 12
    max_lives_per_user: int = 1
    # Also accept plain .m3u8 links (your own encoder/CDN). Off by default.
    live_allow_generic_hls: bool = False
    twitch_irc_host: str = "irc.chat.twitch.tv"
    twitch_irc_port: int = 6697
    twitch_irc_tls: bool = True

    # AI clip selection (only the transcript text is sent). Keys are secrets: .env only.
    ai_provider: str = "anthropic"          # default provider: anthropic | openai | gemini
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    ai_model_anthropic: str = "claude-opus-5-5"
    ai_model_openai: str = "gpt-5"
    ai_model_gemini: str = "gemini-2.5-pro"
    ai_effort: str = "medium"               # Claude: low | medium | high
    live_ai_review_minutes: float = 5.0     # live: AI looks at the last minutes this often

    # Publishing to social networks. App credentials come from each developer
    # portal (Meta for Developers, TikTok for Developers); they are secrets.
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_graph_version: str = "v23.0"
    tiktok_client_key: str | None = None
    tiktok_client_secret: str | None = None
    # "inbox": the video lands in the creator's TikTok drafts to finish there (works before
    # TikTok audits the app). "direct": posts directly (needs the audited Direct Post scope).
    tiktok_post_mode: str = "inbox"
    # Telegram Bot API server. A self-hosted one (github.com/tdlib/telegram-bot-api)
    # raises the bot upload limit from 50 MB to 2 GB.
    telegram_api_base: str = "https://api.telegram.org"
    # Key for encrypting the social tokens at rest; derived from SECRET_KEY if unset.
    token_encryption_key: str | None = None

    # Payments. Stripe keys from dashboard.stripe.com (secret!). Webhook endpoint:
    # <WEB_URL>/api/billing/webhook with events checkout.session.completed and
    # customer.subscription.created/updated/deleted.
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    # Manual payments by WhatsApp (international format, digits only), e.g. 5215512345678.
    whatsapp_number: str | None = None

    # Outgoing email (invitations). Without SMTP the panel shows the link to copy.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_tls: bool = True

    # Web push (generate with: python -m app.cli vapid-keys)
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:admin@example.com"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and (s.secret_key == "change-me-in-production" or len(s.secret_key) < 32):
        raise RuntimeError("SECRET_KEY must be set (32+ chars) in production")
    return s
