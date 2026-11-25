"""Модуль «Погода»: прогноз по геопозиции пользователя."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.core.registry import AppContext, Module
from bot.modules.weather.api import WeatherClient

log = logging.getLogger(__name__)

CANCEL_TEXT = "❌ Отмена"

ASK_LOCATION = (
    "📍 <b>Где вы находитесь?</b>\n\n"
    "Нажмите кнопку <b>«📍 Отправить геопозицию»</b> внизу экрана.\n"
    "<i>Кнопка работает в Telegram на телефоне. Ещё можно отправить любую точку "
    "через 📎 → «Геопозиция».</i>"
)


def location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Отправить геопозицию", request_location=True)],
            [KeyboardButton(text=CANCEL_TEXT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def weather_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [Btn(text="🔄 Обновить", callback_data="wx:refresh"),
         Btn(text="📍 Другое место", callback_data="wx:ask")],
        [Btn(text="🏠 Главное меню", callback_data="core:main")],
    ])


def setup(ctx: AppContext) -> Module:
    router = Router(name="weather")
    client = WeatherClient()
    ctx.shutdown.append(client.close)

    async def render(user_id: int) -> str | None:
        row = await ctx.db.fetchone("SELECT lat, lon, place FROM users WHERE id = ?", (user_id,))
        if not row or row["lat"] is None:
            return None
        try:
            forecast = await client.forecast(row["lat"], row["lon"])
        except Exception:  # noqa: BLE001
            log.exception("weather fetch failed")
            return "⚠️ Не удалось получить погоду. Попробуйте ещё раз через минуту."
        return forecast.render(row["place"])

    async def ask_location(chat_id: int) -> None:
        await ctx.bot.send_message(chat_id, ASK_LOCATION, reply_markup=location_kb())

    @router.callback_query(F.data == "m:weather")
    async def open_weather(call: CallbackQuery) -> None:
        text = await render(call.from_user.id)
        await call.answer()
        if text is None:
            await ask_location(call.message.chat.id)
        else:
            await call.message.edit_text(text, reply_markup=weather_kb())

    @router.message(Command("weather"))
    async def weather_cmd(message: Message) -> None:
        text = await render(message.from_user.id)
        if text is None:
            await ask_location(message.chat.id)
        else:
            await message.answer(text, reply_markup=weather_kb())

    @router.callback_query(F.data == "wx:refresh")
    async def refresh(call: CallbackQuery) -> None:
        text = await render(call.from_user.id)
        if text is None:
            await call.answer()
            await ask_location(call.message.chat.id)
            return
        await call.answer("Обновлено")
        if text != call.message.html_text:
            await call.message.edit_text(text, reply_markup=weather_kb())

    @router.callback_query(F.data == "wx:ask")
    async def ask(call: CallbackQuery) -> None:
        await call.answer()
        await ask_location(call.message.chat.id)

    @router.message(F.location)
    async def got_location(message: Message) -> None:
        loc = message.location
        place = await client.place_name(loc.latitude, loc.longitude)
        await ctx.db.execute(
            "UPDATE users SET lat = ?, lon = ?, place = ? WHERE id = ?",
            (loc.latitude, loc.longitude, place, message.from_user.id),
        )
        await message.answer(
            f"✅ Место сохранено{': ' + place if place else ''}.", reply_markup=ReplyKeyboardRemove()
        )
        await message.answer(await render(message.from_user.id) or "", reply_markup=weather_kb())

    @router.message(F.text == CANCEL_TEXT)
    async def cancel(message: Message) -> None:
        await message.answer("Хорошо. Главное меню — /start", reply_markup=ReplyKeyboardRemove())

    return Module(key="weather", title="🌤 Погода", router=router)
