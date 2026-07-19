"""
Единая точка вызова LLM-провайдера. Пользователь выбирает провайдера на фронте
перед запуском анализа (Claude / Gemini / Groq / OpenRouter) — это позволяет
бесплатно гонять весь пайплайн на Gemini/Groq/OpenRouter и переключиться на
 Claude только когда логика проверена.

Все 4 провайдера получают ОДИНАКОВЫЕ system prompt и датасет и должны вернуть
строго типизированный JSON {"changes": [...]} — либо через tool/function calling
(Claude, Groq, OpenRouter — OpenAI-совместимый tool_choice), либо через structured output
(Gemini — response_schema).

v1.5.0: в промпте объяснено поле bid_editable из датасета — на реальном apply
выяснилось, что в этом кабинете нет ни одной кампании на ручных ставках —
для авто-стратегий/ЕПК Yandex Direct API вообще не принимает Keywords[].Bid.
Код в llm_analyzer.py теперь жёстко отклоняет bid_raise/bid_lower для таких
ключей даже если модель не послушается этому промпту — но честнее сразу не
тратить бюджет вызова на заведомо неприменимые предложения.

v1.5.2: на реальном apply add_negatives модель (Groq) вернула минус-слова с
приклеенным "-" ("-hard", "-ru") — скопировала формат фраз ключей из датасета,
где минус-слова уже хранятся с дефисом. Direct API такое отклоняет (ошибка
5002 — дефис в начале/конце слова недопустим). Явно прописано в промпте ниже;
дополнительно очищается в коде (direct_writer.py) как защита от повторения.

v1.6.0: добавлен call_command_agent() — отдельный режим для страницы «Задачи
ИИ»: пользователь даёт свободную команду («добавь в рекламу такую-то сталь»),
модель генерирует список ключевых фраз + минус-слова + целевую группу объявлений
из числа реально существующих. Отдельная схема/промпт от основного анализа
— чтобы не трогать уже проверенный пайплайн call_llm()/CHANGES_SCHEMA.
"""
import json
import logging
from typing import Optional

import httpx

from app.core.config import settings
from app.analyzers.yandex_direct_knowledge import get_llm_knowledge_block

logger = logging.getLogger(__name__)

PROVIDERS = ("claude", "gemini", "groq", "openrouter")

# JSON Schema для {"changes": [...]} — общий для всех провайдеров.
CHANGES_SCHEMA = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword_id": {"type": "integer", "description": "id ключевого слова из данных"},
                    "phrase": {"type": "string"},
                    "change_type": {
                        "type": "string",
                        "enum": ["bid_raise", "bid_lower", "add_negatives", "pause", "ad_rewrite", "ad_test"],
                    },
                    "current_value": {"type": "string", "description": "текущее значение (например, ставка в ₽)"},
                    "recommended_value": {"type": "string", "description": "рекомендованное значение"},
                    "rationale": {"type": "string", "description": "почему, на основе каких цифр"},
                    "expected_effect": {"type": "string", "description": "ожидаемый эффект"},
                    "priority": {"type": "string", "enum": ["today", "this_week", "month", "scale"]},
                    "negative_keywords": {
                        "type": "array", "items": {"type": "string"},
                        "description": "для change_type=add_negatives — список минус-слов, БЕЗ ведущего дефиса ('ремонт', а не '-ремонт')",
                    },
                },
                "required": ["keyword_id", "phrase", "change_type", "rationale", "priority"],
            },
        },
    },
    "required": ["changes"],
}

SYSTEM_PROMPT = """Ты — опытный PPC-специалист по контекстной рекламе (Яндекс.Директ).
Тебе даны агрегированные данные по ключевым словам за период: показы, клики, расход,
CTR, позиция, ставка, объём трафика, а также данные CRM там, где они есть:
  - crm_leads — все заявки, дошедшие до CRM по этому ключу
  - crm_mql   — заявки, прошедшие первичный отсев (не спам/тест/явный мусор)
  - crm_sql   — заявки, дошедшие минимум до коммерческого предложения (КП/БП),
                независимо от исхода сделки
  - cr_lead_to_mql_pct, cr_mql_to_sql_pct — конверсии между стадиями воронки, %
  - cost_per_mql_rub, cost_per_sql_rub — стоимость привлечения одного MQL/SQL, ₽
  - bid_editable — можно ли вообще менять ставку этого ключа через API:
      true  — кампания на ручной стратегии (MANUAL_CPC), Bid можно записать;
      false — кампания на автоматической стратегии/ЕПК, ставкой управляет
              алгоритм Яндекса, и Yandex Direct API отклонит любую попытку
              записать Keywords[].Bid для этого ключа с ошибкой API.

ВАЖНО про bid_editable=false: для таких ключей НИКОГДА не предлагай
change_type=bid_raise или bid_lower — такое предложение физически нельзя
применить, оно будет отклонено. Вместо этого для проблемных ключей с
bid_editable=false предлагай change_type=add_negatives (минус-слова на
уровне группы объявлений), pause (остановка ключа) или ad_rewrite/ad_test
(правки текста объявления) — эти рычаги работают на уровне ключа/группы
и не зависят от стратегии назначения ставок кампании.

ВАЖНО про negative_keywords: указывай ГОЛЫЕ слова/фразы без дефиса
("ремонт", а не "-ремонт"). В данных фразы ключей могут содержать уже
встроенные минус-слова с дефисом (например, "-Купить -Листы") — это только
для твоего контекста о том, что уже исключено, копировать дефис в свой
ответ НЕ НАДО. Дефис в начале/конце слова Direct API отклонит как ошибку.

Для этого бизнеса важны НЕ продажи и выручка, а количество, конверсия и
стоимость привлечения именно SQL: чем больше SQL при разумной cost_per_sql_rub,
тем лучше. Приоритизируй так:
  - высокий cost_per_sql_rub при низкой cr_mql_to_sql_pct → кандидат на понижение
    ставки (если bid_editable=true) или на паузу/минус-слова (если bid_editable=false) —
    дорогой трафик, который не доходит до КП;
  - низкий cost_per_sql_rub при стабильном потоке SQL → кандидат на повышение
    ставки (только если bid_editable=true) либо на масштабирование другими средствами;
  - есть клики и расход, но crm_leads=0 или мало → возможно, объявление/страница
    не подходят под спрос — рассмотри add_negatives или ad_rewrite, а не только ставку.

Проанализируй данные и предложи конкретные изменения для повышения эффективности.
Используй только те цифры, что даны в данных — не выдумывай значения. Для
change_type=bid_raise/bid_lower обязательно укажи recommended_value в рублях,
рассчитанный от current_value, и делай это ТОЛЬКО когда bid_editable=true. Не
предлагай изменения там, где данных недостаточно (мало кликов/лидов) — в этом
случае лучше пропустить ключ. Верни только вызов инструмента propose_changes."""

# v1.7.0 (пункт 4 запроса): дописываем справку по механике API + методологию
# диагностики — чтобы модель не предлагала технически неприменимые
# изменения и рассуждала по тому же дереву причин, что и ручной аудит.
SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n" + get_llm_knowledge_block()


class LLMProviderError(Exception):
    pass


def provider_configured(provider: str) -> bool:
    return {
        "claude": bool(settings.ANTHROPIC_API_KEY),
        "gemini": bool(settings.GEMINI_API_KEY),
        "groq": bool(settings.GROQ_API_KEY),
        "openrouter": bool(settings.OPENROUTER_API_KEY),
    }.get(provider, False)


def provider_model_name(provider: str) -> str:
    return {
        "claude": settings.ANTHROPIC_MODEL,
        "gemini": settings.GEMINI_MODEL,
        "groq": settings.GROQ_MODEL,
        "openrouter": settings.OPENROUTER_MODEL,
    }.get(provider, "unknown")


def call_llm(provider: str, dataset: list[dict]) -> list[dict]:
    """Единая точка входа. Возвращает список changes (может быть пустым)."""
    if provider not in PROVIDERS:
        raise LLMProviderError(f"Неизвестный провайдер: {provider}. Доступны: {PROVIDERS}")
    if not provider_configured(provider):
        raise LLMProviderError(f"API-ключ для '{provider}' не настроен в .env")

    user_content = (
        f"Данные по {len(dataset)} ключевым словам за период "
        f"(JSON, has_crm={any('crm_leads' in r for r in dataset)}):\n\n"
        + json.dumps(dataset, ensure_ascii=False)
    )

    if provider == "claude":
        return _call_claude(user_content)
    if provider == "gemini":
        return _call_gemini(user_content)
    if provider in ("groq", "openrouter"):
        return _call_openai_compatible(provider, user_content)
    return []


def _call_claude(user_content: str) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    tool = {
        "name": "propose_changes",
        "description": "Вернуть список рекомендованных изменений в рекламном кабинете.",
        "input_schema": CHANGES_SCHEMA,
    }
    resp = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": "propose_changes"},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "propose_changes":
            return block.input.get("changes", [])
    logger.warning("Claude: ответ не содержит tool_use propose_changes")
    return []


def _call_gemini(user_content: str) -> list[dict]:
    """Gemini — через structured output (response_schema), без function calling:
    так надёжнее получить чистый JSON от бесплатной модели."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": CHANGES_SCHEMA,
            "temperature": 0.2,
        },
    }
    resp = httpx.post(url, json=payload, timeout=90.0)
    if resp.status_code != 200:
        raise LLMProviderError(f"Gemini API error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        return parsed.get("changes", [])
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMProviderError(f"Gemini: не удалось распарсить ответ ({e}): {str(data)[:500]}")


def _call_openai_compatible(provider: str, user_content: str) -> list[dict]:
    """Groq и OpenRouter реализуют OpenAI Chat Completions API с tool calling —
    общая логика для обоих."""
    if provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        api_key = settings.GROQ_API_KEY
        model = settings.GROQ_MODEL
        extra_headers = {}
    else:
        url = "https://openrouter.ai/api/v1/chat/completions"
        api_key = settings.OPENROUTER_API_KEY
        model = settings.OPENROUTER_MODEL
        extra_headers = {"HTTP-Referer": "https://localhost", "X-Title": "PPC Optimizer"}

    tool = {
        "type": "function",
        "function": {
            "name": "propose_changes",
            "description": "Вернуть список рекомендованных изменений в рекламном кабинете.",
            "parameters": CHANGES_SCHEMA,
        },
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "tools": [tool],
        "tool_choice": {"type": "function", "function": {"name": "propose_changes"}},
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers}
    resp = httpx.post(url, json=payload, headers=headers, timeout=90.0)
    if resp.status_code != 200:
        raise LLMProviderError(f"{provider} API error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        tool_calls = data["choices"][0]["message"].get("tool_calls") or []
        if not tool_calls:
            logger.warning(f"{provider}: ответ без tool_calls: {str(data)[:500]}")
            return []
        args_str = tool_calls[0]["function"]["arguments"]
        parsed = json.loads(args_str)
        return parsed.get("changes", [])
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMProviderError(f"{provider}: не удалось распарсить ответ ({e}): {str(data)[:500]}")


# ─── v1.6.0: «Задачи ИИ» — свободная команда → план новых ключевых слов ──────────

COMMAND_SCHEMA = {
    "type": "object",
    "properties": {
        "target_ad_group_id": {
            "type": ["integer", "null"],
            "description": "id подходящей СУЩЕСТВУЮЩЕЙ группы объявлений из переданного списка, или null",
        },
        "needs_new_ad_group": {"type": "boolean"},
        "suggested_ad_group_name": {
            "type": "string",
            "description": "если ни одна группа не подходит — предложенное название новой",
        },
        "keywords": {
            "type": "array", "items": {"type": "string"},
            "description": "новые ключевые фразы для добавления",
        },
        "negative_keywords": {
            "type": "array", "items": {"type": "string"},
            "description": "минус-слова без ведущего дефиса, для этой же группы",
        },
        "rationale": {"type": "string"},
    },
    "required": ["keywords", "rationale"],
}

COMMAND_SYSTEM_PROMPT = """Ты — опытный PPC-специалист по контекстной рекламе в Яндекс.Директ,
работаешь с рекламным кабинетом компании, поставляющей импортный металлопрокат
(трубы, листовой прокат, сортовой прокат и т.п.). Пользователь даёт свободную
текстовую команду о том, что добавить в рекламу — например, конкретную
марку стали, типоразмер трубы, ГОСТ/ТУ. Тебе передан список СУЩЕСТВУЮЩИХ групп
объявлений в этом кабинете (id, название группы, название кампании,
текущее число ключей в группе).

Твоя задача:
1. Сгенерировать список релевантных ключевых фраз для описанного в команде
   товара. Используй профессиональную терминологию отрасли (ГОСТ, диаметр,
   толщина стенки, марка стали, способ производства — бесшовная/сварная и
   т.п.), а также разговорные формулировки, которыми реально пользуются покупатели
   в поиске (например, и "труба 89х3.5 гост 10704", и "труба стальная 89 на 3.5
   купить"). 8–20 фраз обычно достаточно.
2. Определи в какую из переданных существующих групп эти ключи лучше всего
   добавить — по смысловому совпадению названия группы/кампании с товаром из
   команды. Верни её id в target_ad_group_id. Используй ТОЛЬКО id из переданного
   списка — не выдумывай числа. Если ни одна группа не подходит по смыслу,
   верни target_ad_group_id=null, needs_new_ad_group=true и предложи вменяемое
   название новой группы в suggested_ad_group_name.
3. Предложи минус-слова для этой же группы, которые стоит добавить вместе с
   новыми ключами, чтобы сразу отсечь нерелевантный трафик по новым фразам
   (например: "бу", "чертёж", "гост скачать", "своими руками", "фото" — в
   зависимости от специфики товара). Можно вернуть пустой список, если
   нечего добавить.

Обоснуй свой выбор группы и список ключей коротко в поле rationale."""

COMMAND_SYSTEM_PROMPT = COMMAND_SYSTEM_PROMPT + "\n\n" + get_llm_knowledge_block()


def call_command_agent(provider: str, command_text: str, context: list[dict]) -> dict:
    """Единая точка входа для страницы «Задачи ИИ». Возвращает dict с ключами
    target_ad_group_id/needs_new_ad_group/suggested_ad_group_name/keywords/
    negative_keywords/rationale (поля могут отсутствовать, если модель их не вернула)."""
    if provider not in PROVIDERS:
        raise LLMProviderError(f"Неизвестный провайдер: {provider}. Доступны: {PROVIDERS}")
    if not provider_configured(provider):
        raise LLMProviderError(f"API-ключ для '{provider}' не настроен в .env")

    user_content = (
        f"Команда пользователя: {command_text}\n\n"
        f"Существующие группы объявлений в кабинете (JSON, {len(context)} шт.):\n\n"
        + json.dumps(context, ensure_ascii=False)
    )

    if provider == "claude":
        return _call_claude_command(user_content)
    if provider == "gemini":
        return _call_gemini_command(user_content)
    if provider in ("groq", "openrouter"):
        return _call_openai_compatible_command(provider, user_content)
    return {}


def _call_claude_command(user_content: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    tool = {
        "name": "propose_plan",
        "description": "Вернуть план добавления ключевых слов по команде пользователя.",
        "input_schema": COMMAND_SCHEMA,
    }
    resp = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=COMMAND_SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": "propose_plan"},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "propose_plan":
            return block.input
    logger.warning("Claude: ответ agent-command не содержит tool_use propose_plan")
    return {}


def _call_gemini_command(user_content: str) -> dict:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": COMMAND_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": COMMAND_SCHEMA,
            "temperature": 0.3,
        },
    }
    resp = httpx.post(url, json=payload, timeout=90.0)
    if resp.status_code != 200:
        raise LLMProviderError(f"Gemini API error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMProviderError(f"Gemini: не удалось распарсить ответ ({e}): {str(data)[:500]}")


def _call_openai_compatible_command(provider: str, user_content: str) -> dict:
    if provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        api_key = settings.GROQ_API_KEY
        model = settings.GROQ_MODEL
        extra_headers = {}
    else:
        url = "https://openrouter.ai/api/v1/chat/completions"
        api_key = settings.OPENROUTER_API_KEY
        model = settings.OPENROUTER_MODEL
        extra_headers = {"HTTP-Referer": "https://localhost", "X-Title": "PPC Optimizer"}

    tool = {
        "type": "function",
        "function": {
            "name": "propose_plan",
            "description": "Вернуть план добавления ключевых слов по команде пользователя.",
            "parameters": COMMAND_SCHEMA,
        },
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": COMMAND_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "tools": [tool],
        "tool_choice": {"type": "function", "function": {"name": "propose_plan"}},
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers}
    resp = httpx.post(url, json=payload, headers=headers, timeout=90.0)
    if resp.status_code != 200:
        raise LLMProviderError(f"{provider} API error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        tool_calls = data["choices"][0]["message"].get("tool_calls") or []
        if not tool_calls:
            logger.warning(f"{provider}: agent-command ответ без tool_calls: {str(data)[:500]}")
            return {}
        args_str = tool_calls[0]["function"]["arguments"]
        return json.loads(args_str)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMProviderError(f"{provider}: не удалось распарсить ответ ({e}): {str(data)[:500]}")


# ─── v1.7.0 (пункт 6 запроса): создание рекламных кампаний ИИ ────────────
#
# Отдельная схема/промпт от CHANGES_SCHEMA/COMMAND_SCHEMA — здесь модель не
# правит существующие объекты, а проектирует НОВУЮ кампанию с нуля: группы,
# точные и широкие ключи, минус-слова, тексты объявлений. Результат идёт как
# один Suggestion(change_type="create_campaign", payload=черновик) — ничего
# не создаётся в Директе, пока директолог не одобрит (см. campaign_planner.py
# и app.core.tasks._apply_suggestion_async).

CAMPAIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Название новой кампании, понятное человеку"},
        "daily_budget_rub": {"type": "number", "description": "Стартовый дневной бюджет в рублях — консервативный, для теста (обычно 300-1000₽)"},
        "rationale": {"type": "string", "description": "Почему эта кампания имеет смысл — на основе каких данных/спроса"},
        "ad_groups": {
            "type": "array",
            "description": "1-5 групп объявлений, каждая — один товар/марка/стандарт (правило «один интент — одна группа»)",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "keywords": {
                        "type": "array", "items": {"type": "string"},
                        "description": "8-20 ключевых фраз: и точные коммерческие с размерами/марками, и более широкие",
                    },
                    "negative_keywords": {
                        "type": "array", "items": {"type": "string"},
                        "description": "минус-слова без ведущего дефиса, отсекающие информационный интент",
                    },
                    "ads": {
                        "type": "array",
                        "description": "1 текстовое объявление на группу",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title":  {"type": "string", "description": "заголовок 1, до 56 символов"},
                                "title2": {"type": "string", "description": "заголовок 2, до 30 символов, можно пусто"},
                                "text":   {"type": "string", "description": "текст объявления, до 81 символа"},
                                "href":   {"type": "string", "description": "ссылка на посадочную страницу"},
                            },
                            "required": ["title", "text", "href"],
                        },
                    },
                },
                "required": ["name", "keywords"],
            },
        },
    },
    "required": ["name", "daily_budget_rub", "ad_groups"],
}

CAMPAIGN_SYSTEM_PROMPT = """Ты — опытный PPC-специалист по контекстной рекламе в Яндекс.Директ,
работаешь с рекламным кабинетом компании, поставляющей импортный металлопрокат
(трубы, листовой прокат, сортовой прокат и т.п.), B2B-сегмент. Тебе дана
свободная команда о том, какую новую кампанию нужно создать (например: «создай
кампанию по маркам стали 1.4310 и S315MC» или «запусти кампанию по трубам
профильным для нового направления»), а также опционально — данные о спросе
и конверсиях по смежным ключам/кластерам, если они уже есть в кабинете.

Спроектируй кампанию с нуля:
1. Название кампании — понятное, отражающее товар/направление.
2. Стартовый дневной бюджет — консервативный (обычно 300-1000₽), это НОВАЯ
   кампания без истории, крупный бюджет здесь неуместен.
3. Группы объявлений — по правилу «один интент — одна группа»: не смешивай
   коммерческие запросы с размерами/марками и информационные (описание/
   характеристики стандарта) в одной группе.
4. Ключевые фразы — используй профессиональную терминологию отрасли (ГОСТ,
   диаметр, толщина стенки, марка стали, способ производства) и то, как
   реально ищут снабженцы. Приоритет — точные фразы с конкретными размерами
   и марками (самый конвертирующий тип по методологии проекта, "золотые
   фразы"), плюс несколько более широких для охвата.
5. Минус-слова — отсекай информационный интент («что такое», «характеристики»,
   «скачать», «гост pdf») и конкурирующие материалы, если применимо.
6. Одно текстовое объявление на группу — заголовок с маркой/стандартом,
   текст с конкретным оффером, ссылка на посадочную (если не знаешь точный
   URL — используй разумную заглушку вида https://example.com/catalog и
   поясни в rationale, что ссылку нужно проверить и заменить перед подтверждением).

Обоснуй в rationale, почему эта кампания и её бюджет разумны."""

CAMPAIGN_SYSTEM_PROMPT = CAMPAIGN_SYSTEM_PROMPT + "\n\n" + get_llm_knowledge_block()


def call_campaign_planner(provider: str, command_text: str, market_context: list[dict]) -> dict:
    """Точка входа для конструктора кампаний (пункт 6). Возвращает черновик
    dict с ключами name/daily_budget_rub/ad_groups/rationale — ничего не
    создаёт в Директе сама, только формирует структуру для payload будущего
    Suggestion(change_type=create_campaign)."""
    if provider not in PROVIDERS:
        raise LLMProviderError(f"Неизвестный провайдер: {provider}. Доступны: {PROVIDERS}")
    if not provider_configured(provider):
        raise LLMProviderError(f"API-ключ для '{provider}' не настроен в .env")

    user_content = (
        f"Команда пользователя: {command_text}\n\n"
        f"Контекст по смежным ключам/кластерам в кабинете, если есть (JSON):\n\n"
        + json.dumps(market_context, ensure_ascii=False)
    )

    if provider == "claude":
        return _call_claude_campaign(user_content)
    if provider == "gemini":
        return _call_gemini_campaign(user_content)
    if provider in ("groq", "openrouter"):
        return _call_openai_compatible_campaign(provider, user_content)
    return {}


def _call_claude_campaign(user_content: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    tool = {
        "name": "propose_campaign",
        "description": "Вернуть черновик новой рекламной кампании.",
        "input_schema": CAMPAIGN_SCHEMA,
    }
    resp = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=CAMPAIGN_SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": "propose_campaign"},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "propose_campaign":
            return block.input
    logger.warning("Claude: ответ campaign-planner не содержит tool_use propose_campaign")
    return {}


def _call_gemini_campaign(user_content: str) -> dict:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": CAMPAIGN_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": CAMPAIGN_SCHEMA,
            "temperature": 0.3,
        },
    }
    resp = httpx.post(url, json=payload, timeout=120.0)
    if resp.status_code != 200:
        raise LLMProviderError(f"Gemini API error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMProviderError(f"Gemini: не удалось распарсить ответ ({e}): {str(data)[:500]}")


def _call_openai_compatible_campaign(provider: str, user_content: str) -> dict:
    if provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        api_key = settings.GROQ_API_KEY
        model = settings.GROQ_MODEL
        extra_headers = {}
    else:
        url = "https://openrouter.ai/api/v1/chat/completions"
        api_key = settings.OPENROUTER_API_KEY
        model = settings.OPENROUTER_MODEL
        extra_headers = {"HTTP-Referer": "https://localhost", "X-Title": "PPC Optimizer"}

    tool = {
        "type": "function",
        "function": {
            "name": "propose_campaign",
            "description": "Вернуть черновик новой рекламной кампании.",
            "parameters": CAMPAIGN_SCHEMA,
        },
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": CAMPAIGN_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "tools": [tool],
        "tool_choice": {"type": "function", "function": {"name": "propose_campaign"}},
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers}
    resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
    if resp.status_code != 200:
        raise LLMProviderError(f"{provider} API error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        tool_calls = data["choices"][0]["message"].get("tool_calls") or []
        if not tool_calls:
            logger.warning(f"{provider}: campaign-planner ответ без tool_calls: {str(data)[:500]}")
            return {}
        args_str = tool_calls[0]["function"]["arguments"]
        return json.loads(args_str)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMProviderError(f"{provider}: не удалось распарсить ответ ({e}): {str(data)[:500]}")
