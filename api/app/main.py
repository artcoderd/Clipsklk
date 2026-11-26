from fastapi import FastAPI

from app.config import get_settings
from app.routers import admin, ai, auth, clips, editor, jobs, library, lives, push, styles

settings = get_settings()

app = FastAPI(
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
app.include_router(styles.router)
app.include_router(library.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {"ok": True}
