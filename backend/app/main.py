from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.db.database import init_db
from app.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Инициализация БД и seed данных при старте
    await init_db()
    await seed_default_rules()
    yield


app = FastAPI(
    title="PPC Optimizer API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


async def seed_default_rules():
    """Заполнить базовые правила из воркфлоу при первом запуске"""
    from app.db.database import AsyncSessionLocal
    from app.models.models import Rule
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as db:
        count = await db.execute(select(func.count(Rule.id)))
        if count.scalar() > 0:
            return  # Правила уже есть

        default_rules = [
            Rule(
                name="Высокий CR — поднять ставку",
                condition_type="cr_high",
                min_clicks=30,
                cr_min=0.15,
                action_type="bid_raise",
                priority="today",
                description="CR > 15% при 30+ кликах. Формула: CPL_цель × CR_ключа",
            ),
            Rule(
                name="Нормальный CR — держать",
                condition_type="cr_mid",
                min_clicks=30,
                cr_min=0.05,
                cr_max=0.15,
                action_type="bid_hold",
                priority="this_week",
                description="CR 5–15%. Стабилизировать позиции.",
            ),
            Rule(
                name="Низкий CR при достаточных данных — CPA",
                condition_type="cr_low_cpa",
                min_clicks=100,
                cr_min=0.01,
                cr_max=0.05,
                action_type="strategy_cpa",
                priority="this_week",
                description="CR 1–5% при 100+ кликах. Алгоритм CPA оптимизирует сам.",
            ),
            Rule(
                name="Очень низкий CR — минус-слова",
                condition_type="cr_critical",
                min_clicks=100,
                cr_max=0.01,
                action_type="add_negatives",
                priority="today",
                description="CR < 1% при 100+ кликах. Информационный трафик.",
            ),
            Rule(
                name="CPQL превышает цель в 1.5×",
                condition_type="cpql_over_target",
                min_clicks=30,
                cpql_multiplier=1.5,
                action_type="bid_lower",
                action_params={"reduce_percent": 20},
                priority="this_week",
                description="Лиды слишком дорогие. Снизить ставку на 20%.",
            ),
            Rule(
                name="Высокий CR — масштабировать семантику",
                condition_type="cr_scale",
                min_clicks=30,
                cr_min=0.15,
                action_type="expand_semantics",
                priority="scale",
                description="CR > 15% подтверждён. Расширить кластер смежными ключами.",
            ),
        ]
        for rule in default_rules:
            db.add(rule)
        await db.commit()
