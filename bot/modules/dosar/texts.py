"""Форматирование сообщений модуля «Досар»."""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

import aiosqlite

from bot.modules.dosar.service import YearProgress

TZ = ZoneInfo("Europe/Bucharest")

KIND_TITLES = {"ordin": "Приказ (Ordin)", "stadiu": "Stadiu dosar", "other": "Документ ANC"}


def fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d.%m.%Y %H:%M")


def dosar_name(row: aiosqlite.Row) -> str:
    label = f" · {escape(row['label'])}" if row["label"] else ""
    return f"<code>{escape(row['number'])}</code>{label}"


def waiting_line(submitted_on: str | None) -> str:
    if not submitted_on:
        return "📅 Дата подачи: не указана"
    day = date.fromisoformat(submitted_on)
    days = (date.today() - day).days
    years, rest = divmod(days, 365)
    months = rest // 30
    parts = []
    if years:
        parts.append(f"{years} г.")
    if months:
        parts.append(f"{months} мес.")
    human = " ".join(parts) or f"{days} дн."
    return f"📅 Подан: {day:%d.%m.%Y} — ждём {days} дн. (~{human})"


def mention_block(rows: list[aiosqlite.Row], limit: int = 5) -> str:
    out = []
    unique, seen = [], set()
    for r in rows:  # одна и та же строка из разных версий списка — показываем один раз
        if r["line"] not in seen:
            seen.add(r["line"])
            unique.append(r)
    rows = unique
    for r in rows[:limit]:
        mark = "" if r["active"] else " <i>(строка больше не в документе)</i>"
        out.append(
            f"• <a href=\"{escape(r['url'], quote=True)}\">{escape(r['title'] or 'PDF')}</a> "
            f"— {KIND_TITLES.get(r['kind'], r['kind'])}{mark}\n"
            f"  <code>{escape(r['line'])}</code>\n"
            f"  <i>замечено ботом: {fmt_dt(r['first_seen_at'])}</i>"
        )
    if len(rows) > limit:
        out.append(f"…и ещё {len(rows) - limit}")
    return "\n".join(out)


def status_summary(active: list[aiosqlite.Row]) -> str:
    orders = [r for r in active if r["kind"] == "ordin"]
    if orders:
        return "🎉 <b>Номер найден в приказе ANC!</b>"
    if active:
        return "🟡 Номер есть в списках ANC, в приказах пока не найден."
    return "⏳ Пока не найден ни в одном документе ANC."


def progress_block(p: YearProgress) -> str:
    if not p.in_orders:
        return f"📊 Досаров {p.year} года в приказах пока не найдено."
    lines = [
        f"📊 <b>Досары {p.year} года в приказах</b>",
        f"Всего найдено номеров: {p.in_orders}",
        f"Максимальный номер в приказах: {p.max_seq_in_orders}",
        f"Из них с номером меньше вашего: {p.position_hint}",
        f"Новых за 30 дней (по наблюдению бота): {p.in_orders_30d}",
        "<i>ANC рассматривает досары не строго по порядку, это ориентир, а не прогноз.</i>",
    ]
    return "\n".join(lines)
