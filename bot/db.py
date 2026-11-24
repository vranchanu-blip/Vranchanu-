"""Тонкая обёртка над SQLite. Каждый модуль экосистемы добавляет свои таблицы в SCHEMA."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,           -- Telegram user id
    username      TEXT,
    first_name    TEXT,
    notify_orders INTEGER NOT NULL DEFAULT 1,    -- уведомлять о новых приказах ANC
    created_at    TEXT NOT NULL
);

-- ===== Модуль «Досар» =====
CREATE TABLE IF NOT EXISTS dosars (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    number       TEXT NOT NULL,                  -- нормализовано: 12345/RD/2021
    label        TEXT,
    submitted_on TEXT,                           -- ISO дата подачи (необязательно)
    created_at   TEXT NOT NULL,
    UNIQUE (user_id, number)
);
CREATE INDEX IF NOT EXISTS idx_dosars_number ON dosars(number);

-- PDF-документы, найденные на сайте ANC
CREATE TABLE IF NOT EXISTS anc_documents (
    url           TEXT PRIMARY KEY,
    title         TEXT,
    kind          TEXT NOT NULL,                 -- 'ordin' | 'stadiu' | 'other'
    source_page   TEXT,
    sha256        TEXT,
    first_seen_at TEXT NOT NULL,
    fetched_at    TEXT,
    error         TEXT
);

-- Строки PDF, в которых встречается номер досара
CREATE TABLE IF NOT EXISTS anc_mentions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_url       TEXT NOT NULL REFERENCES anc_documents(url) ON DELETE CASCADE,
    number        TEXT NOT NULL,
    line          TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,    -- 0 = строки больше нет в документе
    first_seen_at TEXT NOT NULL,
    UNIQUE (doc_url, number, line)
);
CREATE INDEX IF NOT EXISTS idx_mentions_number ON anc_mentions(number);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


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
