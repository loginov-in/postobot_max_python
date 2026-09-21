#!/usr/bin/env bash
#
# =============================================================================
#  Deploy PostoBot на VDS (Ubuntu / Debian)
# =============================================================================
#  Что разворачивает:
#   1. Веб-панель (Flask) под gunicorn  -> nginx -> HTTPS на домене postobot.su
#   2. Бота (bot.py, long polling) под systemd
#
#  Запуск — НА САМОМ VDS, от root:
#       bash html/deploy_vds.sh
#
#  Перед запуском:
#     * домен postobot.su должен смотреть A-записью на IP сервера;
#     * в .env проекта заполнен MAX_BOT (токен бота).
#
#  Переменные окружения (необязательно, есть дефолты):
#     DOMAIN=postobot.su  CERT_EMAIL=...  WEB_PORT=1309  RUN_AS=postobot
# =============================================================================

set -euo pipefail

# --- Конфигурация -----------------------------------------------------------
DOMAIN="${DOMAIN:-postobot.su}"
CERT_EMAIL="${CERT_EMAIL:-admin@${DOMAIN}}"
WEB_PORT="${WEB_PORT:-1309}"
RUN_AS="${RUN_AS:-postobot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"   # корень проекта (родитель html/)
VENV_DIR="${PROJECT_DIR}/.venv"
REQ_FILE="${PROJECT_DIR}/requirements.txt"

WEB_SERVICE="postobot-web"
BOT_SERVICE="postobot-bot"
NGINX_SITE="${DOMAIN}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
fail()  { echo -e "${RED}[error]${NC} $*"; exit 1; }

# --- 0. Проверки окружения --------------------------------------------------
[ "$(id -u)" -eq 0 ] || fail "Запустите скрипт от root: sudo bash html/deploy_vds.sh"
command -v apt-get >/dev/null 2>&1 || fail "Скрипт рассчитан на Ubuntu/Debian (нужен apt-get)."

PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || echo "0 0")
py_major=${PY_MAJOR% *}; py_minor=${PY_MAJOR#* }
if [ "${py_major:-0}" -lt 10 ] || { [ "${py_major:-0}" -eq 10 ] && [ "${py_minor:-0}" -lt 10 ]; }; then
    fail "Нужен Python 3.10+ (сейчас $PY_MAJOR)."
fi

info "Проект:   $PROJECT_DIR"
info "Домен:    $DOMAIN"
info "Порт панели: $WEB_PORT (внутренний, за nginx)"

# --- 1. Системные пакеты ----------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
info "Обновляю пакеты..."
apt-get update -y

info "Устанавливаю python3-venv, nginx, certbot..."
apt-get install -y \
    python3 python3-venv python3-dev gcc \
    nginx \
    certbot python3-certbot-nginx

# --- 2. Системный пользователь для сервисов ---------------------------------
if ! id "${RUN_AS}" >/dev/null 2>&1; then
    info "Создаю системного пользователя '${RUN_AS}'..."
    useradd --system --create-home --shell /usr/sbin/nologin "${RUN_AS}"
fi
info "Владелец проекта -> ${RUN_AS}..."
chown -R "${RUN_AS}:${RUN_AS}" "${PROJECT_DIR}"

# --- 3. Виртуальное окружение ----------------------------------------------
if [ ! -x "${VENV_DIR}/bin/python" ]; then
    info "Создаю venv..."
    python3 -m venv --copies "${VENV_DIR}"
fi
info "Устанавливаю зависимости из requirements.txt..."
"${VENV_DIR}/bin/pip" install --upgrade pip -q
"${VENV_DIR}/bin/pip" install -r "${REQ_FILE}" -q

# --- 4. Файл .env -----------------------------------------------------------
ENV_FILE="${PROJECT_DIR}/.env"
if [ ! -f "${ENV_FILE}" ]; then
    warn "Нет ${ENV_FILE} — создаю шаблон. Заполните MAX_BOT перед запуском бота."
    cat > "${ENV_FILE}" <<EOF
# Токен бота из профиля на business.max.ru
MAX_BOT=
# ID администратора чата (для команд /all, /archive, /restore)
ADMIN_ID=0
EOF
fi

if ! grep -qE '^MAX_BOT=.+' "${ENV_FILE}" 2>/dev/null; then
    warn "MAX_BOT пуст в .env — бот и вход в панель будут недоступны до заполнения токена."
else
    info "MAX_BOT задан."
fi

# --- 5. systemd-службы ------------------------------------------------------
info "Пишу systemd-юниты..."

cat > "/etc/systemd/system/${WEB_SERVICE}.service" <<EOF
[Unit]
Description=PostoBot web panel (Flask / gunicorn)
After=network.target

[Service]
Type=simple
User=${RUN_AS}
Group=${RUN_AS}
WorkingDirectory=${PROJECT_DIR}/html
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/gunicorn --workers 2 --bind 127.0.0.1:${WEB_PORT} \\
    --access-logfile - --error-logfile - web_server:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > "/etc/systemd/system/${BOT_SERVICE}.service" <<EOF
[Unit]
Description=PostoBot bot (long polling)
After=network.target ${WEB_SERVICE}.service
Wants=${WEB_SERVICE}.service

[Service]
Type=simple
User=${RUN_AS}
Group=${RUN_AS}
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
if systemctl enable --now "${WEB_SERVICE}" "${BOT_SERVICE}" >/dev/null 2>&1; then
    info "Службы ${WEB_SERVICE} и ${BOT_SERVICE} запущены."
else
    warn "Службы включены, но старт дал ошибку (ниже будут подсказки по логам)."
fi

# --- 6. nginx ---------------------------------------------------------------
info "Настраиваю nginx для ${DOMAIN}..."
rm -f /etc/nginx/sites-enabled/default

cat > "/etc/nginx/sites-available/${NGINX_SITE}" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    client_max_body_size 4m;

    location / {
        proxy_pass http://127.0.0.1:${WEB_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
    }
}
EOF

ln -sfn "/etc/nginx/sites-available/${NGINX_SITE}" "/etc/nginx/sites-enabled/${NGINX_SITE}"
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx
info "nginx работает (HTTP)."

# --- 7. SSL (Let's Encrypt) -------------------------------------------------
if [ -d "/etc/letsencrypt/live/${DOMAIN}" ]; then
    info "SSL-сертификат уже есть, не перевыпускаю."
else
    info "Выпускаю SSL-сертификат для https://${DOMAIN} ..."
    if certbot --nginx -d "${DOMAIN}" \
        --non-interactive --agree-tos -m "${CERT_EMAIL}" \
        --redirect --keep-until-expiring; then
        info "SSL выпущен, включено перенаправление на HTTPS."
    else
        warn "Не удалось получить сертификат (DNS/порты?)."
        warn "Когда домен будет доступен, запустите вручную:"
        warn "  certbot --nginx -d ${DOMAIN} --redirect"
    fi
fi

# --- 8. Проверка ------------------------------------------------------------
sleep 2
if curl -fsS -o /dev/null "http://127.0.0.1:${WEB_PORT}/apps"; then
    info "Панель отвечает на бэкенде: http://127.0.0.1:${WEB_PORT}/apps"
else
    warn "Панель не отвечает. Смотрите: journalctl -u ${WEB_SERVICE} -e"
fi
systemctl is-active --quiet "${BOT_SERVICE}" \
    && info "Бот запущен." \
    || warn "Бот не запущен. Смотрите: journalctl -u ${BOT_SERVICE} -e"

echo
echo -e "${GREEN}=== Готово ===${NC}"
echo "Панель (внешний адрес): https://${DOMAIN}/apps"
echo "Бот работает под: ${BOT_SERVICE}"
echo
echo "Полезные команды:"
echo "  systemctl status  ${WEB_SERVICE} ${BOT_SERVICE}"
echo "  journalctl -u ${BOT_SERVICE} -f"
echo "  journalctl -u ${WEB_SERVICE} -f"
echo
echo "Если на сервере включён ufw, откройте порты:"
echo "  ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp"
echo "https://${DOMAIN}/apps"