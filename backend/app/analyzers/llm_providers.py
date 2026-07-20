"""
Единая точка вызова LLM-провайдера. Пользователь выбирает провайдера на фронте
перед запуском анализа (Claude / Gemini / Groq / OpenRouter) — это позволяет
бесплатно гонять весь пайплайн на Gemini/Groq/OpenRouter и переключиться на
 Claude только когда логика проверена.

Все 4 провайдера получают ОДИНАКОВЫЕ system prompt и датасет и должны вернуть
строго типизированный JSON {"changes": [...]} — либо через tool/function calling
(Claude, Groq, OpenRouter — OpenAI-совместимый tool_choice), либо через structured output
(Gemini — response_schema).

Схемы и тексты промптов живут в llm_prompts.py, бюджет запроса и ретраи — в
llm_budget.py. Здесь остаётся только логика вызова провайдеров.

v1.5.0: в промпте объяснено поле bid_editable из датасета — на реальном apply
выяснилось, что в этом кабинете нет ни одной кампании на ручных ставках —
для авто-стратегий/ЕПК Yandex Direct API вообще не принимает Keywords[].Bid.
Код в llm_analyzer.py теперь жёстко отклоняет bid_raise/bid_lower для таких
ключей даже если модель не послушается этому промпту — но честнее сразу не
тратить бюджет вызова на заведомо неприменимые предложения.

v1.5.2: на реальном apply add_negatives модель (Groq) вернула минус-слова с
приклеенным "-" ("-hard", "-ru") — скопировала формат фраз ключей из датасета,
где минус-слова уже хранятся с дефисом. Direct API такое отклоняет (ошибка
5002 — дефис в начале/конце слова недопустим). Явно прописано в промпте;
дополнительно очищается в коде (direct_writer.py) как защита от повторения.

v1.6.0: добавлен call_command_agent() — отдельный режим для страницы «Задачи
ИИ»: пользователь даёт свободную команду («добавь в рекламу такую-то сталь»),
модель генерирует список ключевых фраз + минус-слова + целевую группу объявлений
из числа реально существующих. Отдельная схема/промпт от основного анализа
— чтобы не трогать уже проверенный пайплайн call_llm()/CHANGES_SCHEMA.

v1.7.3: payload теперь ужимается под лимиты провайдера перед отправкой
(см. llm_budget.fit_to_budget), а временные ошибки провайдера (503/429)
ретраятся с backoff (llm_budget.post_with_retry) — во ВСЕХ трёх режимах,
не только в основном анализе. Поводом стали две ошибки на реальном прогоне
v1.7.2: groq 413 "Limit 12000 TPM, Requested 37516" и gemini 503 "high demand".
Важное открытие по Groq: TPM считает input + зарезервированный output, поэтому
max_tokens тоже стал зависеть от провайдера.

v1.7.2: call_llm() принимает второй аргумент context — агрегаты по аккаунту,
кампаниям, длинному хвосту и сырому поисковому спросу (см. llm_analyzer._build_context).
Раньше модель видела только плоский список ключей, не могла сравнить ключ со средним
по аккаунту и возвращала одиночные осторожные предложения.

v1.7.1: добавлены поля diagnostics/summary в CHANGES_SCHEMA — модель теперь возвращает
не только список изменений, но и пошаговый человекочитаемый рассказ о ходе анализа —
для живого «чата» с ИИ на вкладке «История вход/выход» вместо голого JSON.
"""
import json
import logging
from typing import Optional

from app.core.config import settings
from app.analyzers.llm_budget import provider_limits, fit_to_budget, post_with_retry
from app.analyzers.llm_prompts import (
    PROVIDERS, CHANGES_SCHEMA, SYSTEM_PROMPT,
    COMMAND_SCHEMA, COMMAND_SYSTEM_PROMPT,
    CAMPAIGN_SCHEMA, CAMPAIGN_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


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


def call_llm(provider: str, dataset: list[dict], context: Optional[dict] = None) -> dict:
    """Единая точка входа. Возвращает dict {"changes": [...], "diagnostics": [...],
    "summary": "...", "payload_meta": {...}} — diagnostics/summary дают
    человекочитаемый рассказ о ходе анализа для вкладки «История вход/выход»,
    payload_meta говорит, что из данных пришлось урезать под лимит провайдера."""
    if provider not in PROVIDERS:
        raise LLMProviderError(f"Неизвестный провайдер: {provider}. Доступны: {PROVIDERS}")
    if not provider_configured(provider):
        raise LLMProviderError(f"API-ключ для '{provider}' не настроен в .env")

    # v1.7.2: кроме построчного датасета передаём агрегированный контекст аккаунта —
    # средние/медианы для сравнения, разрез по кампаниям, длинный хвост и сырой
    # поисковый спрос. Без него модель не могла сказать "дороже среднего втрое"
    # и возвращала одиночные осторожные предложения.
    # v1.7.3: ужимаем payload под лимиты конкретного провайдера ДО отправки —
    # иначе Groq на free-тарифе отвечает 413 и прогон теряется целиком.
    limits = provider_limits(provider)
    dataset, context, payload_meta = fit_to_budget(dataset, context, limits["input_budget"])

    parts = []
    if context:
        parts.append(
            "КОНТЕКСТ АККАУНТА (агрегаты за период — бенчмарк для сравнения, "
            "разрез по кампаниям, длинный хвост, сырой поисковый спрос):\n"
            + json.dumps(context, ensure_ascii=False)
        )
    parts.append(
        f"ПОСТРОЧНЫЕ ДАННЫЕ по {len(dataset)} ключевым словам за период "
        f"(JSON, has_crm={any('crm_leads' in r for r in dataset)}):\n"
        + json.dumps(dataset, ensure_ascii=False)
    )
    if payload_meta.get("trimmed"):
        parts.append(
            "ПРИМЕЧАНИЕ: датасет урезан под лимит запроса провайдера "
            f"({payload_meta['original_keywords']} ключей → {payload_meta['sent_keywords']}). "
            "Работай с тем, что прислано, и не делай выводов об объёме аккаунта "
            "по числу строк — агрегаты в КОНТЕКСТ АККАУНТА посчитаны по полным данным."
        )
    user_content = "\n\n".join(parts)

    max_output = limits["max_output"]
    if provider == "claude":
        result = _call_claude(user_content, max_output)
    elif provider == "gemini":
        result = _call_gemini(user_content, max_output)
    elif provider in ("groq", "openrouter"):
        result = _call_openai_compatible(provider, user_content, max_output)
    else:
        result = {"changes": [], "diagnostics": [], "summary": ""}

    result["payload_meta"] = payload_meta
    return result


def _call_claude(user_content: str, max_output: int = 8192) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    tool = {
        "name": "propose_changes",
        "description": "Вернуть список рекомендованных изменений в рекламном кабинете.",
        "input_schema": CHANGES_SCHEMA,
    }
    resp = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_output,  # v1.7.3: зависит от провайдера, см. llm_budget.PROVIDER_LIMITS
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": "propose_changes"},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "propose_changes":
            return {
                "changes": block.input.get("changes", []),
                "diagnostics": block.input.get("diagnostics", []),
                "summary": block.input.get("summary", ""),
            }
    logger.warning("Claude: ответ не содержит tool_use propose_changes")
    return {"changes": [], "diagnostics": [], "summary": ""}


def _call_gemini(user_content: str, max_output: int = 8192) -> dict:
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
            "maxOutputTokens": max_output,  # v1.7.3: см. llm_budget.PROVIDER_LIMITS
        },
    }
    # v1.7.3: Gemini регулярно отдаёт 503 "high demand" — ретраим с backoff,
    # чтобы не терять весь прогон анализа из-за временной недоступности.
    resp = post_with_retry(url, json_payload=payload, timeout=120.0, provider="gemini")
    if resp.status_code != 200:
        raise LLMProviderError(f"Gemini API error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        return {
            "changes": parsed.get("changes", []),
            "diagnostics": parsed.get("diagnostics", []),
            "summary": parsed.get("summary", ""),
        }
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMProviderError(f"Gemini: не удалось распарсить ответ ({e}): {str(data)[:500]}")


def _call_openai_compatible(provider: str, user_content: str, max_output: int = 4096) -> dict:
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
        # v1.7.3: КРИТИЧНО для Groq — TPM-лимит считает и зарезервированный
        # output тоже, поэтому max_tokens=8192 съедал весь лимит 12000 ещё до
        # данных. Теперь берётся из llm_budget.PROVIDER_LIMITS (для groq — 4000).
        "max_tokens": max_output,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers}
    resp = post_with_retry(url, json_payload=payload, headers=headers, timeout=120.0, provider=provider)
    if resp.status_code != 200:
        raise LLMProviderError(f"{provider} API error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        tool_calls = data["choices"][0]["message"].get("tool_calls") or []
        if not tool_calls:
            logger.warning(f"{provider}: ответ без tool_calls: {str(data)[:500]}")
            return {"changes": [], "diagnostics": [], "summary": ""}
        args_str = tool_calls[0]["function"]["arguments"]
        parsed = json.loads(args_str)
        return {
            "changes": parsed.get("changes", []),
            "diagnostics": parsed.get("diagnostics", []),
            "summary": parsed.get("summary", ""),
        }
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMProviderError(f"{provider}: не удалось распарсить ответ ({e}): {str(data)[:500]}")


# ─── v1.6.0: «Задачи ИИ» — свободная команда → план новых ключевых слов ────────

def call_command_agent(provider: str, command_text: str, context: list[dict]) -> dict:
    """Единая точка входа для страницы «Задачи ИИ». Возвращает dict с ключами
    target_ad_group_id/needs_new_ad_group/suggested_ad_group_name/keywords/
    negative_keywords/rationale (поля могут отсутствовать, если модель их не вернёт)."""
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
    resp = post_with_retry(url, json_payload=payload, timeout=90.0, provider="gemini")
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
    resp = post_with_retry(url, json_payload=payload, headers=headers, timeout=90.0, provider=provider)
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
    resp = post_with_retry(url, json_payload=payload, timeout=120.0, provider="gemini")
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
    resp = post_with_retry(url, json_payload=payload, headers=headers, timeout=120.0, provider=provider)
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
