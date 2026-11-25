"""Места и настройки уведомлений пользователей модуля «Погода»."""
from __future__ import annotations

import aiosqlite

from bot.db import Database, utcnow

MAX_PLACES = 10
DEFAULT_TZ = "Europe/Bucharest"


class PlaceStore:
    def __init__(self, db: Database):
        self.db = db

    async def migrate_legacy(self) -> None:
        """Перенести одно место из старой версии (users.lat/lon) в таблицу places."""
        rows = await self.db.fetchall(
            "SELECT id, lat, lon, place FROM users WHERE lat IS NOT NULL AND active_place_id IS NULL"
        )
        for r in rows:
            pid = await self.add(r["id"], r["place"] or "Моё место", r["lat"], r["lon"])
            if pid:
                await self.set_active(r["id"], pid)

    async def list(self, user_id: int) -> list[aiosqlite.Row]:
        return await self.db.fetchall("SELECT * FROM places WHERE user_id = ? ORDER BY id", (user_id,))

    async def get(self, user_id: int, place_id: int) -> aiosqlite.Row | None:
        return await self.db.fetchone(
            "SELECT * FROM places WHERE id = ? AND user_id = ?", (place_id, user_id)
        )

    async def active(self, user_id: int) -> aiosqlite.Row | None:
        row = await self.db.fetchone(
            "SELECT p.* FROM places p JOIN users u ON u.active_place_id = p.id WHERE u.id = ?", (user_id,)
        )
        if row:
            return row
        # активное место удалили — берём первое из оставшихся
        first = await self.db.fetchone("SELECT * FROM places WHERE user_id = ? ORDER BY id LIMIT 1", (user_id,))
        if first:
            await self.set_active(user_id, first["id"])
        return first

    async def add(self, user_id: int, name: str, lat: float, lon: float) -> int | None:
        count = await self.db.fetchone("SELECT COUNT(*) AS c FROM places WHERE user_id = ?", (user_id,))
        if count and count["c"] >= MAX_PLACES:
            return None
        cur = await self.db.execute(
            "INSERT INTO places(user_id, name, lat, lon, created_at) VALUES(?, ?, ?, ?, ?)",
            (user_id, name[:40], lat, lon, utcnow()),
        )
        return cur.lastrowid

    async def rename(self, user_id: int, place_id: int, name: str) -> None:
        await self.db.execute(
            "UPDATE places SET name = ? WHERE id = ? AND user_id = ?", (name[:40], place_id, user_id)
        )

    async def set_tz(self, place_id: int, tz: str | None) -> None:
        if tz:
            await self.db.execute("UPDATE places SET tz = ? WHERE id = ? AND tz IS NOT ?", (tz, place_id, tz))

    async def delete(self, user_id: int, place_id: int) -> None:
        await self.db.execute("DELETE FROM places WHERE id = ? AND user_id = ?", (place_id, user_id))

    async def set_active(self, user_id: int, place_id: int) -> None:
        await self.db.execute("UPDATE users SET active_place_id = ? WHERE id = ?", (place_id, user_id))

    # ---------- уведомления ----------
    async def settings(self, user_id: int) -> aiosqlite.Row:
        row = await self.db.fetchone("SELECT morning_time, alerts FROM users WHERE id = ?", (user_id,))
        assert row
        return row

    async def set_morning(self, user_id: int, hhmm: str | None) -> None:
        await self.db.execute(
            "UPDATE users SET morning_time = ?, morning_sent = NULL WHERE id = ?", (hhmm, user_id)
        )

    async def toggle_alerts(self, user_id: int) -> bool:
        await self.db.execute("UPDATE users SET alerts = 1 - alerts WHERE id = ?", (user_id,))
        return bool((await self.settings(user_id))["alerts"])

    async def subscribers(self) -> list[aiosqlite.Row]:
        """Пользователи с включёнными уведомлениями и их активное место."""
        return await self.db.fetchall(
            "SELECT u.id AS user_id, u.morning_time, u.morning_sent, u.alerts, "
            "p.id AS place_id, p.name, p.lat, p.lon, p.tz "
            "FROM users u JOIN places p ON p.id = u.active_place_id "
            "WHERE u.morning_time IS NOT NULL OR u.alerts = 1"
        )

    async def mark_morning(self, user_id: int, day: str) -> None:
        await self.db.execute("UPDATE users SET morning_sent = ? WHERE id = ?", (day, user_id))
