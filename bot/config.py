"""Настройки бота из переменных окружения / файла .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: str = "data/bot.db"


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")
    return Settings(
        bot_token=token,
        admin_ids=frozenset(int(x) for x in _split(os.getenv("ADMIN_IDS", ""))),
        db_path=os.getenv("DB_PATH", "data/bot.db"),
    )
