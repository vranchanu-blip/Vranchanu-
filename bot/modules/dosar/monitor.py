"""Фоновый мониторинг сайта ANC: скачивает PDF, индексирует номера досаров, находит изменения."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from bot.db import Database, utcnow
from bot.modules.dosar.anc import PdfLink, extract_pdf_links, find_mentions, pdf_to_text

log = logging.getLogger(__name__)


class Fetcher(Protocol):
    async def get_text(self, url: str) -> str: ...
    async def get_bytes(self, url: str) -> bytes: ...


@dataclass(frozen=True)
class NewMention:
    number: str
    doc_url: str
    doc_title: str
    kind: str
    line: str


@dataclass
class CycleResult:
    started_at: str
    bootstrap: bool = False
    pages_ok: int = 0
    pdf_total: int = 0
    pdf_downloaded: int = 0
    pdf_changed: int = 0
    new_mentions: list[NewMention] = field(default_factory=list)
    new_orders: list[PdfLink] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"страниц: {self.pages_ok}, PDF: {self.pdf_total}, скачано: {self.pdf_downloaded}, "
            f"изменилось: {self.pdf_changed}, новых упоминаний: {len(self.new_mentions)}, "
            f"новых приказов: {len(self.new_orders)}, ошибок: {len(self.errors)}"
        )


ResultHandler = Callable[[CycleResult], Awaitable[None]]


class AncMonitor:
    def __init__(
        self,
        db: Database,
        fetcher: Fetcher,
        pages: tuple[str, ...],
        pdf_filter: str = "",
        interval_min: int = 30,
        on_result: ResultHandler | None = None,
        download_delay: float = 0.5,
    ):
        self.db = db
        self.fetcher = fetcher
        self.pages = pages
        self.pdf_filter = pdf_filter
        self.interval_min = interval_min
        self.on_result = on_result
        self.download_delay = download_delay
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def request_refresh(self) -> None:
        self._wake.set()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_cycle()
            except Exception:  # noqa: BLE001 — цикл не должен падать
                log.exception("ANC cycle failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval_min * 60)
            except asyncio.TimeoutError:
                pass

    async def run_cycle(self) -> CycleResult:
        async with self._lock:
            result = await self._cycle()
        await self.db.set_kv("anc_last_cycle_at", result.started_at)
        await self.db.set_kv("anc_last_cycle_summary", result.summary())
        if result.pages_ok:
            await self.db.set_kv("anc_last_success_at", result.started_at)
        log.info("ANC cycle: %s", result.summary())
        if self.on_result:
            await self.on_result(result)
        return result

    async def _cycle(self) -> CycleResult:
        result = CycleResult(started_at=utcnow())
        result.bootstrap = await self.db.get_kv("anc_bootstrapped") is None

        links: list[PdfLink] = []
        seen: set[str] = set()
        for page in self.pages:
            try:
                page_html = await self.fetcher.get_text(page)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{page}: {exc!r}")
                continue
            result.pages_ok += 1
            for link in extract_pdf_links(page_html, page, self.pdf_filter):
                if link.url not in seen:
                    seen.add(link.url)
                    links.append(link)
        result.pdf_total = len(links)

        for link in links:
            try:
                await self._process_link(link, result)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{link.url}: {exc!r}")
                await self.db.execute(
                    "UPDATE anc_documents SET error = ? WHERE url = ?", (repr(exc)[:500], link.url)
                )

        if result.bootstrap:
            if result.pages_ok:
                await self.db.set_kv("anc_bootstrapped", result.started_at)
            # Первичная загрузка архива — не рассылаем уведомления о старых документах.
            result.new_mentions.clear()
            result.new_orders.clear()
        return result

    async def _process_link(self, link: PdfLink, result: CycleResult) -> None:
        row = await self.db.fetchone("SELECT sha256 FROM anc_documents WHERE url = ?", (link.url,))
        is_new_doc = row is None
        if is_new_doc:
            await self.db.execute(
                "INSERT INTO anc_documents(url, title, kind, source_page, first_seen_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (link.url, link.title, link.kind, link.source_page, utcnow()),
            )
        elif link.kind == "ordin" and row["sha256"]:
            # Опубликованный приказ не меняется — повторно не скачиваем.
            return

        data = await self.fetcher.get_bytes(link.url)
        result.pdf_downloaded += 1
        if self.download_delay:
            await asyncio.sleep(self.download_delay)
        sha = hashlib.sha256(data).hexdigest()
        if row is not None and row["sha256"] == sha:
            await self.db.execute(
                "UPDATE anc_documents SET fetched_at = ?, error = NULL WHERE url = ?",
                (utcnow(), link.url),
            )
            return

        text = await asyncio.to_thread(pdf_to_text, data)
        added = await self._apply_mentions(link.url, find_mentions(text))
        await self.db.execute(
            "UPDATE anc_documents SET sha256 = ?, fetched_at = ?, title = ?, error = NULL "
            "WHERE url = ?",
            (sha, utcnow(), link.title, link.url),
        )
        result.pdf_changed += 1
        if is_new_doc and link.kind == "ordin":
            result.new_orders.append(link)
        result.new_mentions.extend(
            NewMention(number=n, doc_url=link.url, doc_title=link.title, kind=link.kind, line=line)
            for n, line in sorted(added)
        )

    async def _apply_mentions(self, url: str, mentions: dict[str, set[str]]) -> set[tuple[str, str]]:
        """Синхронизирует строки документа с БД. Возвращает впервые появившиеся (номер, строка)."""
        rows = await self.db.fetchall(
            "SELECT number, line, active FROM anc_mentions WHERE doc_url = ?", (url,)
        )
        existing = {(r["number"], r["line"]): r["active"] for r in rows}
        current = {(n, line) for n, lines in mentions.items() for line in lines}

        added = current - existing.keys()
        reactivated = {k for k in current if existing.get(k) == 0}
        gone = {k for k, active in existing.items() if active and k not in current}

        conn = self.db.conn
        assert conn
        now = utcnow()
        await conn.executemany(
            "INSERT INTO anc_mentions(doc_url, number, line, first_seen_at) VALUES(?, ?, ?, ?)",
            [(url, n, line, now) for n, line in added],
        )
        await conn.executemany(
            "UPDATE anc_mentions SET active = 1 WHERE doc_url = ? AND number = ? AND line = ?",
            [(url, n, line) for n, line in reactivated],
        )
        await conn.executemany(
            "UPDATE anc_mentions SET active = 0 WHERE doc_url = ? AND number = ? AND line = ?",
            [(url, n, line) for n, line in gone],
        )
        await conn.commit()
        return added
