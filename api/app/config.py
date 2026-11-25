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

    # Storage (phase 1: local disk; phase 2 adds S3/R2)
    storage_dir: str = "/data/storage"
    max_upload_mb: int = 4096

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and (s.secret_key == "change-me-in-production" or len(s.secret_key) < 32):
        raise RuntimeError("SECRET_KEY must be set (32+ chars) in production")
    return s
