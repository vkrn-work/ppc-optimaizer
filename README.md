# PPC Optimizer

Система автоматического анализа и оптимизации рекламных кампаний Яндекс Директ.

---

## Что делает система

- Собирает данные из Яндекс Директ API v5 и Яндекс Метрики еженедельно
- Рассчитывает CR, CPL, CPQL по каждому ключу (скользящее окно 4 недели)
- Применяет двухуровневую модель ставок: кластерная база + индивидуальный коэффициент
- Генерирует список задач с приоритетами: Сегодня / Эта неделя / Месяц / Масштабирование
- Позволяет одобрять или отклонять предложения с инструкцией для ручного применения
- Трекает каждое изменение 7 дней и выдаёт вердикт: подтверждена / отклонена / нет данных
- Масштабируется на N кабинетов без переработки архитектуры

---

## Деплой на сервер (10 минут)

### Требования к серверу
- Ubuntu 22.04 или 24.04
- 2 vCPU, 4 GB RAM (минимум)
- Открытые порты: 3000 (фронтенд), 8000 (API)

### Шаг 1 — Скопировать проект на сервер

```bash
# Вариант А: через git (после загрузки в репозиторий)
git clone https://github.com/YOUR/ppc-optimizer.git
cd ppc-optimizer

# Вариант Б: через scp
scp -r ppc-optimizer/ user@YOUR_SERVER_IP:~/
ssh user@YOUR_SERVER_IP
cd ppc-optimizer
```

### Шаг 2 — Получить OAuth-токен Яндекс

1. Зайти на https://oauth.yandex.ru/
2. Создать приложение → платформа «Веб-сервисы»
3. Права: `direct:api` и `metrika:read`
4. Callback URI: `https://oauth.yandex.ru/verification_code`
5. Нажать «Получить токен» → скопировать

### Шаг 3 — Запустить деплой

```bash
sudo bash deploy.sh
```

Скрипт:
- Установит Docker, если его нет
- Сгенерирует случайные секреты
- Попросит заполнить `.env` (YANDEX_CLIENT_ID, YANDEX_CLIENT_SECRET)
- Соберёт и запустит все контейнеры
- Выдаст ссылку на дашборд

### Шаг 4 — Первый запуск

1. Открыть `http://YOUR_SERVER_IP:3000`
2. Добавить кабинет: логин Яндекс + OAuth-токен + целевой CPL
3. Нажать «↻ Собрать данные»
4. Через 5–10 минут появятся кампании, ключи и предложения

---

## Локальный запуск (разработка)

```bash
# Клонировать и перейти в папку
cd ppc-optimizer

# Создать .env
cp .env.example .env
# Заполнить YANDEX_CLIENT_ID, YANDEX_CLIENT_SECRET

# Запустить только базу и Redis
docker compose up -d db redis

# Бэкенд
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Worker (в отдельном терминале)
celery -A app.core.celery_app worker --loglevel=info

# Фронтенд (в отдельном терминале)
cd frontend
npm install
npm run dev
```

Дашборд: http://localhost:3000  
API docs: http://localhost:8000/docs

---

## Структура проекта

```
ppc-optimizer/
├── backend/
│   ├── app/
│   │   ├── api/routes.py          # REST API эндпоинты
│   │   ├── analyzers/cr_analyzer.py  # CR-анализ, двухуровневые ставки
│   │   ├── collectors/
│   │   │   ├── direct_collector.py   # Яндекс Директ API
│   │   │   └── metrika_collector.py  # Яндекс Метрика API
│   │   ├── core/
│   │   │   ├── celery_app.py      # Планировщик задач
│   │   │   ├── config.py          # Конфигурация и пороги CR
│   │   │   └── tasks.py           # Фоновые задачи
│   │   ├── db/database.py         # Подключение к PostgreSQL
│   │   ├── generators/suggestion_generator.py  # Генератор предложений
│   │   ├── models/models.py       # Схема БД (13 таблиц)
│   │   └── main.py                # FastAPI приложение + seed правил
│   └── requirements.txt
├── frontend/
│   └── src/pages/
│       ├── index.js               # Главный дашборд
│       ├── suggestions.js         # Предложения + аппрув
│       ├── campaigns.js           # Список кампаний
│       ├── keywords.js            # Ключи с CR-метриками
│       ├── hypotheses.js          # Трекинг гипотез
│       ├── rules.js               # База правил
│       └── settings.js            # Добавить кабинет
├── docker-compose.yml             # 5 сервисов
├── .env.example                   # Шаблон переменных
└── deploy.sh                      # Один скрипт деплоя
```

---

## Масштабирование на N кабинетов

Уже готово с первого дня — `account_id` есть в каждой таблице.

```python
# Добавить второй кабинет через API или Settings
POST /api/v1/accounts
{
  "name": "Кабинет 2",
  "yandex_login": "login2",
  "oauth_token": "token2",
  "target_cpl": 2000
}
# Сбор и анализ запустятся автоматически в следующий понедельник
# или вручную: POST /api/v1/accounts/2/sync
```

---

## Полезные команды

```bash
# Просмотр логов
docker compose logs -f backend
docker compose logs -f worker

# Перезапуск
docker compose restart backend

# Ручной запуск анализа (без ожидания понедельника)
# Через дашборд → кнопка «Собрать данные»
# Или через API: POST http://localhost:8000/api/v1/accounts/1/sync

# Остановка
docker compose down

# Полный сброс (удаляет данные)
docker compose down -v
```

---

## Фаза 2 — что добавляется поверх

- Прямое применение ставок через Директ API write (без ручного шага)
- API 1С для real-time матчинга лидов (замена CSV)
- JS-трекер на сайте (замена Roistat)
- AI-ассистент с SQL-доступом к данным
