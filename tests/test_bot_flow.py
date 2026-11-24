"""Прогон экранов бота через Dispatcher с поддельной сессией Telegram (без сети)."""
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

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

    async def msg(text):
        counter["n"] += 1
        upd = Update(update_id=counter["n"], message=Message(
            message_id=counter["n"], date=datetime.now(), chat=CHAT, from_user=USER, text=text,
            entities=None))
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


async def test_full_flow(env):
    msg, cb, db, session = env
    r = await msg("/start")
    assert "Выберите раздел" in r.text
    r = await cb("m:dosar")
    assert "Досаров пока нет" in r.text
    r = await cb("dz:add")
    assert "12345/RD/2021" in r.text
    r = await msg("not a number")
    assert "Не похоже" in r.text
    r = await msg("12345 / rd / 2021")
    assert "12345/RD/2021" in r.text
    r = await msg("Мама")
    assert "Досар добавлен" in r.text and "Мама" in r.text
    row = await db.fetchone("SELECT id FROM dosars")
    did = row["id"]
    r = await cb(f"dz:date:{did}")
    r = await msg("99.99.2020")
    assert "Не понял" in r.text
    r = await msg("15.03.2021")
    assert "Подан: 15.03.2021" in r.text
    r = await cb(f"dz:hist:{did}")
    assert "ещё не встречался" in r.text
    r = await cb("dz:orders")
    assert "Приказы ещё не загружены" in r.text
    r = await cb("dz:notif")
    buttons = [b.text for row in r.reply_markup.inline_keyboard for b in row]
    assert "🔔 Новые приказы: выкл" in buttons
    r = await cb("dz:check")
    assert "проверку" in r.text
    r = await msg("/check 777/RD/2019")
    assert "777/RD/2019" in r.text
    r = await msg("/admin")
    assert "Пользователей: 1" in r.text
    r = await cb(f"dz:del:{did}")
    r = await cb(f"dz:delok:{did}")
    assert "Досаров пока нет" in r.text
    r = await cb("m:portfolio")
    assert "портфель" in r.text
    r = await cb("core:main")
    assert "Выберите раздел" in r.text
