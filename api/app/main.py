from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routers import admin, admin_billing, ai, auth, billing, clips, editor, jobs, library, lives, push, social, styles

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Default plans/packs exist even on a fresh database (idempotent).
    from app.billing import seed
    from app.db import SessionLocal
    try:
        db = SessionLocal()
        seed(db)
        db.close()
    except Exception:   # tables not migrated yet: the migration seeds them
        pass
    yield


app = FastAPI(
    lifespan=lifespan,
    title=f"{settings.app_name} API",
    # Interactive docs only outside production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(editor.router)
app.include_router(clips.router)
app.include_router(lives.router)
app.include_router(push.router)
app.include_router(ai.router)
app.include_router(social.router)
app.include_router(billing.router)
app.include_router(admin_billing.router)
app.include_router(styles.router)
app.include_router(library.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {"ok": True}
