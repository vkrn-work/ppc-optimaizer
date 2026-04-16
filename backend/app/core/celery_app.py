from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

celery_app = Celery(
    "ppc_optimizer",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.core.tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Moscow",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.core.tasks.collect_account_data": {"queue": "default"},
        "app.core.tasks.run_analysis": {"queue": "analysis"},
        "app.core.tasks.track_hypothesis": {"queue": "default"},
    },
    beat_schedule={
        # Каждый понедельник в 6:00 МСК — сбор и анализ всех активных кабинетов
        "daily-collect-and-analyze": {
            "task": "app.core.tasks.collect_and_analyze_all",
            "schedule": crontab(hour=6, minute=0),
        },
        # Ежедневно в 7:00 — трекинг гипотез
        "daily-hypothesis-tracking": {
            "task": "app.core.tasks.track_all_hypotheses",
            "schedule": crontab(hour=7, minute=0),
        },
    },
)
