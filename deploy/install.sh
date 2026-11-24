#!/bin/bash
# Установка бота на чистый сервер Ubuntu (Oracle Cloud и др.).
# Использование (как root):
#   BOT_TOKEN=... ADMIN_IDS=... bash install.sh
# Повторный запуск безопасен: обновляет код и перезапускает бота.
set -euo pipefail

REPO="https://github.com/vranchanu-blip/Vranchanu-.git"
BRANCH="${BRANCH:-claude/loving-cannon-d7sae4}"
APP=/opt/bot
USER_NAME=bot

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-venv

id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$USER_NAME"

if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch origin "$BRANCH"
  git -C "$APP" checkout -B "$BRANCH" FETCH_HEAD
else
  git clone --branch "$BRANCH" "$REPO" "$APP"
fi

python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

mkdir -p "$APP/data"
if [ -n "${BOT_TOKEN:-}" ]; then
  umask 077
  cat > "$APP/.env" <<ENV
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=${ADMIN_IDS:-}
DB_PATH=$APP/data/bot.db
ENV
  umask 022
fi
[ -f "$APP/.env" ] || { echo "Нет BOT_TOKEN: запустите снова с BOT_TOKEN=..."; exit 1; }
chown -R "$USER_NAME:$USER_NAME" "$APP"

cat > /etc/systemd/system/bot.service <<UNIT
[Unit]
Description=Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python -m bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable bot
systemctl restart bot
echo "Бот установлен и запущен. Логи: journalctl -u bot -f"
