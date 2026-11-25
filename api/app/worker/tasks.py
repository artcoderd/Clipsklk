import time
import uuid

from app import events
from app.db import SessionLocal
from app.models import Job, JobStatus
from app.schemas import JobOut
from app.worker.celery_app import celery_app

# Phase 1 pipeline skeleton. Each stage gets its real implementation in later
# phases (download/transcribe/detect/cut in phase 2, reframe+subtitles in 3...).
STAGES = [
    ("fetching", 15),
    ("transcribing", 45),
    ("detecting_moments", 70),
    ("cutting", 90),
]


def _update(db, job: Job, **fields) -> None:
    for k, v in fields.items():
        setattr(job, k, v)
    db.commit()
    events.publish(job.id, JobOut.model_validate(job).model_dump(mode="json"))


@celery_app.task(name="app.worker.tasks.process_job", bind=True)
def process_job(self, job_id: str, stage_delay: float = 1.0) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None or job.status not in (JobStatus.queued, JobStatus.running):
            return
        _update(db, job, status=JobStatus.running, stage="starting", progress=1)
        try:
            for stage, progress in STAGES:
                _update(db, job, stage=stage, progress=progress)
                time.sleep(stage_delay)  # placeholder for real work
            _update(db, job, status=JobStatus.done, stage="done", progress=100)
        except Exception as exc:  # report any failure to the user instead of hanging
            db.rollback()
            _update(db, job, status=JobStatus.failed, stage="failed", error=str(exc)[:500])
            raise
    finally:
        db.close()
