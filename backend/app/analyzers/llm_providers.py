"""
Единая точка вызова LLM-провайдера. Пользователь выбирает провайдера на фронте
перед запуском анализа (Claude / Gemini / Groq / OpenRouter) — это позволяет
бесплатно гонять весь пайплайн на Gemini/Groq/OpenRouter и переключиться на
Claude только когда логика проверена.

Все 4 провайдера получают ОДИНАКОВЫЕ system prompt и датасет и должны вернуть
строго типизированный JSON {"changes": [...]} — либо через tool/function calling
(Claude, Groq, OpenRouter — OpenAI-совместимый tool_choice), либо через
structured output (Gemini — response_schema).

ВНИМАНИЕ: ни один из 4 вызовов не выполнялся с реальным ключом (нет доступа
к api.anthropic.com/generativelanguage.googleapis.com/api.groq.com/openrouter.ai
из песочницы) — структура запросов написана по документации каждого провайдера,
но не проверена вживую. Первый реальный вызов может вскрыть нюансы формата ответа.
"""
import json
import logging
from typing import Optional

import httpx

from app.core.config import settings

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
                        "description": "для change_type=add_negatives — список минус-слов",
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
CTR, позиция, ставка, объём трафика, а также данные CRM (лиды, сделки, выручка) там,
где они есть.

Проанализируй данные и предложи конкретные изменения для повышения эффективности
(снижение CPL/CPA, рост конверсии, экономия бюджета). Используй только те цифры,
что даны в данных — не выдумывай значения. Для change_type=bid_raise/bid_lower
обязательно укажи recommended_value в рублях, рассчитанный от current_value.
Не предлагай изменения там, где данных недостаточно (мало кликов/лидов) —
в этом случае лучше пропустить ключ. Верни только вызов инструмента propose_changes."""


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
