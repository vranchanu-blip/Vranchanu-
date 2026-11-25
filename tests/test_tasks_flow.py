from datetime import datetime, timedelta, timezone

from aiogram.methods import SendMessage

from tests.test_bot_flow import env  # noqa: F401  (фикстура)


def buttons(r):
    return [b for row in r.reply_markup.inline_keyboard for b in row]


async def test_reminders_and_lists(env):  # noqa: F811
    msg, cb, db, session = env
    fire = session.ctx.state["tasks.fire"]

    r = await cb("m:tasks")
    assert "Ближайшие напоминания" in r.text
    titles = [b.text for b in buttons(r)]
    assert "📝 Дела · 0" in titles and "🛒 Покупки · 0" in titles

    # свободный текст «напомни …»
    r = await msg("напомни через 10 минут проверить духовку")
    assert "Напомню <b>сегодня" in r.text and "Проверить духовку" in r.text

    # без времени — бот спрашивает когда
    r = await msg("напомни купить молоко")
    assert "Когда напомнить" in r.text
    r = await msg("ерунда")
    assert "Не понял время" in r.text
    r = await msg("завтра в 18:00")
    assert "Напомню <b>завтра в 18:00</b>" in r.text and "Купить молоко" in r.text

    # повторяющееся через кнопку
    r = await cb("td:rem:new")
    assert "Например" in r.text
    r = await msg("каждый день в 8:00 витамины")
    assert "каждый день" in r.text

    r = await cb("td:rem:list")
    assert "1." in r.text and "Витамины" in r.text and "🔁" in r.text

    # срабатывание: шлём просроченные, повторяющееся переносится на следующий день
    rows = await db.fetchall("SELECT id, repeat, due_at FROM reminders WHERE active = 1 ORDER BY id")
    assert len(rows) == 3
    later = datetime.now(timezone.utc) + timedelta(days=2)
    sent_before = len([c for c in session.calls if isinstance(c, SendMessage)])
    await fire(later)
    sent = [c for c in session.calls if isinstance(c, SendMessage)][sent_before:]
    assert len(sent) == 3 and all("⏰ <b>Напоминание</b>" in c.text for c in sent)
    active = await db.fetchall("SELECT text, repeat, due_at FROM reminders WHERE active = 1")
    assert [a["text"] for a in active] == ["Витамины"]
    assert datetime.fromisoformat(active[0]["due_at"]) > later

    # отложить на час — появляется разовое напоминание
    rid = rows[0]["id"]
    await cb(f"td:snz:{rid}:1h")
    assert len(await db.fetchall("SELECT * FROM reminders WHERE active = 1")) == 2

    # списки
    lst = (await db.fetchone("SELECT id FROM todo_lists WHERE title = '🛒 Покупки'"))["id"]
    r = await cb(f"td:l:{lst}")
    assert "Список пуст" in r.text
    await cb(f"td:add:{lst}")
    r = await msg("молоко\nхлеб\n\nсыр")
    assert "Сделано 0 из 3" in r.text
    item = (await db.fetchone("SELECT id FROM todo_items WHERE text = 'хлеб'"))["id"]
    r = await cb(f"td:i:{item}")
    assert "Сделано 1 из 3" in r.text and "✅ хлеб" in [b.text for b in buttons(r)]
    r = await cb(f"td:clr:{lst}")
    assert "Сделано 0 из 2" in r.text

    await cb("td:l:new")
    r = await msg("✈️ В отпуск")
    assert "В отпуск" in r.text
    new_id = (await db.fetchone("SELECT id FROM todo_lists WHERE title = '✈️ В отпуск'"))["id"]
    r = await cb(f"td:ldelok:{new_id}")
    assert "В отпуск" not in " ".join(b.text for b in buttons(r))
