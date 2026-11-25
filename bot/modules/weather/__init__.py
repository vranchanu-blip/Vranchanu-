"""Модуль «Погода»: прогноз по сохранённым местам, утренний прогноз и предупреждения."""
from __future__ import annotations

import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.core.menu import send_many
from bot.core.registry import AppContext, Module
from bot.modules.weather.api import WeatherClient
from bot.modules.weather.notifier import WeatherNotifier
from bot.modules.weather.store import MAX_PLACES, PlaceStore

log = logging.getLogger(__name__)

CANCEL_TEXT = "❌ Отмена"
MORNING_TIMES = ["06:00", "06:30", "07:00", "07:30", "08:00", "08:30", "09:00", "09:30"]
NAME_PRESETS = {"home": "🏠 Дом", "work": "💼 Работа", "dacha": "🏡 Дача"}
TIME_RE = re.compile(r"^\s*([01]?\d|2[0-3])[:.\s]([0-5]\d)\s*$")

ASK_LOCATION = (
    "📍 <b>Добавим место</b>\n\n"
    "Нажмите кнопку <b>«📍 Отправить геопозицию»</b> внизу экрана — это ваше текущее место.\n\n"
    "<i>Другой город: 📎 → «Геопозиция» → передвиньте метку и отправьте.</i>"
)


class PlaceName(StatesGroup):
    name = State()


class MorningTime(StatesGroup):
    time = State()


def kb(*rows: list[Btn]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Отправить геопозицию", request_location=True)],
            [KeyboardButton(text=CANCEL_TEXT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def safe_edit(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


def setup(ctx: AppContext) -> Module:
    router = Router(name="weather")
    client = WeatherClient()
    store = PlaceStore(ctx.db)

    async def send(user_id: int, text: str) -> None:
        await send_many(ctx, [(user_id, text)])

    notifier = WeatherNotifier(ctx.db, store, client, send)
    ctx.background.append(notifier.run_forever)
    ctx.shutdown.append(client.close)

    # ---------- экраны ----------
    async def weather_view(user_id: int, fresh: bool = False) -> tuple[str, InlineKeyboardMarkup] | None:
        place = await store.active(user_id)
        if not place:
            return None
        places = await store.list(user_id)
        switch = [
            Btn(text=("• " if p["id"] == place["id"] else "") + p["name"], callback_data=f"wx:pl:{p['id']}")
            for p in places[:3]
        ] if len(places) > 1 else []
        markup = kb(
            switch,
            [Btn(text="🔄 Обновить", callback_data="wx:refresh"), Btn(text="📍 Мои места", callback_data="wx:places")],
            [Btn(text="🔔 Уведомления", callback_data="wx:notif")],
            [Btn(text="🏠 Главное меню", callback_data="core:main")],
        )
        try:
            fc = await client.forecast(place["lat"], place["lon"], fresh=fresh)
        except Exception:  # noqa: BLE001
            log.exception("weather fetch failed")
            return "⚠️ Не удалось получить погоду. Попробуйте ещё раз через минуту.", markup
        await store.set_tz(place["id"], fc.tz)
        return fc.render(place["name"]), markup

    async def places_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        places = await store.list(user_id)
        active = await store.active(user_id)
        rows = [
            [Btn(text=("✅ " if active and p["id"] == active["id"] else "📍 ") + p["name"],
                 callback_data=f"wx:pl:{p['id']}")]
            for p in places
        ]
        extra = [Btn(text="➕ Добавить", callback_data="wx:add")]
        if places:
            extra.append(Btn(text="🗑 Удалить", callback_data="wx:delmode"))
        text = (
            "📍 <b>Мои места</b>\n\n"
            + ("Нажмите на место, чтобы посмотреть погоду в нём. "
               "Утренний прогноз и предупреждения приходят для отмеченного ✅."
               if places else "Мест пока нет — добавьте первое.")
        )
        return text, kb(*rows, extra, [Btn(text="⬅️ Назад", callback_data="wx:show")])

    async def notif_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        st = await store.settings(user_id)
        place = await store.active(user_id)
        morning = st["morning_time"]
        text = (
            "🔔 <b>Уведомления</b>\n\n"
            f"☀️ Утренний прогноз: <b>{'каждый день в ' + morning if morning else 'выключен'}</b>\n"
            f"⚠️ Предупреждения о непогоде: <b>{'включены' if st['alerts'] else 'выключены'}</b>\n"
            "<i>Гроза, сильный дождь, снег, сильный ветер, заморозки — за несколько часов. "
            "С 22:00 до 7:00 не беспокою.</i>\n\n"
            f"📍 Для места: <b>{escape(place['name']) if place else '—'}</b>"
        )
        return text, kb(
            [Btn(text=f"☀️ Утренний прогноз: {morning or 'выкл'}", callback_data="wx:morning")],
            [Btn(text=f"⚠️ Предупреждения: {'вкл' if st['alerts'] else 'выкл'}", callback_data="wx:alerts")],
            [Btn(text="⬅️ Назад", callback_data="wx:show")],
        )

    def morning_picker() -> tuple[str, InlineKeyboardMarkup]:
        times = [Btn(text=t, callback_data=f"wx:mt:{t.replace(':', '')}") for t in MORNING_TIMES]
        return (
            "☀️ <b>Во сколько присылать утренний прогноз?</b>\n<i>Время места, для которого прогноз.</i>",
            kb(times[:4], times[4:],
               [Btn(text="⌨️ Своё время", callback_data="wx:mt:custom"),
                Btn(text="🔕 Выключить", callback_data="wx:mt:off")],
               [Btn(text="⬅️ Назад", callback_data="wx:notif")]),
        )

    async def show_weather(target: Message | CallbackQuery, user_id: int, fresh: bool = False) -> None:
        view = await weather_view(user_id, fresh)
        if view is None:
            chat_id = target.message.chat.id if isinstance(target, CallbackQuery) else target.chat.id
            await ctx.bot.send_message(chat_id, ASK_LOCATION, reply_markup=location_kb())
            if isinstance(target, CallbackQuery):
                await target.answer()
            return
        if isinstance(target, CallbackQuery):
            await safe_edit(target, *view)
            await target.answer("Обновлено" if fresh else None)
        else:
            await target.answer(view[0], reply_markup=view[1])

    # ---------- погода ----------
    @router.callback_query(F.data.in_({"m:weather", "wx:show"}))
    async def open_weather(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await show_weather(call, call.from_user.id)

    @router.message(Command("weather"))
    async def weather_cmd(message: Message, state: FSMContext) -> None:
        await state.clear()
        await show_weather(message, message.from_user.id)

    @router.callback_query(F.data == "wx:refresh")
    async def refresh(call: CallbackQuery) -> None:
        await show_weather(call, call.from_user.id, fresh=True)

    # ---------- места ----------
    @router.callback_query(F.data == "wx:places")
    async def places(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await safe_edit(call, *await places_view(call.from_user.id))
        await call.answer()

    @router.callback_query(F.data.startswith("wx:pl:"))
    async def choose_place(call: CallbackQuery) -> None:
        place = await store.get(call.from_user.id, int(call.data.rsplit(":", 1)[1]))
        if not place:
            await call.answer("Место не найдено", show_alert=True)
            return
        await store.set_active(call.from_user.id, place["id"])
        await show_weather(call, call.from_user.id)

    @router.callback_query(F.data == "wx:add")
    async def add_place(call: CallbackQuery) -> None:
        if len(await store.list(call.from_user.id)) >= MAX_PLACES:
            await call.answer(f"Можно сохранить не больше {MAX_PLACES} мест", show_alert=True)
            return
        await call.answer()
        await ctx.bot.send_message(call.message.chat.id, ASK_LOCATION, reply_markup=location_kb())

    @router.callback_query(F.data == "wx:delmode")
    async def delete_mode(call: CallbackQuery) -> None:
        rows = [[Btn(text=f"🗑 {p['name']}", callback_data=f"wx:del:{p['id']}")]
                for p in await store.list(call.from_user.id)]
        await safe_edit(call, "🗑 <b>Какое место удалить?</b>",
                        kb(*rows, [Btn(text="⬅️ Назад", callback_data="wx:places")]))
        await call.answer()

    @router.callback_query(F.data.startswith("wx:del:"))
    async def delete_place(call: CallbackQuery) -> None:
        await store.delete(call.from_user.id, int(call.data.rsplit(":", 1)[1]))
        await safe_edit(call, *await places_view(call.from_user.id))
        await call.answer("Удалено")

    @router.message(F.location)
    async def got_location(message: Message, state: FSMContext) -> None:
        loc = message.location
        user_id = message.from_user.id
        name = await client.place_name(loc.latitude, loc.longitude) or "Моё место"
        place_id = await store.add(user_id, name, loc.latitude, loc.longitude)
        if place_id is None:
            await message.answer(f"⚠️ Можно сохранить не больше {MAX_PLACES} мест. Удалите лишнее в «📍 Мои места».",
                                 reply_markup=ReplyKeyboardRemove())
            return
        await store.set_active(user_id, place_id)
        await state.set_state(PlaceName.name)
        await state.update_data(place_id=place_id)
        await message.answer(f"✅ Место сохранено: <b>{escape(name)}</b>", reply_markup=ReplyKeyboardRemove())
        presets = [Btn(text=t, callback_data=f"wx:nm:{place_id}:{k}") for k, t in NAME_PRESETS.items()]
        await message.answer(
            "Как назвать это место? Выберите или напишите своё название.",
            reply_markup=kb(presets, [Btn(text=f"✅ Оставить «{name[:30]}»", callback_data=f"wx:nm:{place_id}:keep")]),
        )

    @router.callback_query(F.data.startswith("wx:nm:"))
    async def name_preset(call: CallbackQuery, state: FSMContext) -> None:
        _, _, place_id, key = call.data.split(":")
        await state.clear()
        if key in NAME_PRESETS:
            await store.rename(call.from_user.id, int(place_id), NAME_PRESETS[key])
        await show_weather(call, call.from_user.id)

    @router.message(PlaceName.name, F.text, ~F.text.startswith("/"))
    async def name_text(message: Message, state: FSMContext) -> None:
        place_id = (await state.get_data())["place_id"]
        await state.clear()
        await store.rename(message.from_user.id, place_id, message.text.strip())
        await show_weather(message, message.from_user.id)

    @router.message(F.text == CANCEL_TEXT)
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Хорошо. Главное меню — /start", reply_markup=ReplyKeyboardRemove())

    # ---------- уведомления ----------
    @router.callback_query(F.data == "wx:notif")
    async def notif(call: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await safe_edit(call, *await notif_view(call.from_user.id))
        await call.answer()

    @router.callback_query(F.data == "wx:alerts")
    async def toggle_alerts(call: CallbackQuery) -> None:
        on = await store.toggle_alerts(call.from_user.id)
        await safe_edit(call, *await notif_view(call.from_user.id))
        await call.answer("Предупреждения включены" if on else "Предупреждения выключены")

    @router.callback_query(F.data == "wx:morning")
    async def morning(call: CallbackQuery) -> None:
        await safe_edit(call, *morning_picker())
        await call.answer()

    @router.callback_query(F.data.startswith("wx:mt:"))
    async def morning_set(call: CallbackQuery, state: FSMContext) -> None:
        value = call.data.rsplit(":", 1)[1]
        if value == "custom":
            await state.set_state(MorningTime.time)
            await safe_edit(call, "⌨️ Напишите время в формате <code>ЧЧ:ММ</code>, например <code>07:15</code>.",
                            kb([Btn(text="⬅️ Назад", callback_data="wx:morning")]))
            await call.answer()
            return
        hhmm = None if value == "off" else f"{value[:2]}:{value[2:]}"
        await store.set_morning(call.from_user.id, hhmm)
        await safe_edit(call, *await notif_view(call.from_user.id))
        await call.answer(f"Буду присылать в {hhmm}" if hhmm else "Утренний прогноз выключен")

    @router.message(MorningTime.time, F.text)
    async def morning_text(message: Message, state: FSMContext) -> None:
        m = TIME_RE.match(message.text)
        if not m:
            await message.answer("Не понял время. Пример: <code>07:15</code>")
            return
        await state.clear()
        await store.set_morning(message.from_user.id, f"{int(m.group(1)):02d}:{m.group(2)}")
        text, markup = await notif_view(message.from_user.id)
        await message.answer(text, reply_markup=markup)

    return Module(key="weather", title="🌤 Погода", router=router)
