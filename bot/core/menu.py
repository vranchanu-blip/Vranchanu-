"""Главное меню, /start, /help и админ-команды ядра."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.core.registry import AppContext, Module

log = logging.getLogger(__name__)

WELCOME = (
    "👋 <b>Привет!</b> Это ваш личный помощник.\n\n"
    "Выберите раздел:"
)

HELP = (
    "<b>Что умеет бот</b>\n\n"
    "📁 <b>Досар</b> — отслеживание досаров на гражданство Румынии по ст. 11 "
    "(номер вида <code>12345/RD/2021</code>). Бот регулярно проверяет сайт ANC "
    "(cetatenie.just.ro), ищет ваш номер в списках «Stadiu dosar» и в приказах (Ordine) "
    "и сразу присылает уведомление, когда номер появляется или строка о нём меняется.\n\n"
    "Команды:\n"
    "/start — главное меню\n"
    "/dosar — раздел «Досар»\n"
    "/check 12345/RD/2021 — разовая проверка любого номера\n"
    "/help — эта справка"
)


def main_menu_kb(modules: list[Module]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=m.title, callback_data=f"m:{m.key}")] for m in modules if m.enabled]
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
        await message.answer(WELCOME, reply_markup=main_menu_kb(modules))

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(HELP, reply_markup=back_to_main_kb())

    @router.callback_query(F.data == "core:main")
    async def main_cb(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await call.message.edit_text(WELCOME, reply_markup=main_menu_kb(modules))
        await call.answer()

    @router.callback_query(F.data == "core:help")
    async def help_cb(call: CallbackQuery) -> None:
        await call.message.edit_text(HELP, reply_markup=back_to_main_kb())
        await call.answer()

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_kb(modules))

    # ---------- админка ----------
    @router.message(Command("admin"), F.from_user.id.in_(admins))
    async def admin(message: Message) -> None:
        db = ctx.db
        users = await db.fetchone("SELECT COUNT(*) AS c FROM users")
        dosars = await db.fetchone("SELECT COUNT(*) AS c, COUNT(DISTINCT number) AS u FROM dosars")
        last = await db.get_kv("anc_last_cycle_at") or "—"
        last_ok = await db.get_kv("anc_last_success_at") or "—"
        summary = await db.get_kv("anc_last_cycle_summary") or "—"
        await message.answer(
            "<b>Админ</b>\n"
            f"Пользователей: {users['c']}\n"
            f"Досаров: {dosars['c']} (уникальных: {dosars['u']})\n"
            f"Последняя проверка ANC: {last}\n"
            f"Последняя успешная: {last_ok}\n"
            f"Итог: {summary}\n\n"
            "/refresh — проверить ANC сейчас\n"
            "/broadcast текст — рассылка всем"
        )

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
