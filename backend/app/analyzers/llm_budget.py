"""
v1.7.3: бюджетирование запроса к LLM под лимиты провайдера + ретраи.

Вынесено из llm_providers.py отдельным модулем, чтобы логику лимитов можно было
тестировать и менять, не трогая уже обкатанный код вызова четырёх провайдеров.

Поводом стали две ошибки на реальном прогоне v1.7.2:
    groq   413 — "Request too large: Limit 12000 TPM, Requested 37516"
    gemini 503 — "This model is currently experiencing high demand"

Разбор ошибки Groq вскрыл неочевидное: TPM на free-тарифе считает не только
input, но и ЗАРЕЗЕРВИРОВАННЫЙ output (наш max_tokens). При max_tokens=8192 и
фиксированной части промпта ~4200 токенов лимит 12000 выбирался ПОЛНОСТЬЮ ещё
до единой строки данных — запрос не прошёл бы даже с пустым датасетом. Поэтому
здесь и урезание входных данных, и снижение max_tokens для узких провайдеров,
а не что-то одно.
"""
import json
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# input_budget — сколько токенов отводим на данные (датасет + контекст),
# max_output   — max_tokens ответа. Сумма (фикс. промпт ~4200 + input_budget +
# max_output) должна с запасом влезать в лимит провайдера.
PROVIDER_LIMITS = {
    # v1.7.6: TPM 12000 на ВСЁ (system+schema+данные+зарезервированный ответ).
    # Фикс. часть (промпт+схема) ~4500 токенов реальных. Оставляем 2500 на
    # данные и 2500 на ответ => ~9500, запас ~2500 — с пессимистичной оценкой
    # est_tokens этого хватает, чтобы не превысить лимит.
    "groq":       {"input_budget": 2500,  "max_output": 2500},
    # Gemini: контекст на порядки больше, ограничение не в токенах, а в
    # стабильности (см. ретраи ниже).
    "gemini":     {"input_budget": 60000, "max_output": 8192},
    "claude":     {"input_budget": 60000, "max_output": 8192},
    # OpenRouter: зависит от выбранной модели, берём консервативно.
    "openrouter": {"input_budget": 12000, "max_output": 4096},
}
DEFAULT_LIMITS = {"input_budget": 8000, "max_output": 4096}


def provider_limits(provider: str) -> dict:
    return PROVIDER_LIMITS.get(provider, DEFAULT_LIMITS)


def est_tokens(text: str) -> int:
    """Грубая оценка токенов. v1.7.6: для КИРИЛЛИЦЫ в JSON реальная плотность
    ближе к ~2 символам на токен, а не 3 — прежняя оценка /3 недооценивала
    размер, и Groq на free-тарифе всё равно ловил 413 (Requested 12315 при
    лимите 12000). Считаем пессимистично /2, чтобы бюджет соответствовал
    реальному числу токенов у провайдера."""
    return len(text) // 2 + 1


def _compact_row(row: dict) -> dict:
    """Выкидывает None-поля: в датасете их много (avg_bid_rub, crm_* у ключей
    без заявок), а в токенах они стоят как настоящие данные."""
    return {k: v for k, v in row.items() if v is not None}


def fit_to_budget(dataset: list[dict], context: Optional[dict], budget: int):
    """Ужимает payload под бюджет провайдера, срезая наименее ценное первым.

    Порядок именно такой, потому что дешевле всего расстаться с тем, что почти
    не несёт информации на строку:
      1. None-поля — по смыслу бесплатно, по объёму заметно;
      2. хвост поисковых запросов;
      3. thin_data-ключи по отдельности — по ним и так рано принимать решение,
         а их суммарный вклад остаётся в context.long_tail;
      4. в последнюю очередь — ключи с трафиком, с конца по расходу.

    Возвращает (dataset, context, meta). meta уходит в БД и показывается на
    вкладке «История вход/выход» — урезание не должно быть молчаливым, иначе
    непонятно, почему предложений мало.
    """
    meta = {
        "budget_tokens": budget,
        "original_keywords": len(dataset),
        "original_search_queries": len((context or {}).get("top_search_queries") or []),
        "trimmed": [],
    }

    ds = [_compact_row(r) for r in dataset]
    ctx = json.loads(json.dumps(context)) if context else None

    def size() -> int:
        s = json.dumps(ds, ensure_ascii=False)
        if ctx:
            s += json.dumps(ctx, ensure_ascii=False)
        return est_tokens(s)

    def done():
        meta.update({"sent_keywords": len(ds), "final_tokens": size()})
        return ds, ctx, meta

    if size() <= budget:
        return done()

    if ctx and ctx.get("top_search_queries"):
        for n in (15, 5, 0):
            ctx["top_search_queries"] = ctx["top_search_queries"][:n]
            meta["trimmed"].append(f"search_queries→{n}")
            if size() <= budget:
                return done()

    thin_count = sum(1 for r in ds if r.get("thin_data"))
    if thin_count:
        ds = [r for r in ds if not r.get("thin_data")]
        meta["trimmed"].append(f"thin_data_keywords−{thin_count}")
        if size() <= budget:
            return done()

    # датасет уже отсортирован по расходу — режем с конца, наименее значимое
    while len(ds) > 5 and size() > budget:
        ds = ds[:-max(1, len(ds) // 10)]
    meta["trimmed"].append(f"keywords→{len(ds)}")
    return done()


RETRY_STATUS = (429, 500, 502, 503, 529)


def post_with_retry(url: str, *, json_payload: dict, headers: Optional[dict] = None,
                    timeout: float = 120.0, provider: str = "", attempts: int = 3):
    """POST с экспоненциальным backoff на временных ошибках провайдера.

    Gemini регулярно отдаёт 503 "high demand" — это не ошибка нашего запроса, и
    терять из-за неё весь прогон анализа незачем. 413/400 НЕ ретраятся: повтор
    того же слишком большого запроса даст ровно тот же ответ.
    """
    last = None
    for i in range(attempts):
        resp = httpx.post(url, json=json_payload, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp
        last = resp
        if resp.status_code not in RETRY_STATUS or i == attempts - 1:
            return resp
        delay = 2 ** i * 3  # 3с, 6с
        logger.warning(
            f"{provider}: HTTP {resp.status_code}, повтор через {delay}с "
            f"(попытка {i + 2} из {attempts})"
        )
        time.sleep(delay)
    return last
