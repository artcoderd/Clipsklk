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
