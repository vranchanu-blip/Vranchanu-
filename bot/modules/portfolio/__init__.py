"""Модуль «Портфель»: рынки (индексы, сырьё, крипта) и портфель Interactive Brokers."""
from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from bot.core.registry import AppContext, Module
from bot.db import utcnow
from bot.modules.portfolio.ibkr import IbkrClient, IbkrError, Portfolio
from bot.modules.portfolio.market import GROUPS, Instrument, MarketClient, Quote

log = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Bucharest")
MAX_POSITIONS = 20

IBKR_HELP = (
    "🏦 <b>Подключение Interactive Brokers</b>\n\n"
    "Бот читает отчёт IBKR <b>только на чтение</b>: позиции и наличные. "
    "Покупать, продавать или выводить деньги он не может.\n\n"
    "<b>1. Отчёт (Flex Query)</b>\n"
    "Кабинет IBKR (сайт) → <b>Performance &amp; Reports → Flex Queries</b> → "
    "<b>Activity Flex Query</b> → «<b>+</b>».\n"
    "• Название: <code>bot</code>\n"
    "• Разделы: <b>Account Information</b>, <b>Open Positions</b> (вариант <i>Summary</i>), "
    "<b>Cash Report</b> — в каждом «Select All» → Save\n"
    "• Format: <b>XML</b>, Period: <b>Last Business Day</b>\n"
    "• Save → запомните <b>Query ID</b> (число).\n\n"
    "<b>2. Токен</b>\n"
    "На той же странице: <b>Flex Web Service Configuration</b> (⚙️) → включите → "
    "<b>Generate New Token</b> (срок — 1 год) → скопируйте.\n\n"
    "<b>3.</b> Нажмите «🔗 Подключить» и отправьте боту токен, затем Query ID.\n"
    "<i>Токен не присылайте никому, кроме бота. Бот сразу удалит сообщение с ним.</i>"
)


class IbkrSetup(StatesGroup):
    token = State()
    query = State()


def num(value: float, digits: int = 2) -> str:
    """6512.3456 -> «6 512,35»."""
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def qty(value: float) -> str:
    return num(value, 0) if value == int(value) else num(value, 4).rstrip("0").rstrip(",")


def change(value: float | None) -> str:
    if value is None:
        return "—"
    mark = "🟢" if value > 0 else "🔴" if value < 0 else "⚪"
    sign = "+" if value > 0 else ""
    return f"{mark} <b>{sign}{num(value)}%</b>"


def money(value: float, cur: str) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{num(value)} {cur}"


def render_markets(groups: list[tuple[str, bool, list[tuple[Instrument, Quote | Exception]]]]) -> str:
    lines: list[str] = []
    us_open: bool | None = None
    for title, us_market, items in groups:
        lines += [title, ""]
        for inst, q in items:
            name = escape(inst.name)
            if isinstance(q, Exception):
                lines += [f"{inst.icon} <b>{name}</b> — нет данных", ""]
                continue
            if us_market:
                us_open = bool(us_open) or q.is_open
            unit = f" <i>{escape(inst.unit)}</i>" if inst.unit else ""
            lines += [
                f"{inst.icon} <b>{name}</b>  <code>{num(q.price)}</code>{unit}",
                f"За день ({q.market_date:%d.%m}): {change(q.day_pct)}",
                f"С начала года: {change(q.ytd_pct)}",
                "",
            ]
    if us_open is not None:
        lines.append("<i>" + ("🟢 биржа США: торги идут" if us_open
                              else "🌙 биржа США закрыта — цены последнего закрытия") + "</i>")
    lines.append(f"<i>Yahoo Finance, задержка до 15 мин · обновлено {datetime.now(TZ):%H:%M}</i>")
    return "\n".join(lines)


def mask(account: str) -> str:
    return account[:1] + "•••" + account[-4:] if len(account) > 5 else account


def render_ibkr(pf: Portfolio) -> str:
    cur = escape(pf.base)
    total = pf.total
    lines = [
        "🏦 <b>Портфель IBKR</b>" + (f" · {', '.join(mask(a) for a in pf.accounts)}" if pf.accounts else ""),
        f"<i>Данные на конец дня {pf.as_of:%d.%m.%Y}</i>" if pf.as_of else "",
        "",
        f"💰 Всего: <b>{num(total)} {cur}</b>",
        f"📈 В бумагах: {num(pf.positions_value)} {cur}",
    ]
    if pf.cost:
        lines.append(f"Нереализованный результат: {change(pf.pnl / pf.cost * 100)}  ({money(pf.pnl, cur)})")
    if pf.cash is not None:
        lines.append(f"💵 Наличные: {num(pf.cash)} {cur}")
    if pf.positions:
        lines += ["", "<b>Позиции</b>"]
        for p in pf.positions[:MAX_POSITIONS]:
            share = f" · {num(p.value_base / total * 100, 1)}%" if total else ""
            lines.append(
                f"<code>{escape(p.symbol)}</code> {qty(p.quantity)} шт · {num(p.value_base)} {cur}{share}  "
                f"{change(p.pnl_pct)}"
            )
        if len(pf.positions) > MAX_POSITIONS:
            lines.append(f"…и ещё {len(pf.positions) - MAX_POSITIONS}")
    else:
        lines += ["", "Открытых позиций нет."]
    lines += ["", "<i>Результат — к цене покупки. Отчёт IBKR обновляется раз в день, после закрытия торгов.</i>"]
    return "\n".join(line for line in lines if line is not None)


def kb(*rows: list[Btn]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


async def safe_edit(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


def setup(ctx: AppContext) -> Module:
    router = Router(name="portfolio")
    market = MarketClient()
    ibkr = IbkrClient()
    ctx.shutdown += [market.close, ibkr.close]

    markets_kb = kb(
        [Btn(text="🔄 Обновить", callback_data="pf:refresh")],
        [Btn(text="🏦 Мой портфель IBKR", callback_data="pf:ibkr")],
        [Btn(text="🏠 Главное меню", callback_data="core:main")],
    )

    async def markets_view() -> str:
        instruments = [inst for _, _, items in GROUPS for inst in items]
        quotes = dict(zip((i.symbol for i in instruments), await market.quotes([i.symbol for i in instruments])))
        for symbol, q in quotes.items():
            if isinstance(q, Exception):
                log.warning("quote %s failed: %r", symbol, q)
        return render_markets([
            (title, us, [(inst, quotes[inst.symbol]) for inst in items]) for title, us, items in GROUPS
        ])

    async def creds(user_id: int):
        return await ctx.db.fetchone("SELECT token, query_id FROM ibkr_accounts WHERE user_id = ?", (user_id,))

    async def ibkr_view(user_id: int, fresh: bool = False) -> tuple[str, InlineKeyboardMarkup]:
        row = await creds(user_id)
        if not row:
            return IBKR_HELP, kb(
                [Btn(text="🔗 Подключить", callback_data="pf:ibkr:setup")],
                [Btn(text="⬅️ Рынки", callback_data="m:portfolio")],
            )
        markup = kb(
            [Btn(text="🔄 Обновить", callback_data="pf:ibkr:refresh"),
             Btn(text="⚙️ Отключить", callback_data="pf:ibkr:off")],
            [Btn(text="⬅️ Рынки", callback_data="m:portfolio")],
        )
        try:
            pf = await ibkr.portfolio(row["token"], row["query_id"], fresh=fresh)
        except IbkrError as exc:
            return f"⚠️ {escape(str(exc))}", markup
        except Exception:  # noqa: BLE001
            log.exception("ibkr fetch failed")
            return "⚠️ Не удалось получить отчёт IBKR. Попробуйте позже.", markup
        return render_ibkr(pf), markup

    # ---------- рынки ----------
    @router.callback_query(F.data.in_({"m:portfolio", "pf:refresh"}))
    async def show(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await safe_edit(call, await markets_view(), markets_kb)
        await call.answer("Обновлено" if call.data == "pf:refresh" else None)

    @router.message(Command("market"))
    async def market_cmd(message: Message) -> None:
        await message.answer(await markets_view(), reply_markup=markets_kb)

    # ---------- IBKR ----------
    @router.callback_query(F.data.in_({"pf:ibkr", "pf:ibkr:refresh"}))
    async def ibkr_show(call: CallbackQuery) -> None:
        await call.answer("Загружаю отчёт IBKR…")
        await safe_edit(call, *await ibkr_view(call.from_user.id, fresh=call.data.endswith("refresh")))

    @router.message(Command("ibkr"))
    async def ibkr_cmd(message: Message) -> None:
        text, markup = await ibkr_view(message.from_user.id)
        await message.answer(text, reply_markup=markup)

    @router.callback_query(F.data == "pf:ibkr:setup")
    async def setup_start(call: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(IbkrSetup.token)
        await safe_edit(call, "🔑 Отправьте <b>токен</b> Flex Web Service (длинный набор цифр).\n/cancel — отмена",
                        kb([Btn(text="⬅️ Назад", callback_data="pf:ibkr")]))
        await call.answer()

    @router.message(IbkrSetup.token, F.text, ~F.text.startswith("/"))
    async def setup_token(message: Message, state: FSMContext) -> None:
        token = message.text.strip()
        try:
            await message.delete()  # не держим токен в истории чата
        except TelegramBadRequest:
            pass
        if not token.isalnum() or len(token) < 10:
            await message.answer("Не похоже на токен IBKR. Скопируйте его ещё раз целиком.")
            return
        await state.update_data(token=token)
        await state.set_state(IbkrSetup.query)
        await message.answer("✅ Токен получен (сообщение с ним удалено).\nТеперь отправьте <b>Query ID</b> — номер отчёта.")

    @router.message(IbkrSetup.query, F.text, ~F.text.startswith("/"))
    async def setup_query(message: Message, state: FSMContext) -> None:
        query_id = message.text.strip()
        if not query_id.isdigit():
            await message.answer("Query ID — это число, например <code>123456</code>.")
            return
        token = (await state.get_data())["token"]
        await state.clear()
        note = await message.answer("⏳ Проверяю: запрашиваю отчёт у IBKR, это до минуты…")
        try:
            pf = await ibkr.portfolio(token, query_id, fresh=True)
        except IbkrError as exc:
            await note.edit_text(f"⚠️ {escape(str(exc))}\n\nПроверьте токен и Query ID и подключите заново.",
                                 reply_markup=kb([Btn(text="🔗 Подключить заново", callback_data="pf:ibkr:setup")]))
            return
        except Exception:  # noqa: BLE001
            log.exception("ibkr setup fetch failed")
            await note.edit_text("⚠️ IBKR не ответил. Попробуйте позже.",
                                 reply_markup=kb([Btn(text="🔗 Подключить заново", callback_data="pf:ibkr:setup")]))
            return
        await ctx.db.execute(
            "INSERT INTO ibkr_accounts(user_id, token, query_id, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET token = excluded.token, query_id = excluded.query_id, "
            "updated_at = excluded.updated_at",
            (message.from_user.id, token, query_id, utcnow()),
        )
        _, markup = await ibkr_view(message.from_user.id)
        await note.edit_text("✅ <b>IBKR подключён</b>\n\n" + render_ibkr(pf), reply_markup=markup)

    @router.callback_query(F.data == "pf:ibkr:off")
    async def off_ask(call: CallbackQuery) -> None:
        await safe_edit(call, "Отключить IBKR? Бот удалит сохранённый токен.",
                        kb([Btn(text="⚙️ Да, отключить", callback_data="pf:ibkr:offok"),
                            Btn(text="Отмена", callback_data="pf:ibkr")]))
        await call.answer()

    @router.callback_query(F.data == "pf:ibkr:offok")
    async def off(call: CallbackQuery) -> None:
        row = await creds(call.from_user.id)
        if row:
            ibkr.forget(row["token"], row["query_id"])
        await ctx.db.execute("DELETE FROM ibkr_accounts WHERE user_id = ?", (call.from_user.id,))
        await safe_edit(call, *await ibkr_view(call.from_user.id))
        await call.answer("IBKR отключён. Токен можно отозвать в кабинете IBKR.")

    return Module(key="portfolio", title="💼 Портфель", router=router)
