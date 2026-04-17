from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.db.database import init_db
from app.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    allow_origins=["*"],   # Railway handles SSL termination; restrict per-env if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes at /api/v1/...
app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health():
    """Health check — DB ping included"""
    from app.db.database import get_db
    try:
        from sqlalchemy import text
        async for db in get_db():
            await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = str(e)[:80]
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "version": "1.0.0",
    }


async def seed_default_rules():
    """Заполнить базовые правила при первом запуске"""
    try:
        from app.db.database import AsyncSessionLocal
        from app.models.models import Rule
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            existing = await db.execute(select(Rule).limit(1))
            if existing.scalar_one_or_none():
                return
            rules = [
                Rule(
                    account_id=None,
                    name="Низкая позиция показа",
                    rule_type="signal",
                    condition={"metric": "avg_position", "operator": ">", "value": 3, "min_clicks": 5},
                    action={"type": "suggest_bid_increase", "increase_pct": 30},
                    priority=1,
                    is_active=True,
                    description="Если позиция показа > 3 при ≥5 кликах → поднять ставку на 30%",
                ),
                Rule(
                    account_id=None,
                    name="Падение трафика",
                    rule_type="signal",
                    condition={"metric": "clicks_delta", "operator": "<", "value": -30, "min_traffic": 50},
                    action={"type": "suggest_bid_increase", "increase_pct": 20},
                    priority=2,
                    is_active=True,
                    description="Если клики упали > 30% при объёме трафика > 50 → поднять ставку",
                ),
                Rule(
                    account_id=None,
                    name="CTR = 0 при показах",
                    rule_type="signal",
                    condition={"metric": "ctr", "operator": "==", "value": 0, "min_impressions": 100},
                    action={"type": "flag_ad_issue"},
                    priority=3,
                    is_active=True,
                    description="Если CTR = 0 при ≥100 показах → проблема с объявлением",
                ),
                Rule(
                    account_id=None,
                    name="Позиция клика хуже показа",
                    rule_type="signal",
                    condition={"metric": "click_position_gap", "operator": ">", "value": 1.5},
                    action={"type": "flag_ctr_issue"},
                    priority=4,
                    is_active=True,
                    description="Если поз.клика хуже поз.показа на 1.5+ → объявление не цепляет аудиторию",
                ),
            ]
            for r in rules:
                db.add(r)
            await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Could not seed rules: {e}")
