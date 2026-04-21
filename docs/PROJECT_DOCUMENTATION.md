# PPC Optimizer — Документация проекта

> Версия: 1.2.0 · Апрель 2026
> Статус: MVP Уровень 1 задеплоен, движок сигналов реализован

---

## 1. Что это за приложение

**PPC Optimizer** — система автоматизации управления поисковыми рекламными кампаниями в Яндекс Директ для B2B-компании в нише импортного металлопроката.

Директолог тратит 10–15 часов в неделю на ручной сбор статистики и принятие решений по ставкам. Система заменяет рутину: собирает данные автоматически, анализирует динамику показателей по 5 уровням, генерирует обоснованные предложения по оптимизации.

**Целевые метрики:** 200 SQL в месяц при CPQL ≤ 6 000 ₽
**Кабинет:** `iipcab4@yandex.ru`, счётчик Метрики: `91634755`

---

## 2. Архитектура системы

```
┌─────────────────────────────────────────────────────┐
│              ФРОНТЕНД (Next.js 14)                  │
│  ppc-optimizer.up.railway.app                       │
│  11 страниц: дашборд, кампании, ставки и др.        │
└──────────────────────┬──────────────────────────────┘
                       │ REST API / JSON
┌──────────────────────▼──────────────────────────────┐
│              БЭКЕНД (FastAPI)                       │
│  ppc-optimaizer-production.up.railway.app           │
│  /api/v1/ — 18 эндпоинтов                          │
└──────────┬───────────────────────┬──────────────────┘
           │                       │
┌──────────▼──────────┐  ┌─────────▼──────────────────┐
│   PostgreSQL        │  │  Celery Worker + Beat       │
│   (Railway)         │  │  Ежедневный сбор 06:00 МСК  │
│   15 таблиц         │  │  Яндекс Директ API v5       │
└─────────────────────┘  │  Яндекс Метрика API         │
                         └─────────────────────────────┘
                  Redis (Railway) — брокер задач
```

### Технологический стек

| Компонент | Технология | Версия |
|---|---|---|
| Фронтенд | Next.js + React | 14.2.15 / 18.3 |
| Бэкенд | FastAPI + Python | 0.115 / 3.12 |
| ORM | SQLAlchemy async | 2.0.35 |
| БД | PostgreSQL + asyncpg | — |
| Очереди | Celery + Redis | 5.4.0 |
| Деплой | Railway (3 сервиса) | — |

---

## 3. Деплой на Railway

| Сервис | Название в Railway | URL |
|---|---|---|
| Бэкенд (FastAPI) | `ppc-optimaizer` | `https://ppc-optimaizer-production.up.railway.app` |
| Фронтенд (Next.js) | `diplomatic-spirit` | `https://ppc-optimizer.up.railway.app` |
| Воркер (Celery) | `serene-embrace` | внутренний |

**Команды запуска:**
- Бэкенд: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Воркер: `celery -A app.core.celery_app worker --beat --loglevel=info -Q default,analysis`

**Переменные окружения:**
```
DATABASE_URL=postgresql://...   # Railway автоподставляет
REDIS_URL=redis://...           # Railway автоподставляет
ALLOWED_ORIGINS=*
SECRET_KEY=...
ENVIRONMENT=production
```

---

## 4. REST API эндпоинты

Базовый URL: `https://ppc-optimaizer-production.up.railway.app/api/v1`

### Кабинеты

| Метод | Путь | Описание |
|---|---|---|
| GET | `/accounts` | Список кабинетов |
| POST | `/accounts` | Создать кабинет |
| PATCH | `/accounts/{id}` | Обновить (токен, CPL, Метрика) |
| DELETE | `/accounts/{id}` | Удалить со всеми данными |
| POST | `/accounts/{id}/sync?days=28` | Запустить сбор данных. `days=90` для ретроспективы |

### Дашборд

| Метод | Путь | Параметры | Описание |
|---|---|---|---|
| GET | `/accounts/{id}/dashboard` | `period`, `date_from`, `date_to`, `compare_from`, `compare_to` | Главный дашборд с KPI, дельтами, сигналами и Метрикой |

Параметры `date_from / date_to` задают произвольный диапазон (формат `YYYY-MM-DD`). Если указаны — имеют приоритет над `period`. `compare_from / compare_to` — ручной выбор периода сравнения; если не указаны, вычисляется автоматически.

### Графики (посуточные данные)

| Метод | Путь | Параметры | Описание |
|---|---|---|---|
| GET | `/accounts/{id}/daily-stats` | `date_from`, `date_to` (обязательные) | Посуточная статистика по всему кабинету. Для спарклайнов и графиков. |
| GET | `/accounts/{id}/campaigns/{cid}/daily-stats` | `date_from`, `date_to` (обязательные) | Посуточная статистика конкретной кампании. Для drill-down в таблице кампаний. |

### Кампании, группы, ключи

| Метод | Путь | Параметры | Описание |
|---|---|---|---|
| GET | `/accounts/{id}/campaigns` | `period`, `active_only`, `date_from`, `date_to`, `compare_from`, `compare_to` | Кампании с метриками, дельтами и сигналами |
| GET | `/accounts/{id}/ad-groups` | `campaign_id`, `period` | Группы объявлений с базовой статистикой |
| GET | `/accounts/{id}/keywords` | `period`, `campaign_id`, `ad_group_id`, `search`, `active_only`, `limit`, `date_from`, `date_to` | Ключи с метриками, сигналами, спарклайном |

### Анализ и оптимизация

| Метод | Путь | Описание |
|---|---|---|
| GET | `/accounts/{id}/analyses` | История анализов (последние 10) |
| GET | `/accounts/{id}/suggestions` | Сигналы и точки роста из последнего анализа |
| POST | `/suggestions/{id}/action` | Принять/отклонить сигнал → создать гипотезу |
| GET | `/accounts/{id}/hypotheses` | Список гипотез |
| POST | `/accounts/{id}/hypotheses` | Создать гипотезу вручную |

### Данные и диагностика

| Метод | Путь | Описание |
|---|---|---|
| GET | `/accounts/{id}/metrika-snapshot` | Последние два снапшота Метрики (текущий + предыдущий для сравнения) |
| GET | `/accounts/{id}/search-queries` | Поисковые запросы. `suggest=new_keywords\|negatives` для фильтрации |
| GET | `/accounts/{id}/rules` | Правила анализа |
| GET | `/accounts/{id}/diagnostics` | Статус системы: токен, счётчик, данные в БД, диапазон дат |
| GET | `/health` | Health check |

---

## 5. Важные детали API Яндекс Директ

### Статусы кампаний

| Поле | Значения | Смысл |
|---|---|---|
| `State` | `ON`, `SUSPENDED`, `ENDED`, `CONVERTED` | Статус запуска (включена/остановлена) |
| `Status` | `ACCEPTED`, `REJECTED`, `DRAFT`, `MODERATION` | Статус модерации |

**Активная кампания = `State: ON` И `Status: ACCEPTED`.**
Запрос уже фильтрует по этим полям, поэтому все пришедшие кампании активны — `is_active=True` проставляется безусловно.

### Единицы измерения

| Поле | Единица | Конвертация |
|---|---|---|
| `Bid`, `AvgEffectiveBid` | Микрорубли | `/1_000_000` → рубли |
| `AvgCpc`, `Cost` | Рубли | без конвертации |
| `Ctr`, `WeightedCtr` | Проценты (строка "5.23") | `float(v)` |
| `BounceRate` | Проценты, или `"--"` если нет данных | `float(v)` если не `"--"` |
| `AvgImpressionPosition`, `AvgClickPosition` | Число | без конвертации |
| `AvgTrafficVolume` | Число 0–150 | без конвертации |
| `WeightedImpressions` | Число | без конвертации |

### Расчёт CPC и CTR

Агрегированные метрики считаются через суммы, не через `avg()`:
```
CPC = sum(spend) / sum(clicks)          # не avg(avg_cpc)
CTR = sum(clicks) / sum(impressions) * 100   # не avg(ctr)
```
Среднее от средних некорректно при разном объёме кликов.

---

## 6. Фоновые задачи (Celery)

### Расписание
- `collect_and_analyze_all` — ежедневно в **06:00 МСК**
- `track_all_hypotheses` — ежедневно в **07:00 МСК**

### collect_account_data(account_id, days=28)

Порядок сбора:
1. Кампании (upsert, `is_active=True` для всех пришедших)
2. Группы объявлений (батчи по 10 кампаний)
3. Ключевые слова со ставками (`Bid` в микрорублях → конвертация)
4. Статистика по ключам за `days` дней — все поля включая `WeightedImpressions`, `WeightedCtr`, `BounceRate`
5. Поисковые запросы
6. Метрика (12 срезов → snapshot)
7. Обогащение `sessions` в `keyword_stats` по совпадению `utm_term → keyword.phrase`
8. Запуск анализа (`run_analysis.delay`)

---

## 7. Фронтенд

### Навигация

```
АНАЛИЗ
  ◉ Main Board          /
  ≡ По кампаниям        /campaigns
  ₽ Ставки              /bids
  ⊕ Корректировки       /adjustments

ПОИСКОВЫЕ ФРАЗЫ
  + Новые ключи         /new-keywords
  × Минуса              /negatives

ОПТИМИЗАЦИЯ
  ◈ Предложения         /suggestions
  ◇ Гипотезы            /hypotheses

СИСТЕМА
  ⊙ Кабинеты            /settings
  ≋ Правила             /rules
  ⚠ Диагностика         /diagnostics
```

### Переключение периода

Поддерживается на всех страницах: `Вчера / 3 дня / Неделя / Месяц / Период ↓`.

Логика сравнения:
- `yesterday` → вчера vs среднее за 14 дней (один день слишком волатилен)
- `3d / week / month` → текущий период vs предыдущий аналогичный
- `Период ↓` → произвольный диапазон через date-picker; поле сравнения опционально

### api.js — методы

```js
api.getAccounts()
api.createAccount(data)
api.updateAccount(id, data)
api.deleteAccount(id)
api.triggerSync(id)                              // стандартный сбор 28 дней
api.triggerHistoricalSync(id, days=90)           // ретроспективный сбор
api.getDashboard(id, period, extraParams)        // extraParams — строка date_from=...&date_to=...
api.getCampaigns(id, period, activeOnly, extra)
api.getAdGroups(id, campaignId)
api.getKeywords(id, params)                      // params — строка ?period=week&...
api.getDailyStats(id, dateFrom, dateTo)          // посуточные данные кабинета
api.getCampaignDailyStats(id, campId, dateFrom, dateTo)   // посуточные данные кампании
api.getSuggestions(id, params)
api.actionSuggestion(id, data)
api.getAnalyses(id)
api.getHypotheses(id)
api.createHypothesis(id, data)
api.getRules(id)
api.getMetrikaSnapshot(id)
api.getSearchQueries(id, params)
api.getDiagnostics(id)
```

---

## 8. Движок анализа и генерации сигналов

### Принцип работы

Запускается после каждого сбора данных. Анализирует `keyword_stats` за последние 28 дней и снапшот Метрики. Результат — объект `AnalysisResult` с полями `problems` (список сигналов) и `opportunities` (точки роста).

### Дерево диагностики (5 уровней)

```
Уровень 4: Ставки и ключи
  S-001  low_position          avg_position > 3, ключ теряет показы
  S-002  traffic_drop          клики упали > 40% при стабильном спросе
  S-003  zero_ctr              100+ показов, 0 кликов
  S-004  low_ctr               CTR < 1% при позиции ≤ 3
  S-005  click_position_gap    позиция клика хуже позиции показа на 1.5+
  S-006  spend_no_conversion   расход > 3× target_cpl без конверсий

Уровень 3: Показы / ЕПК
  S-010  epk_bid_collapse      ≥5 ключей кампании с обвалом ставки >50%

Уровень 2: Трафик
  S-020  cpc_spike             CPC вырос >40% к предыдущему периоду

Уровень 6: Поведение (Метрика)
  S-040  high_bounce_rate      bounce > 60% (критично > 75%)
  S-041  low_page_depth        глубина < 1.3 стр.
  S-042  low_visit_duration    время < 30 сек
  S-043  mobile_quality_issue  мобильный bounce > desktop + 20%

Уровень 7: Точки роста
  S-050  scale_opportunity     CTR > 5%, позиция > 2.5 — потенциал роста
```

### Структура сигнала

Каждый сигнал содержит:

| Поле | Описание |
|---|---|
| `signal_id` | Уникальный ID, например `S-001-1234` |
| `type` | Тип сигнала (`low_position`, `epk_bid_collapse` и т.д.) |
| `severity` | `critical` / `warning` / `info` |
| `priority` | `today` / `this_week` / `month` / `scale` |
| `layer` | `bid_keyword` / `impression` / `traffic` / `behavior` / `opportunity` |
| `keyword_id` | ID ключа (для ключевых сигналов) |
| `entity_type` / `entity_id` | Тип и ID объекта (для кампанийных сигналов) |
| `description` | Описание проблемы с конкретными числами |
| `hypothesis` | Предполагаемая причина |
| `action` | Рекомендуемое действие с конкретными значениями |
| `expected_outcome` | Ожидаемый результат |
| `calculation_logic` | Формула расчёта рекомендации |
| `recommended_bid` | Рекомендуемая ставка в рублях (для bid-сигналов) |
| `metric_value` | Значение метрики, вызвавшей сигнал |

### Предложения (Suggestions)

Каждый сигнал автоматически конвертируется в `Suggestion` со статусом `pending`. Директолог аппрувает или отклоняет на странице «Предложения». При аппруве создаётся `Hypothesis` для трекинга результата через 7 дней.

Дедупликация: повторный сигнал для уже существующего pending-предложения того же `(object_id, change_type)` не создаёт дубликата.

### Пороговые значения (v1.2)

```python
POS_CRITICAL = 4.0          # позиция показа — критично
POS_WARNING  = 3.0          # позиция показа — предупреждение
POS_GAP_WARNING = 1.5       # разрыв позиция_клика − позиция_показа
TRAFFIC_VOL_THRESHOLD = 50  # минимальный объём трафика для сигналов
TRAFFIC_DROP_FACTOR = 0.60  # клики упали > 40% → сигнал
CTR_ZERO_MIN_IMPRESSIONS = 100
CTR_LOW_THRESHOLD = 1.0     # %
CTR_SPIKE_FACTOR = 1.4      # CPC вырос > 40%
MIN_CLICKS_KW = 30          # порог статистической значимости по ключу
SPEND_NO_CONV_CLICKS = 30   # мин. кликов для сигнала "расход без конверсий"
SPEND_NO_CONV_MULT = 3.0    # > 3× target_cpl
EPK_COLLAPSE_MIN_KWS = 5    # мин. ключей для сигнала ЕПК-обвала
EPK_COLLAPSE_BID_DROP = 0.50
BOUNCE_RATE_WARNING = 60.0  # %
BOUNCE_RATE_CRITICAL = 75.0
```

---

## 9. Схема БД — ключевые таблицы

### keyword_stats — ключевые поля

| Поле | Тип | Источник | Версия |
|---|---|---|---|
| `clicks`, `impressions`, `spend` | INT / NUMERIC | Директ CRITERIA_PERFORMANCE_REPORT | 1.0 |
| `ctr` | NUMERIC 8,4 | Директ (%) | 1.0 |
| `avg_cpc` | NUMERIC 10,2 | Директ (₽) | 1.0 |
| `avg_bid` | NUMERIC 10,2 | Директ AvgEffectiveBid / 1_000_000 | 1.0 |
| `avg_position` | NUMERIC 5,2 | Директ AvgImpressionPosition | 1.0 |
| `avg_click_position` | NUMERIC 5,2 | Директ AvgClickPosition | 1.0 |
| `traffic_volume` | INT | Директ AvgTrafficVolume (0–150) | 1.0 |
| `weighted_impressions` | INT | Директ WeightedImpressions | **1.2** |
| `weighted_ctr` | NUMERIC 8,4 | Директ WeightedCtr (%) | **1.2** |
| `bounce_rate` | NUMERIC 6,2 | Директ BounceRate (%) | **1.2** |
| `sessions` | INT | Метрика, обогащение по utm_term | **1.2** |

### Уникальный ключ: `(account_id, keyword_id, date)`

---

## 10. Текущее состояние

### Уровень 1 — Рекламные данные + поведение ✅ Реализован полностью

Сбор Директ + Метрика, 12-сигнальный анализатор, генерация предложений, дашборд, все 11 страниц задеплоены.

### Уровень 2 — CRM (лиды) 🔲 Не реализован

Требует CSV-выгрузки из 1С. После подключения появятся MQL, CPL, SQL, CPQL. Таблица `leads` и `keyword_metrics` в схеме готовы, логика анализа — нет.

### Уровень 3 — Автоприменение изменений 🔲 Не реализован

Требует Директ API write-доступа и логики безопасности.

---

## 11. Известные ограничения и TODO

### Критические
- `oauth_token` хранится в открытом виде — нужно шифрование
- Матчинг `utm_term → sessions` приближённый: точный матчинг требует `client_id` / `roistat_id` (Уровень 2)

### Важно
- Страница «Группы» в разделе «По кампаниям» не реализована (только Кампании и Ключи)
- Экспорт предложений в CSV
- Уведомления (email/Telegram) при критических сигналах

### Планируется
- Шифрование oauth_token
- Уровень 2: подключение 1С через CSV
- Уровень 3: автоприменение ставок через Директ API write
- Отдельные посадочные страницы по маркам стали

---

## 12. Локальная разработка

```bash
git clone https://github.com/vkrn-work/ppc-optimaizer
cd ppc-optimaizer
docker-compose up -d

# Фронтенд
cd frontend && npm install && npm run dev   # http://localhost:3000

# Бэкенд
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload              # http://localhost:8000/docs

# Ручной запуск сбора
curl -X POST http://localhost:8000/api/v1/accounts/1/sync
curl -X POST http://localhost:8000/api/v1/accounts/1/sync?days=90

# Миграция v1.2 (один раз)
psql $DATABASE_URL -f backend/app/db/migrate_v1_2.sql
psql $DATABASE_URL -c "ALTER TYPE hypothesisverdict ADD VALUE IF NOT EXISTS 'neutral';"
```

---

*Актуально на апрель 2026. При значимых изменениях обновлять вместе с `docs/CHANGELOG.md`.*
