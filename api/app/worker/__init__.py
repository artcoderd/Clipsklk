import uuid


def enqueue_job(job_id: uuid.UUID) -> None:
    """Hand a job to the Celery workers. Imported lazily so the API process
    doesn't need the worker's heavy dependencies."""
    from app.worker.tasks import process_job
    process_job.delay(str(job_id))
