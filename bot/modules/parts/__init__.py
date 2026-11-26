"""Модуль «Запчасти»: ежедневная подборка новых объявлений Harley-Davidson с eBay Европы."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from bot.core.registry import AppContext, Module
from bot.db import utcnow
from bot.modules.parts.ebay import COUNTRY_RU, DEFAULT_QUERIES, EbayClient, EbayError, Item, flag

log = logging.getLogger(__name__)

TIME_RE = re.compile(r"^\s*([01]?\d|2[0-3])[:.\s]([0-5]\d)\s*$")
PRICE_RE = re.compile(r"^\s*(\d+)\s*[-–—…]+\s*(\d+)\s*$")
MAX_AGE = timedelta(days=14)     # старше двух недель — уже не «новое»
WINDOW = timedelta(hours=2)      # если бот был выключен в 19:09 — догоняем 2 часа

EBAY_HELP = (
    "🔑 <b>Подключение eBay</b>\n\n"
    "1. developer.ebay.com → <b>Register</b> (можно войти обычным аккаунтом eBay), дождитесь одобрения.\n"
    "2. <b>Application Keys</b> → блок <b>Production</b> → <b>Create a keyset</b>.\n"
    "3. Отправьте боту <b>App ID (Client ID)</b>, затем <b>Cert ID (Client Secret)</b>.\n\n"
    "<i>Бот сразу удалит сообщения с ключами и сохранит их только на сервере. "
    "Ключи дают только поиск объявлений — покупать от вашего имени бот не может.</i>"
)


class PartsSetup(StatesGroup):
    client_id = State()
    secret = State()
    time = State()
    price = State()
    queries = State()


def kb(*rows: list[Btn]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Bucharest")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Bucharest")


def money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def caption(it: Item) -> str:
    country = COUNTRY_RU.get(it.country or "", it.country or "")
    price = f"{it.price:,.2f}".replace(",", " ").replace(".", ",")
    approx = f" (≈{money(it.price_eur)} €)" if it.currency != "EUR" else ""
    lines = [
        f"🔧 <b>{escape(it.title[:200])}</b>",
        "",
        f"💶 <b>{price} {escape(it.currency)}</b>{approx}",
        f"📦 {it.condition} · {flag(it.country)} {escape(country)}",
    ]
    if it.shipping:
        lines.append(f"🚚 доставка: {escape(it.shipping)}")
    return "\n".join(lines)


async def safe_edit(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


def setup(ctx: AppContext) -> Module:
    router = Router(name="parts")
    clients: dict[tuple[str, str], EbayClient] = {}

    def client_for(row) -> EbayClient:
        key = (row["client_id"], row["client_secret"])
        if key not in clients:
            clients[key] = EbayClient(*key)
        return clients[key]

    async def close_all() -> None:
        for c in clients.values():
            await c.close()

    ctx.shutdown.append(close_all)

    async def settings(user_id: int):
        row = await ctx.db.fetchone("SELECT * FROM parts_settings WHERE user_id = ?", (user_id,))
        if row is None:
            await ctx.db.execute("INSERT INTO parts_settings(user_id, queries) VALUES(?, ?)",
                                 (user_id, "\n".join(DEFAULT_QUERIES)))
            row = await ctx.db.fetchone("SELECT * FROM parts_settings WHERE user_id = ?", (user_id,))
        return row

    async def update(user_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await ctx.db.execute(f"UPDATE parts_settings SET {cols} WHERE user_id = ?", (*fields.values(), user_id))

    async def user_tz(user_id: int) -> str | None:
        row = await ctx.db.fetchone(
            "SELECT p.tz FROM users u JOIN places p ON p.id = u.active_place_id WHERE u.id = ?", (user_id,)
        )
        return row["tz"] if row else None

    # ---------- экран ----------
    async def view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        s = await settings(user_id)
        connected = bool(s["client_id"])
        queries = [q for q in (s["queries"] or "").splitlines() if q.strip()]
        lines = [
            "🔧 <b>Запчасти Harley-Davidson</b> · eBay Европа",
            "",
            "✅ eBay подключён" if connected else "⚠️ eBay ещё не подключён — нажмите «🔑 Ключи eBay»",
            (f"⏰ Подборка: каждый день в <b>{s['send_time']}</b>, {s['per_day']} шт."
             if s["active"] else "⏸ Подборка на паузе"),
            f"💶 Цена: от {money(s['min_price'])} до {money(s['max_price'])} €",
            "🌍 Вся Европа + Великобритания · новые и б/у",
            "",
            "🔎 <b>Ищу по запросам</b>",
            *[f"• {escape(q)}" for q in queries],
        ]
        markup = kb(
            [Btn(text="🔍 Показать сейчас", callback_data="pt:now")] if connected else [],
            [Btn(text="⏰ Время", callback_data="pt:time"), Btn(text="💶 Цена", callback_data="pt:price")],
            [Btn(text="✏️ Запросы", callback_data="pt:queries"),
             Btn(text="⏸ Пауза" if s["active"] else "▶️ Включить", callback_data="pt:toggle")],
            [Btn(text="🔑 Ключи eBay", callback_data="pt:keys")],
            [Btn(text="🏠 Главное меню", callback_data="core:main")],
        )
        return "\n".join(lines), markup

    async def show(target: Message | CallbackQuery, user_id: int) -> None:
        text, markup = await view(user_id)
        if isinstance(target, CallbackQuery):
            await safe_edit(target, text, markup)
            await target.answer()
        else:
            await target.answer(text, reply_markup=markup, disable_web_page_preview=True)

    # ---------- подборка ----------
    async def pick(user_id: int, s) -> list[Item]:
        queries = [q.strip() for q in (s["queries"] or "").splitlines() if q.strip()] or DEFAULT_QUERIES
        items = await client_for(s).search_europe(queries, s["min_price"], s["max_price"])
        seen = {r["item_id"] for r in await ctx.db.fetchall(
            "SELECT item_id FROM parts_seen WHERE user_id = ?", (user_id,))}
        cutoff = (datetime.now(timezone.utc) - MAX_AGE).isoformat()
        chosen, titles = [], set()
        for it in items:
            norm = re.sub(r"\W+", " ", it.title.lower()).strip()
            if it.item_id in seen or norm in titles or (it.created and it.created < cutoff):
                continue
            titles.add(norm)
            chosen.append(it)
            if len(chosen) >= s["per_day"]:
                break
        for it in chosen:
            await ctx.db.execute(
                "INSERT OR IGNORE INTO parts_seen(user_id, item_id, seen_at) VALUES(?, ?, ?)",
                (user_id, it.item_id, utcnow()),
            )
        return chosen

    async def send_digest(user_id: int, header: str) -> None:
        s = await settings(user_id)
        try:
            items = await pick(user_id, s)
        except EbayError as exc:
            await ctx.bot.send_message(user_id, f"⚠️ Запчасти: {escape(str(exc))}")
            return
        if not items:
            await ctx.bot.send_message(user_id, f"{header}\n\nНовых объявлений сегодня нет.")
            return
        await ctx.bot.send_message(user_id, f"{header} · {len(items)} шт.")
        for it in items:
            markup = kb([Btn(text="🔗 Открыть на eBay", url=it.url)]) if it.url else None
            sent = False
            if it.image:
                try:
                    await ctx.bot.send_photo(user_id, it.image, caption=caption(it), reply_markup=markup)
                    sent = True
                except TelegramBadRequest:
                    pass  # Telegram не смог загрузить картинку — пришлём текстом
            if not sent:
                await ctx.bot.send_message(user_id, caption(it), reply_markup=markup, disable_web_page_preview=True)
            await asyncio.sleep(0.3)

    @router.callback_query(F.data.in_({"m:parts", "pt:home"}))
    async def open_parts(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await show(call, call.from_user.id)

    @router.message(Command("parts"))
    async def parts_cmd(message: Message, state: FSMContext) -> None:
        await state.clear()
        await show(message, message.from_user.id)

    @router.callback_query(F.data == "pt:now")
    async def now(call: CallbackQuery) -> None:
        await call.answer("Ищу на eBay, это до минуты…")
        await send_digest(call.from_user.id, "🔧 <b>Запчасти Harley</b>")

    @router.callback_query(F.data == "pt:toggle")
    async def toggle(call: CallbackQuery) -> None:
        s = await settings(call.from_user.id)
        await update(call.from_user.id, active=0 if s["active"] else 1)
        await show(call, call.from_user.id)

    # ---------- настройки ----------
    back = kb([Btn(text="⬅️ Назад", callback_data="pt:home")])

    @router.callback_query(F.data == "pt:time")
    async def ask_time(call: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(PartsSetup.time)
        await safe_edit(call, "⏰ Во сколько присылать подборку? Например <code>19:09</code>.", back)
        await call.answer()

    @router.message(PartsSetup.time, F.text, ~F.text.startswith("/"))
    async def set_time(message: Message, state: FSMContext) -> None:
        m = TIME_RE.match(message.text)
        if not m:
            await message.answer("Не понял время. Пример: <code>19:09</code>")
            return
        await state.clear()
        await update(message.from_user.id, send_time=f"{int(m.group(1)):02d}:{m.group(2)}", last_sent=None)
        await show(message, message.from_user.id)

    @router.callback_query(F.data == "pt:price")
    async def ask_price(call: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(PartsSetup.price)
        await safe_edit(call, "💶 Диапазон цены в евро, например <code>20-5000</code>.", back)
        await call.answer()

    @router.message(PartsSetup.price, F.text, ~F.text.startswith("/"))
    async def set_price(message: Message, state: FSMContext) -> None:
        m = PRICE_RE.match(message.text)
        if not m or int(m.group(1)) >= int(m.group(2)):
            await message.answer("Не понял. Пример: <code>20-5000</code>")
            return
        await state.clear()
        await update(message.from_user.id, min_price=int(m.group(1)), max_price=int(m.group(2)))
        await show(message, message.from_user.id)

    @router.callback_query(F.data == "pt:queries")
    async def ask_queries(call: CallbackQuery, state: FSMContext) -> None:
        s = await settings(call.from_user.id)
        await state.set_state(PartsSetup.queries)
        await safe_edit(
            call,
            "✏️ Пришлите поисковые запросы — каждый с новой строки (до 10). Сейчас:\n\n"
            f"<code>{escape(s['queries'] or '')}</code>",
            back,
        )
        await call.answer()

    @router.message(PartsSetup.queries, F.text, ~F.text.startswith("/"))
    async def set_queries(message: Message, state: FSMContext) -> None:
        queries = [q.strip()[:80] for q in message.text.splitlines() if q.strip()][:10]
        if not queries:
            await message.answer("Нужен хотя бы один запрос.")
            return
        await state.clear()
        await update(message.from_user.id, queries="\n".join(queries))
        await show(message, message.from_user.id)

    @router.callback_query(F.data == "pt:keys")
    async def ask_keys(call: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(PartsSetup.client_id)
        await safe_edit(call, EBAY_HELP + "\n\nОтправьте <b>App ID (Client ID)</b>:", back)
        await call.answer()

    @router.message(PartsSetup.client_id, F.text, ~F.text.startswith("/"))
    async def got_client_id(message: Message, state: FSMContext) -> None:
        value = message.text.strip()
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        if len(value) < 10 or " " in value:
            await message.answer("Не похоже на App ID. Скопируйте его целиком.")
            return
        await state.update_data(client_id=value)
        await state.set_state(PartsSetup.secret)
        await message.answer("✅ App ID получен (сообщение удалено). Теперь <b>Cert ID (Client Secret)</b>:")

    @router.message(PartsSetup.secret, F.text, ~F.text.startswith("/"))
    async def got_secret(message: Message, state: FSMContext) -> None:
        secret = message.text.strip()
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        client_id = (await state.get_data())["client_id"]
        await state.clear()
        note = await message.answer("⏳ Проверяю ключи на eBay…")
        test = EbayClient(client_id, secret)
        try:
            await test._get_token()
        except EbayError as exc:
            await note.edit_text(f"⚠️ {escape(str(exc))}",
                                 reply_markup=kb([Btn(text="🔑 Ввести заново", callback_data="pt:keys")]))
            return
        finally:
            await test.close()
        await settings(message.from_user.id)
        await update(message.from_user.id, client_id=client_id, client_secret=secret)
        text, markup = await view(message.from_user.id)
        await note.edit_text("✅ <b>eBay подключён</b>\n\n" + text, reply_markup=markup,
                             disable_web_page_preview=True)

    # ---------- расписание ----------
    async def tick(now_utc: datetime) -> None:
        rows = await ctx.db.fetchall(
            "SELECT user_id, send_time, last_sent FROM parts_settings WHERE active = 1 AND client_id IS NOT NULL"
        )
        for r in rows:
            local = now_utc.astimezone(zone(await user_tz(r["user_id"])))
            today = local.date().isoformat()
            if r["last_sent"] == today:
                continue
            hh, mm = map(int, r["send_time"].split(":"))
            target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if not (target <= local < target + WINDOW):
                continue
            await update(r["user_id"], last_sent=today)
            try:
                await send_digest(r["user_id"], "🔧 <b>Запчасти Harley на сегодня</b>")
            except TelegramForbiddenError:
                await update(r["user_id"], active=0)
            except Exception:  # noqa: BLE001
                log.exception("parts digest for %s failed", r["user_id"])
        await ctx.db.execute(
            "DELETE FROM parts_seen WHERE seen_at < ?",
            ((now_utc - timedelta(days=180)).replace(microsecond=0).isoformat(),),
        )

    async def run_forever() -> None:
        while True:
            try:
                await tick(datetime.now(timezone.utc))
            except Exception:  # noqa: BLE001
                log.exception("parts loop failed")
            await asyncio.sleep(60)

    ctx.background.append(run_forever)
    ctx.state["parts.tick"] = tick
    return Module(key="parts", title="🔧 Запчасти", router=router)
