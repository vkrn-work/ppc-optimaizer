# PPC Optimizer

Система автоматизации управления поисковыми рекламными кампаниями в Яндекс Директ для B2B-компании в нише импортного металлопроката.

**Версия:** 1.1.0 · **Деплой:** Railway · **Статус:** MVP Уровень 1 работает

| Сервис | URL |
|---|---|
| Фронтенд | https://ppc-optimizer.up.railway.app |
| Бэкенд API | https://ppc-optimaizer-production.up.railway.app/docs |

---

## Документация

| Файл | Описание |
|---|---|
| [docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md) | Техническая документация: архитектура, API, БД, алгоритмы |
| [docs/BUSINESS_CONTEXT.md](docs/BUSINESS_CONTEXT.md) | Бизнес-контекст: ниша, методология анализа, логика ставок |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Дорожная карта: что сделано, что планируется |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | История изменений по версиям |

---

## Что делает система

- Собирает данные из Яндекс Директ API v5 и Яндекс Метрики ежедневно (06:00 МСК)
- Анализирует динамику показателей: позиции, CTR, клики, расход
- Генерирует предложения по оптимизации с приоритетами (Сегодня / Неделя / Месяц)
- Трекает каждое изменение 7 дней и выдаёт вердикт: подтверждена / отклонена / нет данных
- Поддерживает несколько кабинетов одновременно

---

## Быстрый старт (локальная разработка)

```bash
git clone https://github.com/vkrn-work/ppc-optimaizer
cd ppc-optimaizer
docker-compose up -d

# Фронтенд: http://localhost:3000
# API docs: http://localhost:8000/docs
```

Подробнее — в [docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md), раздел «Локальная разработка».

---

## Стек

| Компонент | Технология |
|---|---|
| Фронтенд | Next.js 14 + React 18 |
| Бэкенд | FastAPI + Python 3.12 |
| БД | PostgreSQL + SQLAlchemy async |
| Очереди | Celery + Redis |
| Деплой | Railway (3 сервиса) |
