"""Запросы к БД для модуля «Досар»."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import aiosqlite

from bot.db import Database, utcnow
from bot.modules.dosar.anc import number_seq, number_year

MAX_DOSARS_PER_USER = 10


@dataclass(frozen=True)
class YearProgress:
    year: int
    in_orders: int          # сколько разных досаров этого года найдено в приказах
    max_seq_in_orders: int | None
    in_orders_30d: int      # из них впервые замечено за 30 дней
    position_hint: int | None  # сколько досаров года с номером меньше вашего уже в приказах


class DosarService:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, user_id: int, number: str, label: str | None) -> int | None:
        """Возвращает id или None, если такой досар уже есть / превышен лимит."""
        count = await self.db.fetchone("SELECT COUNT(*) AS c FROM dosars WHERE user_id = ?", (user_id,))
        if count and count["c"] >= MAX_DOSARS_PER_USER:
            return None
        try:
            cur = await self.db.execute(
                "INSERT INTO dosars(user_id, number, label, created_at) VALUES(?, ?, ?, ?)",
                (user_id, number, label, utcnow()),
            )
        except aiosqlite.IntegrityError:
            return None
        return cur.lastrowid

    async def list(self, user_id: int) -> list[aiosqlite.Row]:
        return await self.db.fetchall(
            "SELECT * FROM dosars WHERE user_id = ? ORDER BY id", (user_id,)
        )

    async def get(self, user_id: int, dosar_id: int) -> aiosqlite.Row | None:
        return await self.db.fetchone(
            "SELECT * FROM dosars WHERE id = ? AND user_id = ?", (dosar_id, user_id)
        )

    async def delete(self, user_id: int, dosar_id: int) -> None:
        await self.db.execute("DELETE FROM dosars WHERE id = ? AND user_id = ?", (dosar_id, user_id))

    async def set_label(self, user_id: int, dosar_id: int, label: str | None) -> None:
        await self.db.execute(
            "UPDATE dosars SET label = ? WHERE id = ? AND user_id = ?", (label, dosar_id, user_id)
        )

    async def set_submitted(self, user_id: int, dosar_id: int, day: date | None) -> None:
        await self.db.execute(
            "UPDATE dosars SET submitted_on = ? WHERE id = ? AND user_id = ?",
            (day.isoformat() if day else None, dosar_id, user_id),
        )

    async def mentions(self, number: str, active_only: bool = False) -> list[aiosqlite.Row]:
        sql = (
            "SELECT m.line, m.active, m.first_seen_at, d.url, d.title, d.kind "
            "FROM anc_mentions m JOIN anc_documents d ON d.url = m.doc_url "
            "WHERE m.number = ?"
        )
        if active_only:
            sql += " AND m.active = 1"
        sql += " ORDER BY m.first_seen_at DESC, m.id DESC"
        return await self.db.fetchall(sql, (number,))

    async def subscribers(self, number: str) -> list[aiosqlite.Row]:
        return await self.db.fetchall(
            "SELECT user_id, label FROM dosars WHERE number = ?", (number,)
        )

    async def order_subscribers(self) -> list[int]:
        rows = await self.db.fetchall("SELECT id FROM users WHERE notify_orders = 1")
        return [r["id"] for r in rows]

    async def toggle_orders(self, user_id: int) -> bool:
        await self.db.execute(
            "UPDATE users SET notify_orders = 1 - notify_orders WHERE id = ?", (user_id,)
        )
        row = await self.db.fetchone("SELECT notify_orders FROM users WHERE id = ?", (user_id,))
        return bool(row and row["notify_orders"])

    async def notify_orders_enabled(self, user_id: int) -> bool:
        row = await self.db.fetchone("SELECT notify_orders FROM users WHERE id = ?", (user_id,))
        return bool(row and row["notify_orders"])

    async def recent_orders(self, limit: int = 10) -> list[aiosqlite.Row]:
        return await self.db.fetchall(
            "SELECT url, title, first_seen_at FROM anc_documents WHERE kind = 'ordin' "
            "ORDER BY first_seen_at DESC, rowid ASC LIMIT ?",
            (limit,),
        )

    async def index_stats(self) -> dict[str, int]:
        row = await self.db.fetchone(
            "SELECT "
            " (SELECT COUNT(*) FROM anc_documents) AS docs,"
            " (SELECT COUNT(*) FROM anc_documents WHERE kind = 'ordin') AS orders,"
            " (SELECT COUNT(*) FROM anc_documents WHERE sha256 IS NULL) AS pending,"
            " (SELECT COUNT(DISTINCT number) FROM anc_mentions) AS numbers"
        )
        return dict(row) if row else {}

    async def year_progress(self, number: str) -> YearProgress:
        year = number_year(number)
        rows = await self.db.fetchall(
            "SELECT m.number, MIN(m.first_seen_at) AS seen FROM anc_mentions m "
            "JOIN anc_documents d ON d.url = m.doc_url "
            "WHERE d.kind = 'ordin' AND m.number LIKE ? GROUP BY m.number",
            (f"%/RD/{year}",),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
        seqs = [number_seq(r["number"]) for r in rows]
        my_seq = number_seq(number)
        return YearProgress(
            year=year,
            in_orders=len(rows),
            max_seq_in_orders=max(seqs) if seqs else None,
            in_orders_30d=sum(1 for r in rows if r["seen"] >= cutoff),
            position_hint=sum(1 for s in seqs if s < my_seq) if seqs else None,
        )
