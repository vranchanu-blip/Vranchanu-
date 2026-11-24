"""Модуль «Досар»: отслеживание досаров на гражданство Румынии по ст. 11."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from bot.core.menu import send_many
from bot.core.registry import AppContext, Module
from bot.modules.dosar import texts
from bot.modules.dosar.anc import AncClient, normalize_number
from bot.modules.dosar.monitor import AncMonitor, CycleResult
from bot.modules.dosar.service import MAX_DOSARS_PER_USER, DosarService

log = logging.getLogger(__name__)

REFRESH_COOLDOWN = timedelta(minutes=5)


class AddDosar(StatesGroup):
    number = State()
    label = State()


class EditDosar(StatesGroup):
    label = State()
    submitted = State()


def kb(*rows: list[Btn]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def back(cb: str = "dz:menu", text: str = "⬅️ Назад") -> list[Btn]:
    return [Btn(text=text, callback_data=cb)]


def parse_day(raw: str) -> date | None:
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            day = datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
        return day if date(2000, 1, 1) <= day <= date.today() else None
    return None


def setup(ctx: AppContext) -> Module:
    router = Router(name="dosar")
    svc = DosarService(ctx.db)
    client = AncClient(proxy=ctx.settings.anc_proxy)

    async def on_result(result: CycleResult) -> None:
        await notify(ctx, svc, result)

    monitor = AncMonitor(
        ctx.db,
        client,
        pages=ctx.settings.anc_source_pages,
        pdf_filter=ctx.settings.anc_pdf_filter,
        interval_min=ctx.settings.check_interval_min,
        on_result=on_result,
    )
    ctx.background.append(monitor.run_forever)
    ctx.shutdown.append(client.close)

    # ---------- экраны ----------
    async def menu_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        dosars = await svc.list(user_id)
        last_ok = await ctx.db.get_kv("anc_last_success_at")
        notify_on = await svc.notify_orders_enabled(user_id)
        lines = ["📁 <b>Досар — гражданство Румынии, ст. 11</b>", ""]
        if dosars:
            lines.append("Ваши досары:")
            for d in dosars:
                active = await svc.mentions(d["number"], active_only=True)
                icon = "🎉" if any(r["kind"] == "ordin" for r in active) else ("🟡" if active else "⏳")
                lines.append(f"{icon} {texts.dosar_name(d)}")
        else:
            lines.append("Досаров пока нет. Нажмите «➕ Добавить досар».")
        lines += ["", f"🕐 Последняя успешная проверка ANC: {texts.fmt_dt(last_ok)}"]
        rows = [[Btn(text=f"📄 {d['label'] or d['number']}", callback_data=f"dz:card:{d['id']}")] for d in dosars]
        rows += [
            [Btn(text="➕ Добавить досар", callback_data="dz:add")],
            [Btn(text="🔄 Проверить сейчас", callback_data="dz:check"),
             Btn(text="📰 Приказы", callback_data="dz:orders")],
            [Btn(text=f"🔔 Новые приказы: {'вкл' if notify_on else 'выкл'}", callback_data="dz:notif")],
            back("core:main", "🏠 Главное меню"),
        ]
        return "\n".join(lines), kb(*rows)

    async def card_view(user_id: int, dosar_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
        d = await svc.get(user_id, dosar_id)
        if not d:
            return None
        all_rows = await svc.mentions(d["number"])
        active = [r for r in all_rows if r["active"]]
        progress = await svc.year_progress(d["number"])
        parts = [
            f"📄 {texts.dosar_name(d)}",
            texts.waiting_line(d["submitted_on"]),
            "",
            texts.status_summary(active),
        ]
        if active:
            parts += ["", texts.mention_block(active, limit=3)]
        parts += ["", texts.progress_block(progress)]
        markup = kb(
            [Btn(text=f"📜 История ({len(all_rows)})", callback_data=f"dz:hist:{dosar_id}")],
            [Btn(text="🏷 Название", callback_data=f"dz:label:{dosar_id}"),
             Btn(text="📅 Дата подачи", callback_data=f"dz:date:{dosar_id}")],
            [Btn(text="🗑 Удалить", callback_data=f"dz:del:{dosar_id}")],
            back(),
        )
        return "\n".join(parts), markup

    async def show(target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            await target.answer()
        else:
            await target.answer(text, reply_markup=markup, disable_web_page_preview=True)

    # ---------- меню ----------
    @router.callback_query(F.data.in_({"m:dosar", "dz:menu"}))
    async def menu_cb(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await show(call, *await menu_view(call.from_user.id))

    @router.message(Command("dosar"))
    async def menu_cmd(message: Message, state: FSMContext) -> None:
        await state.clear()
        await show(message, *await menu_view(message.from_user.id))

    # ---------- добавление ----------
    @router.callback_query(F.data == "dz:add")
    async def add_start(call: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AddDosar.number)
        await call.message.edit_text(
            "Отправьте номер досара в формате <code>12345/RD/2021</code>.\n/cancel — отмена",
            reply_markup=kb(back()),
        )
        await call.answer()

    @router.message(AddDosar.number, F.text)
    async def add_number(message: Message, state: FSMContext) -> None:
        number = normalize_number(message.text)
        if not number:
            await message.answer("Не похоже на номер досара. Пример: <code>12345/RD/2021</code>")
            return
        await state.update_data(number=number)
        await state.set_state(AddDosar.label)
        await message.answer(
            f"Номер: <code>{number}</code>\nДайте досару название (например, «Мама») или пропустите.",
            reply_markup=kb([Btn(text="Пропустить", callback_data="dz:skip")]),
        )

    async def finish_add(user_id: int, state: FSMContext, label: str | None) -> tuple[str, InlineKeyboardMarkup]:
        data = await state.get_data()
        await state.clear()
        dosar_id = await svc.add(user_id, data["number"], label)
        if dosar_id is None:
            text, markup = await menu_view(user_id)
            return (
                f"⚠️ Не удалось добавить: такой досар уже есть или достигнут лимит "
                f"({MAX_DOSARS_PER_USER}).\n\n{text}",
                markup,
            )
        view = await card_view(user_id, dosar_id)
        assert view
        text, markup = view
        return f"✅ Досар добавлен, буду следить за ним.\n\n{text}", markup

    @router.callback_query(AddDosar.label, F.data == "dz:skip")
    async def add_skip(call: CallbackQuery, state: FSMContext) -> None:
        await show(call, *await finish_add(call.from_user.id, state, None))

    @router.message(AddDosar.label, F.text)
    async def add_label(message: Message, state: FSMContext) -> None:
        await show(message, *await finish_add(message.from_user.id, state, message.text.strip()[:40]))

    # ---------- карточка ----------
    @router.callback_query(F.data.startswith("dz:card:"))
    async def card(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        view = await card_view(call.from_user.id, int(call.data.rsplit(":", 1)[1]))
        if not view:
            await call.answer("Досар не найден", show_alert=True)
            return
        await show(call, *view)

    @router.callback_query(F.data.startswith("dz:hist:"))
    async def history(call: CallbackQuery) -> None:
        dosar_id = int(call.data.rsplit(":", 1)[1])
        d = await svc.get(call.from_user.id, dosar_id)
        if not d:
            await call.answer("Досар не найден", show_alert=True)
            return
        rows = await svc.mentions(d["number"])
        body = texts.mention_block(rows, limit=10) if rows else "Номер ещё не встречался в документах ANC."
        await show(call, f"📜 История {texts.dosar_name(d)}\n\n{body}", kb(back(f"dz:card:{dosar_id}")))

    @router.callback_query(F.data.startswith("dz:del:"))
    async def delete_ask(call: CallbackQuery) -> None:
        dosar_id = int(call.data.rsplit(":", 1)[1])
        await show(
            call,
            "Удалить досар из отслеживания?",
            kb([Btn(text="🗑 Да, удалить", callback_data=f"dz:delok:{dosar_id}"),
                Btn(text="Отмена", callback_data=f"dz:card:{dosar_id}")]),
        )

    @router.callback_query(F.data.startswith("dz:delok:"))
    async def delete_ok(call: CallbackQuery) -> None:
        await svc.delete(call.from_user.id, int(call.data.rsplit(":", 1)[1]))
        await show(call, *await menu_view(call.from_user.id))

    @router.callback_query(F.data.startswith("dz:label:"))
    async def label_start(call: CallbackQuery, state: FSMContext) -> None:
        dosar_id = int(call.data.rsplit(":", 1)[1])
        await state.set_state(EditDosar.label)
        await state.update_data(dosar_id=dosar_id)
        await show(call, "Отправьте новое название (или «-», чтобы убрать).", kb(back(f"dz:card:{dosar_id}")))

    @router.message(EditDosar.label, F.text)
    async def label_set(message: Message, state: FSMContext) -> None:
        dosar_id = (await state.get_data())["dosar_id"]
        await state.clear()
        raw = message.text.strip()
        await svc.set_label(message.from_user.id, dosar_id, None if raw == "-" else raw[:40])
        view = await card_view(message.from_user.id, dosar_id)
        if view:
            await show(message, *view)

    @router.callback_query(F.data.startswith("dz:date:"))
    async def date_start(call: CallbackQuery, state: FSMContext) -> None:
        dosar_id = int(call.data.rsplit(":", 1)[1])
        await state.set_state(EditDosar.submitted)
        await state.update_data(dosar_id=dosar_id)
        await show(call, "Отправьте дату подачи в формате ДД.ММ.ГГГГ (или «-», чтобы убрать).",
                   kb(back(f"dz:card:{dosar_id}")))

    @router.message(EditDosar.submitted, F.text)
    async def date_set(message: Message, state: FSMContext) -> None:
        raw = message.text.strip()
        day = None if raw == "-" else parse_day(raw)
        if raw != "-" and day is None:
            await message.answer("Не понял дату. Пример: <code>15.03.2021</code>")
            return
        dosar_id = (await state.get_data())["dosar_id"]
        await state.clear()
        await svc.set_submitted(message.from_user.id, dosar_id, day)
        view = await card_view(message.from_user.id, dosar_id)
        if view:
            await show(message, *view)

    # ---------- проверка / приказы / уведомления ----------
    async def maybe_refresh() -> str:
        if monitor.busy:
            return "⏳ Проверка сайта ANC уже идёт — пришлю уведомление, если что-то изменится."
        last = await ctx.db.get_kv("anc_last_cycle_at")
        if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < REFRESH_COOLDOWN:
            return f"Сайт ANC проверялся недавно ({texts.fmt_dt(last)}). Показываю актуальные данные."
        monitor.request_refresh()
        return "🔄 Запустил проверку сайта ANC — пришлю уведомление, если что-то изменится."

    @router.callback_query(F.data == "dz:check")
    async def check_now(call: CallbackQuery) -> None:
        note = await maybe_refresh()
        text, markup = await menu_view(call.from_user.id)
        await show(call, f"{note}\n\n{text}", markup)

    @router.message(Command("check"))
    async def check_cmd(message: Message, command: CommandObject) -> None:
        number = normalize_number(command.args or "")
        if not number:
            await message.answer("Использование: <code>/check 12345/RD/2021</code>")
            return
        rows = await svc.mentions(number)
        active = [r for r in rows if r["active"]]
        progress = await svc.year_progress(number)
        parts = [f"🔎 <code>{number}</code>", texts.status_summary(active)]
        if rows:
            parts += ["", texts.mention_block(rows)]
        parts += ["", texts.progress_block(progress)]
        await message.answer("\n".join(parts), disable_web_page_preview=True)

    @router.callback_query(F.data == "dz:orders")
    async def orders(call: CallbackQuery) -> None:
        rows = await svc.recent_orders(10)
        stats = await svc.index_stats()
        if rows:
            body = "\n".join(
                f"• <a href=\"{escape(r['url'], quote=True)}\">{escape(r['title'])}</a>" for r in rows
            )
        else:
            body = "Приказы ещё не загружены (сайт ANC пока не отвечал боту)."
        await show(
            call,
            f"📰 <b>Последние приказы ANC</b>\n\n{body}\n\n"
            f"<i>В базе документов: {stats.get('docs', 0)}, приказов: {stats.get('orders', 0)}, "
            f"номеров досаров: {stats.get('numbers', 0)}</i>",
            kb(back()),
        )

    @router.callback_query(F.data == "dz:notif")
    async def toggle_notif(call: CallbackQuery) -> None:
        on = await svc.toggle_orders(call.from_user.id)
        await call.answer(f"Уведомления о новых приказах {'включены' if on else 'выключены'}")
        text, markup = await menu_view(call.from_user.id)
        await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)

    @router.message(Command("refresh"), F.from_user.id.in_(ctx.settings.admin_ids))
    async def admin_refresh(message: Message) -> None:
        if monitor.busy:
            await message.answer("Проверка уже идёт.")
            return
        await message.answer("Запускаю проверку ANC…")
        result = await monitor.run_cycle()
        errors = "\n".join(escape(e) for e in result.errors[:5])
        await message.answer(f"Готово: {result.summary()}" + (f"\n\n<code>{errors}</code>" if errors else ""))

    return Module(key="dosar", title="📁 Досар (гражданство RO)", router=router)


async def notify(ctx: AppContext, svc: DosarService, result: CycleResult) -> None:
    per_user: dict[int, list[str]] = defaultdict(list)

    for m in result.new_mentions:
        for sub in await svc.subscribers(m.number):
            label = f" · {escape(sub['label'])}" if sub["label"] else ""
            head = "🎉 <b>Ваш номер найден в приказе ANC!</b>" if m.kind == "ordin" else "🔔 <b>Обновление по досару</b>"
            per_user[sub["user_id"]].append(
                f"{head}\n<code>{m.number}</code>{label}\n"
                f"📄 <a href=\"{escape(m.doc_url, quote=True)}\">{escape(m.doc_title)}</a> "
                f"({texts.KIND_TITLES.get(m.kind, m.kind)})\n<code>{escape(m.line)}</code>"
            )

    if result.new_orders:
        listing = "\n".join(
            f"• <a href=\"{escape(o.url, quote=True)}\">{escape(o.title)}</a>" for o in result.new_orders[:10]
        )
        for user_id in await svc.order_subscribers():
            per_user[user_id].append(f"📰 <b>ANC опубликовал новые приказы</b>\n{listing}")

    items = [(uid, "\n\n".join(msgs)[:4000]) for uid, msgs in per_user.items()]
    if items:
        await send_many(ctx, items)

    # Сообщить админам, если сайт ANC перестал отвечать (один раз до восстановления).
    failed = result.pages_ok == 0
    was_failed = await ctx.db.get_kv("anc_failing") == "1"
    if failed != was_failed:
        await ctx.db.set_kv("anc_failing", "1" if failed else "0")
        msg = (
            "⚠️ Сайт ANC не отвечает боту. Если так будет долго — проверьте ANC_PROXY.\n"
            + escape("\n".join(result.errors[:3]))
            if failed else "✅ Сайт ANC снова доступен."
        )
        await send_many(ctx, [(a, msg) for a in ctx.settings.admin_ids])
