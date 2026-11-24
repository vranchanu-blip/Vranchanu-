"""Модуль «Портфель» — заготовка, будет следующим этапом."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.core.registry import AppContext, Module


def setup(ctx: AppContext) -> Module:
    router = Router(name="portfolio")

    @router.callback_query(F.data == "m:portfolio")
    async def stub(call: CallbackQuery) -> None:
        await call.message.edit_text(
            "💼 <b>Инвестиционный портфель</b>\n\nРаздел в разработке.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="core:main")]
            ]),
        )
        await call.answer()

    return Module(key="portfolio", title="💼 Портфель (скоро)", router=router)
