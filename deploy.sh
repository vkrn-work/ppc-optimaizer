#!/bin/bash
# =============================================================
# PPC Optimizer — деплой на чистый сервер Ubuntu 22.04 / 24.04
# Запуск: bash deploy.sh
# =============================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── 1. Проверка прав ────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then error "Запустите как root: sudo bash deploy.sh"; fi

SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
info "IP сервера: $SERVER_IP"

# ── 2. Установка Docker ────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  info "Устанавливаю Docker..."
  apt-get update -q
  apt-get install -y -q ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker
  info "Docker установлен"
else
  info "Docker уже установлен"
fi

# ── 3. Настройка .env ────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  # Генерируем случайные секреты
  POSTGRES_PASS=$(openssl rand -hex 24)
  SECRET_KEY=$(openssl rand -hex 32)
  sed -i "s/ppc_secret_change_me/$POSTGRES_PASS/g" .env
  sed -i "s/change_this_to_random_64_char_string/$SECRET_KEY/g" docker-compose.yml 2>/dev/null || true
  echo "SECRET_KEY=$SECRET_KEY" >> .env

  warn "========================================================"
  warn "Файл .env создан. ОБЯЗАТЕЛЬНО заполните:"
  warn "  YANDEX_CLIENT_ID=..."
  warn "  YANDEX_CLIENT_SECRET=..."
  warn "  ALLOWED_ORIGINS=http://$SERVER_IP:3000"
  warn "========================================================"
  echo ""
  read -p "Нажмите Enter когда заполните .env (или Ctrl+C для отмены)..."
fi

# ── 4. Сборка и запуск ────────────────────────────────────────────────────
info "Собираю и запускаю контейнеры..."
docker compose pull --quiet 2>/dev/null || true
docker compose build --quiet
docker compose up -d

# ── 5. Ожидание готовности ───────────────────────────────────────────────
info "Жду запуска сервисов..."
sleep 10

MAX_WAIT=60
for i in $(seq 1 $MAX_WAIT); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    info "Бэкенд готов"
    break
  fi
  if [ $i -eq $MAX_WAIT ]; then
    warn "Бэкенд не ответил за ${MAX_WAIT}с. Проверьте: docker compose logs backend"
  fi
  sleep 2
done

# ── 6. Результат ────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}========================================================"
echo "  PPC Optimizer запущен!"
echo ""
echo "  Дашборд:  http://$SERVER_IP:3000"
echo "  API:      http://$SERVER_IP:8000"
echo "  API docs: http://$SERVER_IP:8000/docs"
echo ""
echo "  Следующий шаг:"
echo "  1. Откройте http://$SERVER_IP:3000"
echo "  2. Добавьте кабинет (OAuth-токен Яндекс)"
echo "  3. Нажмите «Собрать данные» — первый анализ через ~5 мин"
echo -e "========================================================${NC}"
