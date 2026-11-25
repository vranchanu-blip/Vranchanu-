"""Тонкая обёртка над SQLite. Каждый модуль экосистемы добавляет свои таблицы в SCHEMA."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,           -- Telegram user id
    username    TEXT,
    first_name  TEXT,
    created_at  TEXT NOT NULL
);

-- ===== Модуль «Погода» =====
CREATE TABLE IF NOT EXISTS places (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    tz          TEXT,                            -- часовой пояс места (из прогноза)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_places_user ON places(user_id);

-- ===== Модуль «Портфель» =====
CREATE TABLE IF NOT EXISTS ibkr_accounts (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token       TEXT NOT NULL,                   -- токен Flex Web Service (только чтение отчётов)
    query_id    TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Колонки, добавленные позже: (таблица, колонка, тип). Добавляются, если их ещё нет.
MIGRATIONS = [
    ("users", "lat", "REAL"),
    ("users", "lon", "REAL"),
    ("users", "place", "TEXT"),
    ("users", "active_place_id", "INTEGER"),
    ("users", "morning_time", "TEXT"),          # 'HH:MM' или NULL = выкл
    ("users", "morning_sent", "TEXT"),          # дата последнего утреннего прогноза
    ("users", "alerts", "INTEGER NOT NULL DEFAULT 0"),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute("PRAGMA journal_mode = WAL")
        await self.conn.executescript(SCHEMA)
        for table, column, kind in MIGRATIONS:
            async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            if column not in existing:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Cursor:
        """Выполнить запрос и закоммитить. Возвращает курсор (rowcount, lastrowid)."""
        assert self.conn
        cur = await self.conn.execute(sql, tuple(params))
        await self.conn.commit()
        return cur

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        assert self.conn
        async with self.conn.execute(sql, tuple(params)) as cur:
            return list(await cur.fetchall())

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        assert self.conn
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def get_kv(self, key: str) -> str | None:
        row = await self.fetchone("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_kv(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        await self.execute(
            "INSERT INTO users(id, username, first_name, created_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET username = excluded.username, "
            "first_name = excluded.first_name",
            (user_id, username, first_name, utcnow()),
        )
