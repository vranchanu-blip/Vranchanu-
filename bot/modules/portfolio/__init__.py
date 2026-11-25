"""Модуль «Портфель»: пока — движение основных индексов США."""
from __future__ import annotations

import logging
from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from bot.core.registry import AppContext, Module
from bot.modules.portfolio.market import INDICES, MarketClient, Quote

log = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Bucharest")


def num(value: float, digits: int = 2) -> str:
    """6512.3456 -> «6 512,35»."""
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def change(value: float | None) -> str:
    if value is None:
        return "—"
    mark = "🟢" if value > 0 else "🔴" if value < 0 else "⚪"
    sign = "+" if value > 0 else ""
    return f"{mark} <b>{sign}{num(value)}%</b>"


def render(results: list[tuple[str, str, Quote | Exception]]) -> str:
    lines = ["📊 <b>Индексы США</b>", ""]
    open_any = False
    for raw_name, flag, q in results:
        name = escape(raw_name)
        if isinstance(q, Exception):
            lines += [f"{flag} <b>{name}</b> — нет данных", ""]
            continue
        open_any |= q.is_open
        lines += [
            f"{flag} <b>{name}</b>  <code>{num(q.price)}</code>",
            f"За день ({q.market_date:%d.%m}): {change(q.day_pct)}",
            f"С начала года: {change(q.ytd_pct)}",
            f"За 12 месяцев: {change(q.year_pct)}",
            "",
        ]
    status = "🟢 торги идут" if open_any else "🌙 биржа закрыта — цены последнего закрытия"
    lines += [
        f"<i>{status}</i>",
        f"<i>Yahoo Finance, задержка до 15 мин · обновлено {datetime.now(TZ):%H:%M}</i>",
    ]
    return "\n".join(lines)


def markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [Btn(text="🔄 Обновить", callback_data="pf:refresh")],
        [Btn(text="🏠 Главное меню", callback_data="core:main")],
    ])


def setup(ctx: AppContext) -> Module:
    router = Router(name="portfolio")
    client = MarketClient()
    ctx.shutdown.append(client.close)

    async def view() -> str:
        quotes = await client.quotes([s for s, _, _ in INDICES])
        for (symbol, _, _), q in zip(INDICES, quotes):
            if isinstance(q, Exception):
                log.warning("quote %s failed: %r", symbol, q)
        return render([(name, flag, q) for (_, name, flag), q in zip(INDICES, quotes)])

    @router.callback_query(F.data.in_({"m:portfolio", "pf:refresh"}))
    async def show(call: CallbackQuery) -> None:
        text = await view()
        try:
            await call.message.edit_text(text, reply_markup=markup())
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc):
                raise
        await call.answer("Обновлено" if call.data == "pf:refresh" else None)

    @router.message(Command("market"))
    async def market_cmd(message: Message) -> None:
        await message.answer(await view(), reply_markup=markup())

    return Module(key="portfolio", title="💼 Портфель", router=router)
