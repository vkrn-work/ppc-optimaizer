# Деплой на Railway — пошаговая инструкция

Время: ~20 минут. Результат: рабочий дашборд по публичной ссылке.

---

## Шаг 1 — Загрузить код на GitHub

1. Зайти на https://github.com и создать аккаунт (если нет)
2. Нажать **New repository** → назвать `ppc-optimizer` → **Create repository**
3. На своём компьютере:

```bash
# Распаковать zip
unzip ppc-optimizer.zip
cd ppc-optimizer

# Инициализировать git и загрузить
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/ВАШ_ЛОГИН/ppc-optimizer.git
git push -u origin main
```

> Если git не установлен: https://git-scm.com/downloads

---

## Шаг 2 — Создать проект на Railway

1. Зайти на https://railway.app
2. **Sign in with GitHub** (авторизуйтесь через GitHub-аккаунт)
3. Нажать **New Project**
4. Выбрать **Deploy from GitHub repo** → выбрать `ppc-optimizer`

---

## Шаг 3 — Добавить PostgreSQL

1. В проекте нажать **+ New** → **Database** → **Add PostgreSQL**
2. Railway автоматически создаст базу и добавит переменную `DATABASE_URL`
3. Запомните: нажать на PostgreSQL → вкладка **Variables** → скопировать `DATABASE_URL`

---

## Шаг 4 — Добавить Redis

1. Нажать **+ New** → **Database** → **Add Redis**
2. Railway создаст Redis и добавит `REDIS_URL`

---

## Шаг 5 — Настроить бэкенд

1. В проекте Railway нажать на сервис из GitHub (он создался автоматически)
2. Вкладка **Settings** → **Root Directory** → написать `backend`
3. Вкладка **Variables** → добавить переменные:

```
YANDEX_CLIENT_ID        = (ваш ID приложения Яндекс)
YANDEX_CLIENT_SECRET    = (ваш секрет приложения Яндекс)
SECRET_KEY              = (любые 32 случайных символа, например: abc123xyz...)
ENVIRONMENT             = production
ALLOWED_ORIGINS         = https://ДОМЕН_ФРОНТЕНДА.up.railway.app
```

> DATABASE_URL и REDIS_URL Railway подставит автоматически из добавленных баз.

4. Вкладка **Settings** → **Deploy** → Start Command:
```
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

5. Нажать **Deploy** → дождаться зелёного статуса

---

## Шаг 6 — Добавить Celery worker

1. В проекте нажать **+ New** → **GitHub Repo** → тот же репозиторий
2. **Settings** → **Root Directory** → `backend`
3. **Settings** → Start Command:
```
celery -A app.core.celery_app worker --beat --loglevel=info -Q default,analysis
```
4. **Variables** → добавить те же переменные что в шаге 5
   (DATABASE_URL и REDIS_URL — снова добавить вручную, скопировав из баз)
5. Deploy

---

## Шаг 7 — Добавить фронтенд

1. **+ New** → **GitHub Repo** → тот же репозиторий
2. **Settings** → **Root Directory** → `frontend`
3. **Variables**:
```
NEXT_PUBLIC_API_URL = https://ДОМЕН_БЭКЕНДА.up.railway.app
```
4. Start Command: `node .next/standalone/server.js`
5. Deploy

---

## Шаг 8 — Получить ссылки

Для каждого сервиса: **Settings** → **Networking** → **Generate Domain**

Вы получите три ссылки вида:
- Бэкенд: `https://ppc-optimizer-backend-xxxx.up.railway.app`
- Фронтенд: `https://ppc-optimizer-frontend-xxxx.up.railway.app`

Обновите переменную `ALLOWED_ORIGINS` в бэкенде на реальный домен фронтенда.
Обновите `NEXT_PUBLIC_API_URL` во фронтенде на реальный домен бэкенда.

---

## Шаг 9 — Первый вход

1. Открыть ссылку фронтенда
2. Добавить кабинет: логин Яндекс + OAuth-токен + целевой CPL
3. Нажать **↻ Собрать данные**
4. Через 5–10 минут появятся кампании, ключи и предложения

---

## Как получить OAuth-токен Яндекс

1. https://oauth.yandex.ru → **Создать приложение**
2. Платформа: **Веб-сервисы**
3. Права: отметить `direct:api` и `metrika:read`
4. Callback URI: `https://oauth.yandex.ru/verification_code`
5. Создать → нажать **Получить токен** → скопировать токен

---

## Бесплатный лимит Railway

$5 free credits в месяц. При 3 сервисах (бэкенд + worker + фронтенд) + 2 базах:
- Приблизительный расход: ~$3–4/мес при умеренном использовании
- Для тестирования хватит на 1–2 месяца бесплатно
- Когда перейдёте на Hetzner — просто поменяете `deploy.sh`

---

## Если что-то пошло не так

```
# Посмотреть логи — в Railway: сервис → вкладка Logs

# Частые проблемы:
# 1. "relation does not exist" — БД не инициализировалась
#    Решение: перезапустить бэкенд (Deploy → Redeploy)

# 2. "Connection refused redis" — REDIS_URL не добавлен в worker
#    Решение: добавить переменную REDIS_URL в Variables воркера

# 3. CORS error в браузере — ALLOWED_ORIGINS не совпадает с доменом фронтенда
#    Решение: обновить ALLOWED_ORIGINS в Variables бэкенда
```
