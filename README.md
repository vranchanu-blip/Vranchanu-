# Личная экосистема в Telegram

Модульный Telegram-бот (Python, aiogram 3, SQLite). Каждый раздел — отдельный модуль
в `bot/modules/`, главное меню собирается из списка модулей автоматически.

| Модуль | Статус |
|---|---|
| 🌤 **Погода** — прогноз по геопозиции | готов |
| 💼 **Портфель** — индексы S&P 500 и Nasdaq (за день и с начала года) | в работе |

## Погода

- пользователь отправляет геопозицию кнопкой (или любую точку через 📎 → «Геопозиция»);
- место запоминается, дальше — кнопка «🔄 Обновить»;
- сейчас (температура, ощущается как, ветер, влажность, восход/закат),
  ближайшие часы и прогноз на 3 дня;
- данные: [Open-Meteo](https://open-meteo.com) (бесплатно, без ключа),
  название места — OpenStreetMap Nominatim.

## Запуск

Сервер (Ubuntu): `deploy/install.sh` ставит бота как службу systemd.
Обновление — команда `/update` админу в Telegram (служба при старте делает `git pull`).

Локально:

```bash
cp .env.example .env      # вписать BOT_TOKEN и свой ADMIN_IDS
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m bot
```

## Разработка

```bash
pip install -r requirements-dev.txt
pytest
```

Новый модуль: пакет `bot/modules/<name>/` с функцией `setup(ctx) -> Module`
(см. `bot/modules/weather`), затем добавить его в `MODULES` в `bot/__main__.py`.
