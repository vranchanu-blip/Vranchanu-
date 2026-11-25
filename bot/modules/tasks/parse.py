"""Разбор напоминаний на русском: «завтра в 9 позвонить в банк», «через 30 минут…»,
«каждый понедельник в 8:00 вынести мусор», «25.12 в 10 поздравить маму»."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
WEEKDAY_STEMS = ["понедельн", "вторн", "сред", "четверг", "пятниц", "суббот", "воскрес"]
WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEKDAY_EVERY = ["каждый понедельник", "каждый вторник", "каждую среду", "каждый четверг",
                 "каждую пятницу", "каждую субботу", "каждое воскресенье"]
WD = r"(понедельник\w*|вторник\w*|сред[уаы]\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)"


@dataclass(frozen=True)
class Parsed:
    body: str
    due: datetime | None          # локальное время (с tzinfo), None — время не понял
    repeat: str | None = None     # daily | weekdays | weekly:<0-6> | monthly:<1-31>


def weekday_index(word: str) -> int:
    return next(i for i, stem in enumerate(WEEKDAY_STEMS) if word.startswith(stem))


def describe_repeat(repeat: str | None) -> str:
    if not repeat:
        return ""
    if repeat == "daily":
        return "каждый день"
    if repeat == "weekdays":
        return "по будням"
    kind, _, arg = repeat.partition(":")
    if kind == "weekly":
        return WEEKDAY_EVERY[int(arg)]
    if kind == "monthly":
        return f"каждый месяц {arg}-го"
    return repeat


def month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def next_due(prev: datetime, repeat: str, now: datetime) -> datetime:
    """Следующее срабатывание повторяющегося напоминания (строго позже now)."""
    due = prev
    while due <= now:
        if repeat == "daily":
            due += timedelta(days=1)
        elif repeat == "weekdays":
            due += timedelta(days=1)
            while due.weekday() >= 5:
                due += timedelta(days=1)
        elif repeat.startswith("weekly"):
            due += timedelta(days=7)
        elif repeat.startswith("monthly"):
            day = int(repeat.split(":")[1])
            y, m = (due.year + 1, 1) if due.month == 12 else (due.year, due.month + 1)
            d = month_day(y, m, day)
            due = due.replace(year=d.year, month=d.month, day=d.day)
        else:
            raise ValueError(repeat)
    return due


class _Text:
    """Строка, из которой вырезаем распознанные куски; остаток — текст напоминания."""

    def __init__(self, text: str):
        self.orig = text
        self.low = text.lower().replace("ё", "е")
        self.cut: list[tuple[int, int]] = []

    def take(self, pattern: str) -> re.Match | None:
        for m in re.finditer(pattern, self.low):
            if any(a < m.end() and m.start() < b for a, b in self.cut):
                continue
            self.cut.append(m.span())
            return m
        return None

    def rest(self) -> str:
        chars = list(self.orig)
        for a, b in self.cut:
            for i in range(a, b):
                chars[i] = " "
        body = re.sub(r"\s+", " ", "".join(chars)).strip(" ,.;:-–—")
        body = re.sub(r"^(что|чтобы|о том,? что|про|о)\s+", "", body, flags=re.IGNORECASE).strip(" ,.;:-–—")
        return body[:1].upper() + body[1:] if body else "Напоминание"


def parse(text: str, now: datetime) -> Parsed:
    t = _Text(text)
    t.take(r"^\s*(напомни(те|ть)?|напоминание)\b\s*(мне\s*)?,?")

    # ---- повтор ----
    repeat = None
    default_hour = 9
    if m := t.take(r"\b(каждый день|ежедневно|каждое утро|каждый вечер)\b"):
        repeat = "daily"
        default_hour = 20 if "вечер" in m.group(1) else 9
    elif t.take(r"\b(по будням|каждый будний день|в будни)\b"):
        repeat = "weekdays"
    elif m := t.take(rf"\b(каждый|каждую|каждое|по)\s+{WD}"):
        repeat = f"weekly:{weekday_index(m.group(2))}"
    elif t.take(r"\bкаждую неделю\b"):
        repeat = f"weekly:{now.weekday()}"
    elif m := t.take(r"\bкаждое\s+(\d{1,2})(?:-?е|-?го)?\s+число\b") or \
            t.take(r"\bкаждый месяц(?:\s+(\d{1,2})(?:-?го|-?е|\s*числа)?)?"):
        repeat = f"monthly:{int(m.group(1)) if m.group(1) else now.day}"

    # ---- через N ----
    if t.take(r"\bчерез\s+полчаса\b"):
        return Parsed(t.rest(), now + timedelta(minutes=30))
    if m := t.take(r"\bчерез\s+(?:(\d+|полтора)\s*)?(минут\w*|мин\b|час\w*|день|дня|дней|сутки|недел\w*)"):
        n = 1.5 if m.group(1) == "полтора" else int(m.group(1) or 1)
        unit = m.group(2)
        delta = (timedelta(minutes=n) if unit.startswith("мин") else
                 timedelta(hours=n) if unit.startswith("час") else
                 timedelta(weeks=n) if unit.startswith("недел") else timedelta(days=n))
        return Parsed(t.rest(), now + delta)

    # ---- время ----
    hour = minute = None
    if m := t.take(r"\bв\s+(\d{1,2})(?:[:.](\d{2}))?(?:\s*(утра|дня|вечера|ночи))?(?:\s*час\w*)?(?!\s*[./]\d)\b"):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) in ("дня", "вечера") and hour < 12:
            hour += 12
        if m.group(3) == "ночи" and hour == 12:
            hour = 0
    elif m := t.take(r"\b(\d{1,2}):(\d{2})\b"):
        hour, minute = int(m.group(1)), int(m.group(2))
    elif t.take(r"\bв полдень\b"):
        hour, minute = 12, 0
    elif t.take(r"\bв полночь\b"):
        hour, minute = 0, 0
    elif m := t.take(r"\b(утром|днем|вечером|ночью)\b"):
        hour, minute = {"утром": 9, "днем": 13, "вечером": 20, "ночью": 23}[m.group(1)], 0
    if hour is not None and not (0 <= hour <= 23 and 0 <= minute <= 59):
        return Parsed(t.rest(), None)

    # ---- дата ----
    day: date | None = None
    by_weekday = False
    today = now.date()
    if m := t.take(r"\b(сегодня|послезавтра|завтра)\b"):
        day = today + timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[m.group(1)])
    elif m := t.take(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b"):
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = (int(y) + 2000 if y and len(y) == 2 else int(y)) if y else today.year
        try:
            day = date(year, mo, d)
        except ValueError:
            return Parsed(t.rest(), None)
        if not y and day < today:
            day = day.replace(year=year + 1)
    elif m := t.take(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?"):
        d, mo = int(m.group(1)), MONTHS.index(m.group(2)) + 1
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            day = date(year, mo, d)
        except ValueError:
            return Parsed(t.rest(), None)
        if not m.group(3) and day < today:
            day = day.replace(year=year + 1)
    elif repeat is None and (m := t.take(rf"\b(?:в|во)\s+{WD}")):
        wd = weekday_index(m.group(1))
        day = today + timedelta(days=(wd - today.weekday()) % 7)
        by_weekday = True

    if hour is None and day is None and repeat is None:
        return Parsed(t.rest(), None)

    h, mi = (hour, minute) if hour is not None else (default_hour, 0)

    def at(d: date) -> datetime:
        return now.replace(year=d.year, month=d.month, day=d.day, hour=h, minute=mi, second=0, microsecond=0)

    if repeat:
        if repeat.startswith("weekly"):
            wd = int(repeat.split(":")[1])
            first = at(today + timedelta(days=(wd - today.weekday()) % 7))
        elif repeat.startswith("monthly"):
            first = at(month_day(today.year, today.month, int(repeat.split(":")[1])))
        else:
            first = at(today)
            while repeat == "weekdays" and first.weekday() >= 5:
                first += timedelta(days=1)
        return Parsed(t.rest(), first if first > now else next_due(first, repeat, now), repeat)

    if day is None:
        due = at(today)
        return Parsed(t.rest(), due if due > now else due + timedelta(days=1))
    due = at(day)
    if due <= now and day == today and hour is None:
        due = now + timedelta(hours=1)  # «сегодня позвонить» без времени — через час
    elif due <= now and by_weekday:
        due += timedelta(days=7)        # «в среду», а среда уже прошла сегодня
    return Parsed(t.rest(), due)
