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
    # ВАЖНО: воркер слушает только очереди "analysis" и "default" (см. его
    # стартовый лог [queues]). Celery по умолчанию шлёт задачи, для которых
    # нет явного маршрута, в очередь с именем "celery" — которую НИКТО не
    # слушает. Каждая новая задача из tasks.py должна быть явно перечислена
    # здесь, иначе она будет молча зависать в Redis навсегда (без ошибок,
    # без логов — просто ничего не произойдёт). CHANGED: добавлены
    # run_llm_analysis и apply_suggestion (обнаружено при отладке импорта
    # CRM + LLM-анализа — кнопка отвечала "started", но задача никогда не
    # выполнялась), а также collect_and_analyze_all и track_all_hypotheses
    # (периодические beat-задачи — та же дыра сработала бы по расписанию).
    task_routes={
        "app.core.tasks.collect_account_data": {"queue": "default"},
        "app.core.tasks.run_analysis": {"queue": "analysis"},
        "app.core.tasks.track_hypothesis": {"queue": "default"},
        "app.core.tasks.run_llm_analysis": {"queue": "default"},
        "app.core.tasks.apply_suggestion": {"queue": "default"},
        "app.core.tasks.collect_and_analyze_all": {"queue": "default"},
        "app.core.tasks.track_all_hypotheses": {"queue": "default"},
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
