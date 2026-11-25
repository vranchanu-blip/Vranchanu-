"""Главное меню, /start, /help и админ-команды ядра."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.core.registry import AppContext, Module

log = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Bucharest")

HELP = (
    "ℹ️ <b>Помощь</b>\n\n"
    "🌤 <b>Погода</b> — прогноз по вашей геопозиции: сейчас, ближайшие часы и 3 дня вперёд. "
    "Место запоминается, потом достаточно нажать «🔄 Обновить».\n\n"
    "💼 <b>Портфель</b> — S&amp;P 500 и Nasdaq-100: изменение за день и с начала года.\n\n"
    "<b>Команды</b>\n"
    "/start — главное меню\n"
    "/weather — погода\n"
    "/market — индексы\n"
    "/help — эта справка"
)


def greeting(name: str | None) -> str:
    hour = datetime.now(TZ).hour
    hello = (
        "Доброе утро" if 5 <= hour < 12 else
        "Добрый день" if 12 <= hour < 18 else
        "Добрый вечер" if 18 <= hour < 23 else
        "Доброй ночи"
    )
    who = f", {escape(name)}" if name else ""
    return f"👋 <b>{hello}{who}!</b>\n\nЧем займёмся?"


def main_menu_kb(modules: list[Module]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=m.title, callback_data=f"m:{m.key}") for m in modules if m.enabled]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="ℹ️ Помощь", callback_data="core:help")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="core:main")]]
    )


def setup_core(ctx: AppContext, modules: list[Module]) -> Router:
    router = Router(name="core")
    admins = ctx.settings.admin_ids

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(greeting(message.from_user.first_name), reply_markup=main_menu_kb(modules))

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(HELP, reply_markup=back_to_main_kb())

    @router.callback_query(F.data == "core:main")
    async def main_cb(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await call.message.edit_text(greeting(call.from_user.first_name), reply_markup=main_menu_kb(modules))
        await call.answer()

    @router.callback_query(F.data == "core:help")
    async def help_cb(call: CallbackQuery) -> None:
        await call.message.edit_text(HELP, reply_markup=back_to_main_kb())
        await call.answer()

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Отменено.\n\n" + greeting(message.from_user.first_name), reply_markup=main_menu_kb(modules))

    # ---------- админка ----------
    @router.message(Command("admin"), F.from_user.id.in_(admins))
    async def admin(message: Message) -> None:
        users = await ctx.db.fetchone("SELECT COUNT(*) AS c FROM users")
        await message.answer(
            "🛠 <b>Админ</b>\n"
            f"Пользователей: {users['c']}\n\n"
            "/update — обновить бота (только на сервере)\n"
            "/broadcast текст — рассылка всем"
        )

    @router.message(Command("update"), F.from_user.id.in_(admins))
    async def update(message: Message) -> None:
        # На сервере бот работает как служба systemd: после выхода она перезапускает его,
        # а перед запуском подтягивает свежий код (см. deploy/install.sh).
        await message.answer("🔄 Перезапускаюсь с обновлением, это займёт 1–3 минуты. Потом отправьте /start.")
        asyncio.get_running_loop().call_later(1, os.kill, os.getpid(), signal.SIGTERM)

    @router.message(Command("broadcast"), F.from_user.id.in_(admins))
    async def broadcast(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("Использование: /broadcast текст")
            return
        rows = await ctx.db.fetchall("SELECT id FROM users")
        sent = await send_many(ctx, [(r["id"], command.args) for r in rows])
        await message.answer(f"Отправлено: {sent} из {len(rows)}")

    return router


async def send_many(ctx: AppContext, items: list[tuple[int, str]]) -> int:
    """Аккуратная рассылка с учётом лимитов Telegram."""
    sent = 0
    for chat_id, text in items:
        for _ in range(3):
            try:
                await ctx.bot.send_message(chat_id, text, disable_web_page_preview=True)
                sent += 1
                break
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
            except TelegramForbiddenError:
                break  # пользователь заблокировал бота
            except Exception:  # noqa: BLE001
                log.exception("send to %s failed", chat_id)
                break
        await asyncio.sleep(0.05)
    return sent
