import uuid


def enqueue_job(job_id: uuid.UUID) -> None:
    """Hand a job to the video workers. Sent by task name so the API process
    never imports the worker's heavy dependencies (numpy, Whisper, yt-dlp)."""
    from app.worker.celery_app import celery_app
    celery_app.send_task("app.worker.tasks.process_job", args=[str(job_id)])
