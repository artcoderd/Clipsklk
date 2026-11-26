import uuid


def _send(name: str, arg: uuid.UUID) -> None:
    # Sent by task name so the API process never imports the worker's heavy
    # dependencies (numpy, OpenCV, Whisper, yt-dlp).
    from app.worker.celery_app import celery_app
    celery_app.send_task(name, args=[str(arg)])


def enqueue_job(job_id: uuid.UUID) -> None:
    _send("app.worker.tasks.process_job", job_id)


def enqueue_rerender(clip_id: uuid.UUID) -> None:
    _send("app.worker.tasks.rerender_clip", clip_id)


def enqueue_live(session_id: uuid.UUID) -> None:
    _send("app.worker.tasks.run_live", session_id)


def enqueue_live_clip(clip_id: uuid.UUID) -> None:
    _send("app.worker.tasks.make_live_clip", clip_id)


def enqueue_live_review(session_id, asset_id, offset: float) -> None:
    from app.worker.celery_app import celery_app
    celery_app.send_task("app.worker.tasks.live_ai_review", args=[str(session_id), str(asset_id), float(offset)])


def enqueue_cover(clip_id) -> None:
    _send("app.worker.tasks.refresh_cover", clip_id)


def enqueue_proxy(clip_id) -> None:
    _send("app.worker.tasks.prepare_proxy", clip_id)
