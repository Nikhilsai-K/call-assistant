from celery import Celery

from .config import settings

celery_app = Celery(
    "vocalflow",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "vocalflow_worker.tasks.postcall",
        "vocalflow_worker.tasks.kb_index",
        "vocalflow_worker.tasks.stripe_events",
        "vocalflow_worker.tasks.crm_sync",
        "vocalflow_worker.tasks.shadow_mode",
    ],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_max_tasks_per_child=200,
    beat_schedule={
        "redrive-postcall-stream": {
            "task": "vocalflow_worker.tasks.postcall.redrive",
            "schedule": 10.0,
        },
        "redrive-kb-index": {
            "task": "vocalflow_worker.tasks.kb_index.redrive",
            "schedule": 10.0,
        },
    },
)
