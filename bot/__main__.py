"""Точка входа: python -m bot"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, TelegramObject, User

from bot.config import load_settings
from bot.core.menu import setup_core
from bot.core.registry import AppContext
from bot.db import Database
from bot.modules import portfolio, weather

# Порядок = порядок кнопок в главном меню.
MODULES = [weather, portfolio]


class UserMiddleware(BaseMiddleware):
    """Регистрирует/обновляет пользователя в БД при каждом апдейте."""

    def __init__(self, db: Database):
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user and not user.is_bot:
            await self.db.upsert_user(user.id, user.username, user.first_name)
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    db = Database(settings.db_path)
    await db.connect()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    ctx = AppContext(settings=settings, db=db, bot=bot)

    loaded = [m.setup(ctx) for m in MODULES]
    dp = Dispatcher()
    dp.update.outer_middleware(UserMiddleware(db))
    dp.include_router(setup_core(ctx, loaded))
    for module in loaded:
        dp.include_router(module.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="weather", description="Погода"),
        BotCommand(command="market", description="Индексы S&P 500 и Nasdaq-100"),
        BotCommand(command="help", description="Помощь"),
    ])

    tasks = [asyncio.create_task(job()) for job in ctx.background]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        for close in ctx.shutdown:
            await close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
