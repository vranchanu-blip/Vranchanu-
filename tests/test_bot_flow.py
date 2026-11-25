"""Прогон экранов бота через Dispatcher с поддельной сессией Telegram (без сети)."""
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Location, Message, Update, User

from bot.__main__ import MODULES, UserMiddleware
from bot.config import Settings
from bot.core.menu import setup_core
from bot.core.registry import AppContext
from bot.db import Database


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=len(self.calls), date=datetime.now(),
                           chat=Chat(id=1, type="private"), text=method.text)
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(f"unexpected {method!r}")

    async def stream_content(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass


USER = User(id=1, is_bot=False, first_name="Test")
CHAT = Chat(id=1, type="private")


@pytest.fixture
async def env():
    db = Database(":memory:")
    await db.connect()
    session = FakeSession()
    bot = Bot("42:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    settings = Settings(bot_token="42:TEST", admin_ids=frozenset({1}), db_path=":memory:")
    ctx = AppContext(settings=settings, db=db, bot=bot)
    loaded = [m.setup(ctx) for m in MODULES]
    dp = Dispatcher()
    dp.update.outer_middleware(UserMiddleware(db))
    dp.include_router(setup_core(ctx, loaded))
    for m in loaded:
        dp.include_router(m.router)
    counter = {"n": 0}

    async def msg(text=None, location=None):
        counter["n"] += 1
        upd = Update(update_id=counter["n"], message=Message(
            message_id=counter["n"], date=datetime.now(), chat=CHAT, from_user=USER, text=text,
            location=location, entities=None))
        await dp.feed_update(bot, upd)
        return session.calls[-1]

    async def cb(data):
        counter["n"] += 1
        m = Message(message_id=1, date=datetime.now(), chat=CHAT, from_user=USER, text="x")
        upd = Update(update_id=counter["n"], callback_query=CallbackQuery(
            id=str(counter["n"]), from_user=USER, chat_instance="c", message=m, data=data))
        await dp.feed_update(bot, upd)
        return [c for c in session.calls if isinstance(c, (EditMessageText, SendMessage))][-1]

    yield msg, cb, db, session
    for close in ctx.shutdown:
        await close()
    await db.close()


FORECAST = {
    "current": {"time": "2026-11-25T11:45", "temperature_2m": 5.4, "apparent_temperature": 2.1,
                "relative_humidity_2m": 81, "weather_code": 3, "wind_speed_10m": 4.2,
                "wind_direction_10m": 315, "is_day": 1},
    "hourly": {"time": [f"2026-11-25T{h:02d}:00" for h in range(24)],
               "temperature_2m": [float(h % 9) for h in range(24)],
               "weather_code": [61] * 24, "precipitation_probability": [50] * 24},
    "daily": {"time": ["2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28"],
              "weather_code": [3, 61, 0, 71], "temperature_2m_max": [7, 6, 4, 1],
              "temperature_2m_min": [1, 2, -3, -5], "precipitation_probability_max": [20, 70, 0, 40],
              "sunrise": ["2026-11-25T07:31"] * 4, "sunset": ["2026-11-25T16:45"] * 4},
}


def test_render_forecast():
    from bot.modules.weather.api import Forecast
    text = Forecast(FORECAST).render("Кишинёв, Молдова")
    assert "Кишинёв, Молдова" in text and "<b>+5°</b>  пасмурно" in text
    assert "Ощущается как +2°" in text and "4 м/с, СЗ" in text
    assert "<code>12:00</code>" in text and "<code>21:00</code>" in text
    assert "Чт 26.11" in text and "-5°…+1°" in text and "☔ 70%" in text
    assert "🌅 07:31" in text


async def test_full_flow(env, monkeypatch):
    from bot.modules.weather.api import Forecast, WeatherClient

    async def fake_forecast(self, lat, lon):
        return Forecast(FORECAST)

    async def fake_place(self, lat, lon):
        return "Кишинёв, Молдова"

    monkeypatch.setattr(WeatherClient, "forecast", fake_forecast)
    monkeypatch.setattr(WeatherClient, "place_name", fake_place)

    msg, cb, db, session = env
    r = await msg("/start")
    assert "Test!" in r.text and "Чем займёмся" in r.text
    titles = [b.text for row in r.reply_markup.inline_keyboard for b in row]
    assert titles[:2] == ["🌤 Погода", "💼 Портфель (скоро)"]

    r = await cb("m:weather")
    assert "Где вы находитесь" in r.text and r.reply_markup.keyboard[0][0].request_location

    r = await msg(location=Location(latitude=47.0, longitude=28.8))
    assert "Кишинёв, Молдова" in r.text and "Прогноз" in r.text
    saved = [c for c in session.calls if isinstance(c, SendMessage)][-2]
    assert "Место сохранено: Кишинёв, Молдова" in saved.text
    row = await db.fetchone("SELECT lat, place FROM users WHERE id = 1")
    assert row["lat"] == 47.0

    r = await cb("m:weather")
    assert isinstance(r, EditMessageText) and "Кишинёв" in r.text
    r = await msg("/weather")
    assert "Кишинёв" in r.text

    r = await msg("❌ Отмена")
    assert "Главное меню" in r.text
    r = await cb("core:help")
    assert "Погода" in r.text
    r = await msg("/admin")
    assert "Пользователей: 1" in r.text
    r = await cb("m:portfolio")
    assert "портфель" in r.text
    r = await cb("core:main")
    assert "Чем займёмся" in r.text
