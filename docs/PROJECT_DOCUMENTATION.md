# PPC Optimizer — Документация проекта

> Версия: 1.1.0 · Апрель 2026
> Статус: MVP Уровень 1 задеплоен и работает

---

## 1. Что это за приложение

**PPC Optimizer** — система автоматизации управления поисковыми рекламными кампаниями в Яндекс Директ для B2B-компании в нише импортного металлопроката.

Директолог тратит 10–15 часов в неделю на ручной сбор статистики и принятие решений по ставкам. Система заменяет рутину: собирает данные автоматически, анализирует динамику показателей, генерирует предложения по оптимизации.

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
│  /api/v1/ — 16 эндпоинтов                          │
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

Параметр `days` в `/sync`:
- `28` (по умолчанию) — стандартный еженедельный сбор
- `90` — ретроспективный сбор при первом подключении кабинета

### Дашборд

| Метод | Путь | Параметры | Описание |
|---|---|---|---|
| GET | `/accounts/{id}/dashboard` | `period=week\|yesterday\|3d\|month` | Главный дашборд с KPI и дельтами |

### Кампании, группы, ключи

| Метод | Путь | Параметры | Описание |
|---|---|---|---|
| GET | `/accounts/{id}/campaigns` | `period`, `active_only` | Список кампаний со статистикой |
| GET | `/accounts/{id}/ad-groups` | `campaign_id`, `period` | Группы объявлений с базовой статистикой |
| GET | `/accounts/{id}/keywords` | `period`, `campaign_id`, `ad_group_id`, `search`, `active_only`, `limit` | Ключи с дельтами и сигналами |

### Анализ и оптимизация

| Метод | Путь | Описание |
|---|---|---|
| GET | `/accounts/{id}/analyses` | История анализов (последние 10) |
| GET | `/accounts/{id}/suggestions` | Предложения из последнего анализа |
| POST | `/suggestions/{id}/action` | Принять/отклонить предложение → создать гипотезу |
| GET | `/accounts/{id}/hypotheses` | Список гипотез |
| POST | `/accounts/{id}/hypotheses` | Создать гипотезу вручную |

### Данные и диагностика

| Метод | Путь | Описание |
|---|---|---|
| GET | `/accounts/{id}/metrika-snapshot` | Последний снапшот Метрики |
| GET | `/accounts/{id}/search-queries` | Поисковые запросы с параметром `suggest=new_keywords\|negatives` |
| GET | `/accounts/{id}/rules` | Правила анализа |
| GET | `/accounts/{id}/diagnostics` | Статус системы: токен, счётчик, данные в БД |
| GET | `/health` | Health check с проверкой БД |

---

## 5. Важные детали API Яндекс Директ

### Статусы кампаний

Директ возвращает два разных поля — их нельзя путать:

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
| `Ctr` | Проценты (строка "5.23") | `float(v)` |
| `AvgImpressionPosition` | Число | без конвертации |

### Расчёт CPC и CTR

Агрегированные метрики считаются через суммы, не через `avg()`:
```
CPC = sum(spend) / sum(clicks)     # не avg(avg_cpc)
CTR = sum(clicks) / sum(impressions) * 100   # не avg(ctr)
```
Среднее от средних некорректно при разном объёме кликов.

---

## 6. Фоновые задачи (Celery)

### Расписание
- `collect_and_analyze_all` — ежедневно в **06:00 МСК**
- `track_all_hypotheses` — ежедневно в **07:00 МСК**

### collect_account_data(account_id, days=28)

Принимает параметр `days`:
- Стандартный запуск: `days=28`
- Ретроспективный сбор при первом подключении: `days=90` через `/sync?days=90`

Порядок сбора:
1. Кампании (upsert, `is_active=True` для всех пришедших)
2. Группы объявлений (батчи по 10 кампаний)
3. Ключевые слова со ставками (Bid в микрорублях → конвертация)
4. Статистика по ключам за `days` дней (AvgEffectiveBid → конвертация)
5. Поисковые запросы
6. Метрика (12 срезов → snapshot)
7. Запуск анализа (`run_analysis.delay`)

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

Поддерживается на всех страницах: `Вчера / 3 дня / Неделя / Месяц`.
Передаётся параметром `period` в каждый запрос к API.
Логика сравнения:
- `yesterday` → вчера vs среднее за 14 дней (один день слишком волатилен)
- `3d / week / month` → текущий период vs предыдущий аналогичный

### Время МСК

`last_sync_at` хранится в БД как UTC без суффикса. При отображении добавляется `Z` перед `new Date()` чтобы браузер правильно трактовал как UTC, затем конвертируется в `Europe/Moscow`.

### Страница Ставки (/bids)

- Показывает **все** кампании (ручные помечаются `✎`, автоматические `⚙`)
- При выборе кампании появляется фильтр по группам объявлений
- Столбцы: фраза, текущая ставка, рекомендуемая ставка, позиция показа, позиция клика, объём трафика, клики, Δ клики, CTR, CPC, расход, сигнал
- Калькулятор: `ставка = CPL × CR / 100`

### api.js — методы

```js
api.getAccounts()
api.createAccount(data)
api.updateAccount(id, data)
api.deleteAccount(id)
api.triggerSync(id)                        // стандартный сбор 28 дней
api.triggerHistoricalSync(id, days=90)     // ретроспективный сбор
api.getDashboard(id, period)               // period обязателен
api.getCampaigns(id, period, activeOnly)
api.getAdGroups(id, campaignId)            // новый в v1.1.0
api.getKeywords(id, params)
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

## 8. Алгоритм анализа

Запускается после каждого сбора. Анализирует `keyword_stats` за последние 28 дней.

### 5 типов проблем

| Тип | Условие | Severity | Приоритет |
|---|---|---|---|
| `low_position` | avg_position > 3 AND clicks ≥ 5 AND кампания ручная | critical/warning | today |
| `traffic_drop` | клики упали > 40% (prev > 10) AND трафик > 50 | critical | today |
| `zero_ctr` | impressions ≥ 100 AND clicks == 0 | warning | this_week |
| `low_ctr` | CTR < 1% AND impressions ≥ 50 AND позиция ≤ 3 | warning | this_week |
| `click_position_gap` | поз.клика > поз.показа + 1.5 AND clicks ≥ 5 | info | this_week |

### Рекомендованная ставка
- Позиция > 3 → `current_bid × 1.3`
- Позиция < 1.5 → `current_bid × 0.9`

---

## 9. Текущее состояние

### Уровень 1 — Рекламные + поведение ✅ Реализован
Сбор Директ + Метрика, анализ, дашборд, все 11 страниц задеплоены.

### Уровень 2 — CRM (лиды) 🔲 Не реализован
Требует CSV-выгрузки из 1С. После подключения появятся MQL, CPL, SQL, CPQL.

### Уровень 3 — Автоприменение изменений 🔲 Не реализован
Требует Директ API write-доступа и логики безопасности.

---

## 10. Известные ограничения и TODO

### Критические
- `oauth_token` хранится в открытом виде — нужно шифрование
- Страница "Группы" в разделе "По кампаниям" не реализована (только Кампании и Ключи)
- Скоринг поисковых фраз (коммерческий интент) в стадии базовой реализации

### Важно
- Мини-тренды (спарклайны) в таблице Ключей
- Экспорт предложений в CSV
- Страница Правил — редактор пользовательских правил

### Планируется
- Уведомления (email/Telegram) при критических сигналах
- Автоприменение изменений через Директ API (Уровень 3)
- Отдельные посадочные страницы по маркам стали (Уровень 2)

---

## 11. Локальная разработка

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
```

---

*Актуально на апрель 2026. При значимых изменениях обновлять вместе с `docs/CHANGELOG.md`.*
