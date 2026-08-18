from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery("vayonis", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
celery_app.conf.task_default_queue = "default"

celery_app.autodiscover_tasks(["app.workers"])

celery_app.conf.beat_schedule = {
    "refresh-expiring-tokens-daily": {
        "task": "refresh_expiring_tokens",
        "schedule": crontab(hour=3, minute=0),
    },
}