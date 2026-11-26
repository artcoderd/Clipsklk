from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("clipeo", broker=settings.redis_url, backend=settings.redis_url,
                    include=["app.worker.tasks"])
celery_app.conf.update(
    task_acks_late=True,            # don't lose a job if a worker dies mid-way
    worker_prefetch_multiplier=1,   # GPU jobs are long; take one at a time
    task_track_started=True,
    task_routes={
        "app.worker.tasks.run_live": {"queue": "live"},   # long, light (CPU): recording + detection
        "app.worker.tasks.*": {"queue": "video"},         # heavy (GPU): transcription + render
    },
    task_time_limit=8 * 3600,        # hard stop for runaway jobs (sources are capped at 6 h)
    broker_transport_options={"visibility_timeout": 9 * 3600},  # must exceed the longest job
    worker_max_tasks_per_child=50,
)
