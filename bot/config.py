"""Настройки бота из переменных окружения / файла .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_SOURCE_PAGES = (
    "https://cetatenie.just.ro/stadiu-dosar/",
    "https://cetatenie.just.ro/ordine-articolul-11/",
)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: str = "data/bot.db"
    anc_source_pages: tuple[str, ...] = DEFAULT_SOURCE_PAGES
    anc_pdf_filter: str = ""
    anc_proxy: str | None = None
    check_interval_min: int = 30


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")
    pages = tuple(_split(os.getenv("ANC_SOURCE_PAGES", ""))) or DEFAULT_SOURCE_PAGES
    return Settings(
        bot_token=token,
        admin_ids=frozenset(int(x) for x in _split(os.getenv("ADMIN_IDS", ""))),
        db_path=os.getenv("DB_PATH", "data/bot.db"),
        anc_source_pages=pages,
        anc_pdf_filter=os.getenv("ANC_PDF_FILTER", "").strip(),
        anc_proxy=os.getenv("ANC_PROXY", "").strip() or None,
        check_interval_min=max(5, int(os.getenv("CHECK_INTERVAL_MIN", "30"))),
    )
