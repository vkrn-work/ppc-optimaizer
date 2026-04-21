# Changelog — PPC Optimizer

Все значимые изменения фиксируются здесь в обратном хронологическом порядке.
Формат: `[версия] — дата — краткое описание`. Детали под каждой записью.

---

## [1.2.0] — 2026-04-21

### Главное

Реализован движок генерации сигналов на основе PPC Metrics Handbook. Система теперь диагностирует 12 типов проблем по 5 уровням (ставки, показы, трафик, поведение, точки роста) и генерирует обоснованные предложения с гипотезой, расчётом и ожидаемым результатом.

Добавлены новые метрики сбора из API Директа: взвешенные показатели, данные об отказах, обогащение визитами из Метрики.

---

### Добавлено

**БД — новые колонки (требуется миграция `migrate_v1_2.sql`)**

- `keyword_stats.weighted_impressions` (INTEGER) — взвешенные показы `WeightedImpressions` из Директа, учитывают позицию показа
- `keyword_stats.weighted_ctr` (NUMERIC 8,4) — взвешенный CTR `WeightedCtr`, корректнее сравнивать CTR при разных позициях
- `keyword_stats.bounce_rate` (NUMERIC 6,2) — процент отказов `BounceRate` из Директа, поведение по клику
- `keyword_stats.sessions` (INTEGER) — визиты из Яндекс Метрики, обогащается по `utm_term → keyword.phrase` после каждого сбора
- `campaigns.epk_collapse_detected` (BOOLEAN DEFAULT FALSE) — флаг обнаружения ЕПК-обвала
- `hypothesisverdict` enum: добавлено значение `neutral` (требует `ALTER TYPE` отдельной командой)

**Анализатор (`cr_analyzer.py`) — полная переработка**

Реализовано 12 типов сигналов по дереву диагностики из PPC Metrics Handbook:

| Сигнал | Тип | Слой | Условие |
|---|---|---|---|
| S-001 | `low_position` | bid_keyword | avg_position > 3, clicks ≥ 3, ручная стратегия |
| S-002 | `traffic_drop` | bid_keyword | клики упали > 40%, prev > 5, traffic_vol > 50 |
| S-003 | `zero_ctr` | bid_keyword | impressions ≥ 100, clicks = 0 |
| S-004 | `low_ctr` | bid_keyword | CTR < 1%, impressions ≥ 50, позиция ≤ 3 |
| S-005 | `click_position_gap` | bid_keyword | поз.клика > поз.показа + 1.5, clicks ≥ 5 |
| S-006 | `spend_no_conversion` | bid_keyword | spend > target_cpl × 3, clicks ≥ 30 |
| S-010 | `epk_bid_collapse` | impression | ≥ 5 ключей одной кампании с падением ставки > 50% и кликов > 50% за период |
| S-020 | `cpc_spike` | traffic | CPC вырос > 40% к предыдущему периоду, clicks ≥ 5 |
| S-040 | `high_bounce_rate` | behavior | bounce_rate > 60% (критично > 75%), visits > 20 |
| S-041 | `low_page_depth` | behavior | pageDepth < 1.3, visits > 20 |
| S-042 | `low_visit_duration` | behavior | avgDuration < 30 сек, visits > 20 |
| S-043 | `mobile_quality_issue` | behavior | мобильный bounce > desktop bounce + 20%, visits > 10 |

Каждый сигнал содержит: `signal_id`, `severity`, `priority`, `layer`, `description`, `hypothesis`, `action`, `expected_outcome`, `calculation_logic`, `recommended_bid`.

Добавлен скоринг качества трафика `traffic_quality_score` (0–100) по данным Метрики.

**Генератор предложений (`suggestion_generator.py`) — переработка**

Логика перенесена с `KeywordMetrics + Rule` на `analysis.problems`. Каждый сигнал из анализатора конвертируется в `Suggestion` со статусом `pending`. Реализована дедупликация — повторные сигналы для уже существующего pending-предложения не создают дубликатов.

**Коллектор (`direct_collector.py`)**

В запрос `CRITERIA_PERFORMANCE_REPORT` добавлены поля: `WeightedImpressions`, `WeightedCtr`, `BounceRate`. Теперь собираются все метрики для работы анализатора v1.2.

**Задачи (`core/tasks.py`)**

- Новая функция `_enrich_sessions()` — после сбора Метрики матчит `utm_term → keyword.phrase → keyword_stats.sessions`. Заполняет визиты из Метрики в разрезе ключей.
- Поисковые запросы теперь собираются в том же `async with` блоке, что и статистика — убрано лишнее открытие коннекта.

**API (`routes.py`)**

- `GET /accounts/{id}/daily-stats?date_from=&date_to=` — посуточная статистика по кабинету за произвольный диапазон (для спарклайнов и графиков на дашборде)
- `GET /accounts/{id}/campaigns/{cid}/daily-stats?date_from=&date_to=` — посуточная статистика конкретной кампании (для drill-down)
- `period_dates()` — поддержка произвольного диапазона через `date_from / date_to / compare_from / compare_to` на всех эндпоинтах
- Эндпоинт `/campaigns` — добавлены поля `bounce_rate`, `sessions`, `signals_count`, `signals_critical`, `signals_warning`, `has_epk_collapse`, `top_signal`
- Эндпоинт `/keywords` — добавлены `weighted_ctr`, `weighted_impressions`, `bounce_rate`, `sessions`, `click_position_gap`, `traffic_quality`, `bid_delta`, `position_delta`
- Эндпоинт `/dashboard` — добавлено `analysis_summary` (сводка сигналов из анализа)
- Эндпоинт `/metrika-snapshot` — возвращает `prev_date` и `prev_data` (предыдущий снапшот для сравнения)

**Фронтенд (`campaigns.js`)**

- Колонка «Сигналы»: показывает количество критичных и важных сигналов по кампании, метку `⚠ ЕПК` при обнаружении обвала
- Сортировка по умолчанию — по критичности сигналов, потом по расходу
- Режим «Ключи» — поле сигнала показывает тип из новой классификации

---

### Порядок деплоя v1.2

1. Задеплоить новый код (все изменённые файлы)
2. Выполнить миграцию в PostgreSQL:
   ```sql
   -- Из файла backend/app/db/migrate_v1_2.sql
   ALTER TABLE keyword_stats
     ADD COLUMN IF NOT EXISTS weighted_impressions INTEGER,
     ADD COLUMN IF NOT EXISTS weighted_ctr NUMERIC(8, 4),
     ADD COLUMN IF NOT EXISTS bounce_rate NUMERIC(6, 2),
     ADD COLUMN IF NOT EXISTS sessions INTEGER;
   ALTER TABLE campaigns
     ADD COLUMN IF NOT EXISTS epk_collapse_detected BOOLEAN DEFAULT FALSE;
   -- Отдельной командой вне транзакции:
   ALTER TYPE hypothesisverdict ADD VALUE IF NOT EXISTS 'neutral';
   ```
3. Перезапустить бэкенд и воркер
4. Запустить ретроспективный сбор для заполнения новых полей: `POST /accounts/{id}/sync?days=90`

---

### Известные ограничения (без изменений)

- `oauth_token` хранится в открытом виде в БД — нужно шифрование (запланировано)
- Сигналы S-040..S-043 (поведение) требуют данных Метрики — при первом запуске до сбора снапшота не генерируются
- Матчинг `utm_term → sessions` приближённый (точный требует client_id/roistat_id, Уровень 2)
- Уровень 2 (CRM/лиды) не реализован

---

## [1.1.0] — 2026-04-20

### Исправлены баги

**Бэкенд**

- `tasks.py` — исправлен критический баг: `is_active` у кампаний выставлялось через `Status == "ON"`, хотя поле `Status` в API Директа означает статус модерации (ACCEPTED/REJECTED), а не статус запуска. Поскольку запрос уже фильтрует `States: ON` и `Statuses: ACCEPTED`, все пришедшие кампании активны — теперь явно проставляется `is_active=True`. Это устраняло показ 87 кампаний вместо реального числа.
- `tasks.py` — `AvgEffectiveBid` из Reports API приходит в микрорублях (1 руб = 1 000 000 мкруб). Добавлено деление на `1_000_000` при сохранении в `avg_bid`. До этого ставки в таблице ключей были завышены в миллион раз.
- `tasks.py` — `strategy_type` теперь сохраняется при upsert кампаний (раньше поле не попадало в `set_` при конфликте).
- `routes.py` — `avg_cpc` в агрегациях теперь считается как `sum(spend)/sum(clicks)`, а не `avg(avg_cpc)`. Аналогично `ctr` = `sum(clicks)/sum(impressions)*100`. Среднее от средних некорректно при разном объёме кликов.
- `routes.py` — в ответе `/campaigns` добавлено поле `direct_id` (ID кампании в Яндекс Директ). Поле было в БД, но не передавалось на фронтенд.
- `routes.py` — диагностика (`/diagnostics`) переписана: убран `__import__` через eval-подобный вызов, счётчик поисковых запросов теперь работает корректно.

**Фронтенд**

- `api.js` — добавлены недостающие методы, из-за отсутствия которых страницы Корректировки и Диагностика падали с `TypeError: api.X is not a function`:
  - `getMetrikaSnapshot(id)`
  - `getDiagnostics(id)`
  - `createHypothesis(id, data)`
  - `getSearchQueries(id, params)`
  - `getAdGroups(id, campaignId)`
  - `triggerHistoricalSync(id, days)`
- `api.js` — `getDashboard` и `getCampaigns` теперь принимают параметр `period`. Раньше переключение периода на дашборде и в кампаниях не имело эффекта.
- `Layout.js` — время последней синхронизации теперь корректно показывает МСК. `last_sync_at` из БД приходит как UTC без суффикса `Z` — браузер трактовал его как локальное время. Добавлено принудительное добавление `Z` перед конвертацией.
- `bids.js` — убран жёсткий фильтр `strategy_type === 'MANUAL_CPC'` на список кампаний. Теперь показываются все кампании, ручные помечаются `✎`, автоматические `⚙`.
- `bids.js` — добавлен выбор группы объявлений (появляется после выбора кампании, подгружает группы через новый эндпоинт).
- `bids.js` — добавлены столбцы CTR и CPC в таблицу ключей.

### Добавлено

- `routes.py` — новый эндпоинт `GET /accounts/{id}/ad-groups` — возвращает группы объявлений с базовой статистикой (клики, расход, количество ключей) за выбранный период.
- `routes.py` — эндпоинт `POST /accounts/{id}/sync` теперь принимает query-параметр `?days=N`. По умолчанию 28 дней (стандартный цикл), при `days=90` собирает ретроспективу. Позволяет загрузить историю без ожидания накопления данных.
- `tasks.py` — `collect_account_data` принимает параметр `days: int = 28`, который передаётся в async-функцию сбора. Включена детальная логика: `logger.info` на каждом этапе (кампании, группы, ключи, статистика).
- `api.js` — метод `triggerHistoricalSync(id, days=90)` для запуска ретроспективного сбора через UI.

### Известные ограничения (без изменений)

- `oauth_token` хранится в открытом виде в БД — нужно шифрование (запланировано).
- Страница групп объявлений в разделе "По кампаниям" не реализована — только кампании и ключи.
- Уровень 2 (CRM/лиды) не реализован — блок CRM на дашборде показывает заглушку.

---

## [1.0.0] — 2026-04 (первоначальный деплой)

MVP задеплоен на Railway. Реализован Уровень 1: сбор данных из Яндекс Директ и Метрики, CR-анализ, дашборд с KPI и дельтами, 11 страниц фронтенда.
