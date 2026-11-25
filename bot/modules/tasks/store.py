"""Напоминания и списки дел в SQLite."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from bot.db import Database, utcnow

MAX_REMINDERS = 50
MAX_LISTS = 10
MAX_ITEMS = 50
DEFAULT_TZ = "Europe/Bucharest"


def to_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class TaskStore:
    def __init__(self, db: Database):
        self.db = db

    async def user_tz(self, user_id: int) -> str:
        """Часовой пояс — из активного места в «Погоде», иначе Бухарест."""
        row = await self.db.fetchone(
            "SELECT p.tz FROM users u JOIN places p ON p.id = u.active_place_id WHERE u.id = ?", (user_id,)
        )
        return (row["tz"] if row and row["tz"] else None) or DEFAULT_TZ

    # ---------- напоминания ----------
    async def add_reminder(self, user_id: int, text: str, due: datetime, tz: str, repeat: str | None) -> int | None:
        count = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM reminders WHERE user_id = ? AND active = 1", (user_id,)
        )
        if count and count["c"] >= MAX_REMINDERS:
            return None
        cur = await self.db.execute(
            "INSERT INTO reminders(user_id, text, due_at, tz, repeat, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (user_id, text[:300], to_utc(due), tz, repeat, utcnow()),
        )
        return cur.lastrowid

    async def reminders(self, user_id: int) -> list[aiosqlite.Row]:
        return await self.db.fetchall(
            "SELECT * FROM reminders WHERE user_id = ? AND active = 1 ORDER BY due_at", (user_id,)
        )

    async def reminder(self, user_id: int, rid: int) -> aiosqlite.Row | None:
        return await self.db.fetchone("SELECT * FROM reminders WHERE id = ? AND user_id = ?", (rid, user_id))

    async def cancel_reminder(self, user_id: int, rid: int) -> None:
        await self.db.execute("UPDATE reminders SET active = 0 WHERE id = ? AND user_id = ?", (rid, user_id))

    async def due(self, now: datetime) -> list[aiosqlite.Row]:
        return await self.db.fetchall(
            "SELECT * FROM reminders WHERE active = 1 AND due_at <= ? ORDER BY due_at", (to_utc(now),)
        )

    async def reschedule(self, rid: int, due: datetime | None) -> None:
        if due is None:
            await self.db.execute("UPDATE reminders SET active = 0 WHERE id = ?", (rid,))
        else:
            await self.db.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (to_utc(due), rid))

    # ---------- списки ----------
    async def lists(self, user_id: int) -> list[aiosqlite.Row]:
        rows = await self.db.fetchall(
            "SELECT l.*, (SELECT COUNT(*) FROM todo_items i WHERE i.list_id = l.id AND i.done = 0) AS open "
            "FROM todo_lists l WHERE l.user_id = ? ORDER BY l.id",
            (user_id,),
        )
        if not rows:
            await self.add_list(user_id, "📝 Дела")
            await self.add_list(user_id, "🛒 Покупки")
            return await self.lists(user_id)
        return rows

    async def add_list(self, user_id: int, title: str) -> int | None:
        count = await self.db.fetchone("SELECT COUNT(*) AS c FROM todo_lists WHERE user_id = ?", (user_id,))
        if count and count["c"] >= MAX_LISTS:
            return None
        cur = await self.db.execute(
            "INSERT INTO todo_lists(user_id, title, created_at) VALUES(?, ?, ?)", (user_id, title[:40], utcnow())
        )
        return cur.lastrowid

    async def get_list(self, user_id: int, list_id: int) -> aiosqlite.Row | None:
        return await self.db.fetchone("SELECT * FROM todo_lists WHERE id = ? AND user_id = ?", (list_id, user_id))

    async def delete_list(self, user_id: int, list_id: int) -> None:
        await self.db.execute("DELETE FROM todo_lists WHERE id = ? AND user_id = ?", (list_id, user_id))

    async def items(self, list_id: int) -> list[aiosqlite.Row]:
        return await self.db.fetchall(
            "SELECT * FROM todo_items WHERE list_id = ? ORDER BY done, id", (list_id,)
        )

    async def add_items(self, list_id: int, texts: list[str]) -> int:
        count = await self.db.fetchone("SELECT COUNT(*) AS c FROM todo_items WHERE list_id = ?", (list_id,))
        room = MAX_ITEMS - (count["c"] if count else 0)
        texts = [t.strip()[:100] for t in texts if t.strip()][:max(room, 0)]
        for t in texts:
            await self.db.execute(
                "INSERT INTO todo_items(list_id, text, created_at) VALUES(?, ?, ?)", (list_id, t, utcnow())
            )
        return len(texts)

    async def toggle_item(self, user_id: int, item_id: int) -> int | None:
        """Отметить/снять отметку. Возвращает id списка или None, если пункт чужой."""
        row = await self.db.fetchone(
            "SELECT i.list_id FROM todo_items i JOIN todo_lists l ON l.id = i.list_id "
            "WHERE i.id = ? AND l.user_id = ?",
            (item_id, user_id),
        )
        if not row:
            return None
        await self.db.execute("UPDATE todo_items SET done = 1 - done WHERE id = ?", (item_id,))
        return row["list_id"]

    async def clear_done(self, list_id: int) -> None:
        await self.db.execute("DELETE FROM todo_items WHERE list_id = ? AND done = 1", (list_id,))
