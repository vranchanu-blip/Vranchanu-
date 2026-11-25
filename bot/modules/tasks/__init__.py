"""Модуль «Дела»: напоминания обычными словами и списки дел с галочками."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from bot.core.registry import AppContext, Module
from bot.modules.tasks.parse import WEEKDAY_SHORT, Parsed, describe_repeat, next_due, parse
from bot.modules.tasks.store import MAX_REMINDERS, TaskStore

log = logging.getLogger(__name__)

EXAMPLES = (
    "<i>Например:</i>\n"
    "• <code>завтра в 9 позвонить в банк</code>\n"
    "• <code>через 30 минут выключить духовку</code>\n"
    "• <code>в пятницу вечером купить подарок</code>\n"
    "• <code>25.12 в 10 поздравить маму</code>\n"
    "• <code>каждый понедельник в 8 вынести мусор</code>\n"
    "• <code>каждый месяц 1-го оплатить квартиру</code>"
)


class Remind(StatesGroup):
    text = State()
    when = State()


class TodoAdd(StatesGroup):
    items = State()
    new_list = State()


def kb(*rows: list[Btn]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Bucharest")


def when_text(due: datetime, now: datetime) -> str:
    days = (due.date() - now.date()).days
    day = "сегодня" if days == 0 else "завтра" if days == 1 else f"{WEEKDAY_SHORT[due.weekday()]} {due:%d.%m}"
    if due.year != now.year:
        day += f".{due:%Y}"
    return f"{day} в {due:%H:%M}"


async def safe_edit(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


def setup(ctx: AppContext) -> Module:
    router = Router(name="tasks")
    store = TaskStore(ctx.db)

    async def local_now(user_id: int) -> tuple[datetime, str]:
        tz = await store.user_tz(user_id)
        return datetime.now(zone(tz)), tz

    # ---------- экраны ----------
    async def home_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        now, _ = await local_now(user_id)
        rems = await store.reminders(user_id)
        lists = await store.lists(user_id)
        lines = ["📝 <b>Дела</b>", "", "⏰ <b>Ближайшие напоминания</b>"]
        if rems:
            for r in rems[:3]:
                due = datetime.fromisoformat(r["due_at"]).astimezone(zone(r["tz"]))
                rep = " 🔁" if r["repeat"] else ""
                lines.append(f"• {when_text(due, now)}{rep} — {escape(r['text'])}")
            if len(rems) > 3:
                lines.append(f"<i>…и ещё {len(rems) - 3}</i>")
        else:
            lines.append("<i>нет</i>")
        lines += ["", "<i>Совет: можно просто написать боту «напомни завтра в 9 …»</i>"]
        rows = [
            [Btn(text="➕ Напоминание", callback_data="td:rem:new"),
             Btn(text=f"⏰ Все ({len(rems)})", callback_data="td:rem:list")],
            *[[Btn(text=f"{l['title']} · {l['open']}", callback_data=f"td:l:{l['id']}")] for l in lists],
            [Btn(text="➕ Новый список", callback_data="td:l:new")],
            [Btn(text="🏠 Главное меню", callback_data="core:main")],
        ]
        return "\n".join(lines), kb(*rows)

    async def reminders_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        now, _ = await local_now(user_id)
        rems = await store.reminders(user_id)
        if not rems:
            return "⏰ <b>Напоминания</b>\n\nНапоминаний нет.", kb(
                [Btn(text="➕ Напоминание", callback_data="td:rem:new")],
                [Btn(text="⬅️ Назад", callback_data="m:tasks")],
            )
        lines = ["⏰ <b>Напоминания</b>", ""]
        buttons = []
        for i, r in enumerate(rems, 1):
            due = datetime.fromisoformat(r["due_at"]).astimezone(zone(r["tz"]))
            rep = f"  🔁 <i>{describe_repeat(r['repeat'])}</i>" if r["repeat"] else ""
            lines.append(f"<b>{i}.</b> {when_text(due, now)} — {escape(r['text'])}{rep}")
            buttons.append(Btn(text=f"🗑 {i}", callback_data=f"td:rem:del:{r['id']}"))
        lines += ["", "<i>Нажмите 🗑 с номером, чтобы удалить.</i>"]
        rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
        return "\n".join(lines), kb(*rows, [Btn(text="➕ Напоминание", callback_data="td:rem:new")],
                                    [Btn(text="⬅️ Назад", callback_data="m:tasks")])

    async def list_view(user_id: int, list_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
        lst = await store.get_list(user_id, list_id)
        if not lst:
            return None
        items = await store.items(list_id)
        done = sum(1 for i in items if i["done"])
        text = f"<b>{escape(lst['title'])}</b>\n\n"
        text += (f"Сделано {done} из {len(items)}. Нажмите на пункт, чтобы отметить."
                 if items else "Список пуст. Нажмите «➕ Добавить».")
        rows = [[Btn(text=("✅ " if i["done"] else "⬜ ") + i["text"][:60], callback_data=f"td:i:{i['id']}")]
                for i in items]
        controls = [Btn(text="➕ Добавить", callback_data=f"td:add:{list_id}")]
        if done:
            controls.append(Btn(text="🧹 Убрать сделанное", callback_data=f"td:clr:{list_id}"))
        return text, kb(*rows, controls,
                        [Btn(text="🗑 Удалить список", callback_data=f"td:ldel:{list_id}"),
                         Btn(text="⬅️ Назад", callback_data="m:tasks")])

    # ---------- создание напоминания ----------
    async def save(target: Message, user_id: int, parsed: Parsed, tz: str) -> None:
        now = datetime.now(zone(tz))
        if parsed.due is None:
            return
        if parsed.due <= now:
            await target.answer("⚠️ Это время уже прошло. Напишите другое, например <code>завтра в 9</code>.")
            return
        rid = await store.add_reminder(user_id, parsed.body, parsed.due, tz, parsed.repeat)
        if rid is None:
            await target.answer(f"⚠️ Можно держать не больше {MAX_REMINDERS} напоминаний. Удалите лишние.")
            return
        rep = f"\n🔁 {describe_repeat(parsed.repeat)}" if parsed.repeat else ""
        await target.answer(
            f"✅ Напомню <b>{when_text(parsed.due, now)}</b>:\n{escape(parsed.body)}{rep}",
            reply_markup=kb([Btn(text="↩️ Отменить", callback_data=f"td:rem:undo:{rid}"),
                             Btn(text="📝 Дела", callback_data="m:tasks")]),
        )

    def quick_when_kb() -> InlineKeyboardMarkup:
        return kb(
            [Btn(text="Через 1 час", callback_data="td:q:1h"), Btn(text="Сегодня в 20:00", callback_data="td:q:t20")],
            [Btn(text="Завтра в 9:00", callback_data="td:q:m9"), Btn(text="Завтра в 18:00", callback_data="td:q:m18")],
            [Btn(text="❌ Отмена", callback_data="m:tasks")],
        )

    async def handle_text(message: Message, state: FSMContext, raw: str) -> None:
        now, tz = await local_now(message.from_user.id)
        parsed = parse(raw, now)
        if parsed.due is None:
            await state.set_state(Remind.when)
            await state.update_data(body=parsed.body)
            await message.answer(
                f"Когда напомнить: «{escape(parsed.body)}»?\nВыберите или напишите, например <code>в 15:30</code>.",
                reply_markup=quick_when_kb(),
            )
            return
        await state.clear()
        await save(message, message.from_user.id, parsed, tz)

    @router.callback_query(F.data == "td:rem:new")
    async def rem_new(call: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(Remind.text)
        await safe_edit(call, "⏰ <b>Что и когда напомнить?</b>\n\n" + EXAMPLES,
                        kb([Btn(text="❌ Отмена", callback_data="m:tasks")]))
        await call.answer()

    @router.message(Command("remind"))
    async def remind_cmd(message: Message, command: CommandObject, state: FSMContext) -> None:
        if command.args:
            await handle_text(message, state, command.args)
            return
        await state.set_state(Remind.text)
        await message.answer("⏰ <b>Что и когда напомнить?</b>\n\n" + EXAMPLES,
                             reply_markup=kb([Btn(text="❌ Отмена", callback_data="m:tasks")]))

    @router.message(Remind.text, F.text, ~F.text.startswith("/"))
    async def rem_text(message: Message, state: FSMContext) -> None:
        await handle_text(message, state, message.text)

    @router.message(StateFilter(default_state), F.text.regexp(r"(?i)^\s*напомни"))
    async def rem_free(message: Message, state: FSMContext) -> None:
        await handle_text(message, state, message.text)

    @router.message(Remind.when, F.text, ~F.text.startswith("/"))
    async def rem_when(message: Message, state: FSMContext) -> None:
        body = (await state.get_data()).get("body", "Напоминание")
        now, tz = await local_now(message.from_user.id)
        p = parse(message.text, now)
        if p.due is None:
            await message.answer("Не понял время. Пример: <code>завтра в 15:30</code>", reply_markup=quick_when_kb())
            return
        await state.clear()
        await save(message, message.from_user.id, Parsed(body, p.due, p.repeat), tz)

    @router.callback_query(Remind.when, F.data.startswith("td:q:"))
    async def rem_quick(call: CallbackQuery, state: FSMContext) -> None:
        body = (await state.get_data()).get("body", "Напоминание")
        now, tz = await local_now(call.from_user.id)
        key = call.data.rsplit(":", 1)[1]
        base = now.replace(second=0, microsecond=0)
        due = {
            "1h": base + timedelta(hours=1),
            "t20": base.replace(hour=20, minute=0),
            "m9": (base + timedelta(days=1)).replace(hour=9, minute=0),
            "m18": (base + timedelta(days=1)).replace(hour=18, minute=0),
        }[key]
        await state.clear()
        await call.answer()
        await save(call.message, call.from_user.id, Parsed(body, due), tz)

    @router.callback_query(F.data.startswith("td:rem:undo:"))
    async def rem_undo(call: CallbackQuery) -> None:
        await store.cancel_reminder(call.from_user.id, int(call.data.rsplit(":", 1)[1]))
        await safe_edit(call, "↩️ Напоминание отменено.", kb([Btn(text="📝 Дела", callback_data="m:tasks")]))
        await call.answer()

    @router.callback_query(F.data == "td:rem:list")
    async def rem_list(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await safe_edit(call, *await reminders_view(call.from_user.id))
        await call.answer()

    @router.callback_query(F.data.startswith("td:rem:del:"))
    async def rem_del(call: CallbackQuery) -> None:
        await store.cancel_reminder(call.from_user.id, int(call.data.rsplit(":", 1)[1]))
        await safe_edit(call, *await reminders_view(call.from_user.id))
        await call.answer("Удалено")

    # ---------- срабатывание: кнопки под напоминанием ----------
    @router.callback_query(F.data.startswith("td:done:"))
    async def rem_done(call: CallbackQuery) -> None:
        text = call.message.html_text if call.message else ""
        try:
            await call.message.edit_text(f"✅ <s>{text.split(chr(10), 1)[-1]}</s>")
        except TelegramBadRequest:
            pass
        await call.answer("Готово")

    @router.callback_query(F.data.startswith("td:snz:"))
    async def rem_snooze(call: CallbackQuery) -> None:
        _, _, rid, what = call.data.split(":")
        r = await store.reminder(call.from_user.id, int(rid))
        if not r:
            await call.answer("Напоминание не найдено", show_alert=True)
            return
        now = datetime.now(zone(r["tz"])).replace(second=0, microsecond=0)
        due = {
            "10m": now + timedelta(minutes=10),
            "1h": now + timedelta(hours=1),
            "tm": (now + timedelta(days=1)).replace(hour=9, minute=0),
        }[what]
        await store.add_reminder(call.from_user.id, r["text"], due, r["tz"], None)
        try:
            await call.message.edit_text(f"⏰ Отложено: напомню {when_text(due, now)}\n{escape(r['text'])}")
        except TelegramBadRequest:
            pass
        await call.answer("Отложено")

    # ---------- списки ----------
    @router.callback_query(F.data.in_({"m:tasks"}))
    async def home(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await safe_edit(call, *await home_view(call.from_user.id))
        await call.answer()

    @router.message(Command("todo"))
    async def todo_cmd(message: Message, state: FSMContext) -> None:
        await state.clear()
        text, markup = await home_view(message.from_user.id)
        await message.answer(text, reply_markup=markup)

    async def show_list(target: CallbackQuery | Message, user_id: int, list_id: int) -> None:
        view = await list_view(user_id, list_id)
        if view is None:
            if isinstance(target, CallbackQuery):
                await target.answer("Список не найден", show_alert=True)
            return
        if isinstance(target, CallbackQuery):
            await safe_edit(target, *view)
            await target.answer()
        else:
            await target.answer(view[0], reply_markup=view[1])

    @router.callback_query(F.data.regexp(r"^td:l:\d+$"))
    async def open_list(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await show_list(call, call.from_user.id, int(call.data.rsplit(":", 1)[1]))

    @router.callback_query(F.data.startswith("td:i:"))
    async def toggle(call: CallbackQuery) -> None:
        list_id = await store.toggle_item(call.from_user.id, int(call.data.rsplit(":", 1)[1]))
        if list_id is None:
            await call.answer("Пункт не найден", show_alert=True)
            return
        await show_list(call, call.from_user.id, list_id)

    @router.callback_query(F.data.startswith("td:add:"))
    async def add_start(call: CallbackQuery, state: FSMContext) -> None:
        list_id = int(call.data.rsplit(":", 1)[1])
        await state.set_state(TodoAdd.items)
        await state.update_data(list_id=list_id)
        await safe_edit(call, "✍️ Напишите пункт. Можно сразу несколько — каждый с новой строки.",
                        kb([Btn(text="✅ Готово", callback_data=f"td:l:{list_id}")]))
        await call.answer()

    @router.message(TodoAdd.items, F.text, ~F.text.startswith("/"))
    async def add_items(message: Message, state: FSMContext) -> None:
        list_id = (await state.get_data())["list_id"]
        if not await store.get_list(message.from_user.id, list_id):
            await state.clear()
            return
        added = await store.add_items(list_id, message.text.splitlines())
        await state.clear()
        if not added:
            await message.answer("⚠️ Список заполнен (максимум 50 пунктов).")
        await show_list(message, message.from_user.id, list_id)

    @router.callback_query(F.data.startswith("td:clr:"))
    async def clear_done(call: CallbackQuery) -> None:
        list_id = int(call.data.rsplit(":", 1)[1])
        if await store.get_list(call.from_user.id, list_id):
            await store.clear_done(list_id)
        await show_list(call, call.from_user.id, list_id)

    @router.callback_query(F.data.startswith("td:ldel:"))
    async def del_list_ask(call: CallbackQuery) -> None:
        list_id = int(call.data.rsplit(":", 1)[1])
        await safe_edit(call, "Удалить список вместе со всеми пунктами?",
                        kb([Btn(text="🗑 Да, удалить", callback_data=f"td:ldelok:{list_id}"),
                            Btn(text="Отмена", callback_data=f"td:l:{list_id}")]))
        await call.answer()

    @router.callback_query(F.data.startswith("td:ldelok:"))
    async def del_list(call: CallbackQuery) -> None:
        await store.delete_list(call.from_user.id, int(call.data.rsplit(":", 1)[1]))
        await safe_edit(call, *await home_view(call.from_user.id))
        await call.answer("Список удалён")

    @router.callback_query(F.data == "td:l:new")
    async def new_list(call: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(TodoAdd.new_list)
        await safe_edit(call, "Как назвать список? Например: <code>🏠 Дом</code>, <code>✈️ В отпуск</code>.",
                        kb([Btn(text="❌ Отмена", callback_data="m:tasks")]))
        await call.answer()

    @router.message(TodoAdd.new_list, F.text, ~F.text.startswith("/"))
    async def new_list_name(message: Message, state: FSMContext) -> None:
        await state.clear()
        list_id = await store.add_list(message.from_user.id, message.text.strip())
        if list_id is None:
            await message.answer("⚠️ Можно не больше 10 списков.")
            return
        await show_list(message, message.from_user.id, list_id)

    # ---------- фоновая рассылка ----------
    async def fire(now_utc: datetime) -> None:
        for r in await store.due(now_utc):
            local_due = datetime.fromisoformat(r["due_at"]).astimezone(zone(r["tz"]))
            rep = f"\n🔁 <i>{describe_repeat(r['repeat'])}</i>" if r["repeat"] else ""
            try:
                await ctx.bot.send_message(
                    r["user_id"],
                    f"⏰ <b>Напоминание</b>\n{escape(r['text'])}{rep}",
                    reply_markup=kb(
                        [Btn(text="✅ Готово", callback_data=f"td:done:{r['id']}")],
                        [Btn(text="+10 мин", callback_data=f"td:snz:{r['id']}:10m"),
                         Btn(text="+1 час", callback_data=f"td:snz:{r['id']}:1h"),
                         Btn(text="Завтра 9:00", callback_data=f"td:snz:{r['id']}:tm")],
                    ),
                )
            except TelegramForbiddenError:
                await store.reschedule(r["id"], None)  # пользователь заблокировал бота
                continue
            except Exception:  # noqa: BLE001
                log.exception("reminder %s send failed", r["id"])
                continue
            nxt = next_due(local_due, r["repeat"], now_utc.astimezone(zone(r["tz"]))) if r["repeat"] else None
            await store.reschedule(r["id"], nxt)

    async def run_forever() -> None:
        while True:
            try:
                await fire(datetime.now(timezone.utc))
            except Exception:  # noqa: BLE001
                log.exception("reminders loop failed")
            await asyncio.sleep(20)

    ctx.background.append(run_forever)
    ctx.state["tasks.fire"] = fire  # для тестов
    return Module(key="tasks", title="📝 Дела", router=router)
