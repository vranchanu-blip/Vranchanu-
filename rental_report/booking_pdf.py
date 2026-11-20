"""Booking.com Extranet → Финансы → «Отчёт о бронированиях» (Reservation statement),
сохранённый как PDF («Распечатать эту страницу» → «Сохранить как PDF»)."""
import re
from decimal import Decimal
from pathlib import Path

from .models import Reservation
from .parsing import parse_amount, parse_date

DATE = r"\d{1,2}\s+[^\d\s]+\.?\s+\d{4}"
MONEY = r"[\d\s.,]+?\s*(?:lei|RON|€|EUR)"
ROW = re.compile(rf"^(?P<guest>.+?)\s+(?P<ci>{DATE})\s+(?P<co>{DATE})\s+(?P<status>.+?)\s+"
                 rf"(?P<amount>{MONEY})\s+(?P<commission>{MONEY})\s*$")
TOTAL = re.compile(r"(?:Общая сумма бронирования|Total reservation amount|Valoarea totală a rezervărilor)"
                   rf"\s*:?\s*(?P<amount>{MONEY})", re.I)
CODE = re.compile(r"^\d{9,11}$")


def extract_text(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)


def is_booking_statement(text: str) -> bool:
    return "booking.com" in text.lower() and bool(ROW.search(text.replace("\xa0", " ")) or
                                                  any(ROW.match(l) for l in text.replace("\xa0", " ").splitlines()))


def parse_text(text: str) -> tuple[list[Reservation], Decimal | None]:
    text = text.replace("\xa0", " ")
    rows, codes, seen = [], [], set()
    for line in (l.strip() for l in text.splitlines()):
        m = ROW.match(line)
        if m:
            key = (m["guest"], m["ci"], m["co"])
            if key in seen:      # таблица в PDF печатается дважды
                continue
            seen.add(key)
            amount, cur = parse_amount(m["amount"])
            commission, _ = parse_amount(m["commission"])
            ci, co = parse_date(m["ci"]), parse_date(m["co"])
            currency = "EUR" if (cur == "EUR" or "€" in m["amount"]) else "RON"
            rows.append(Reservation(platform="Booking", code="", guest=m["guest"].strip(),
                                    checkin=ci, checkout=co, nights=(co - ci).days,
                                    gross=amount, commission=commission, net=amount - commission,
                                    currency=currency, status=m["status"].strip()))
        elif CODE.match(line) and line not in codes:
            codes.append(line)
    if len(codes) == len(rows):  # номера бронирований идут отдельным столбцом в том же порядке
        for r, c in zip(rows, codes):
            r.code = c
    t = TOTAL.search(text)
    return rows, (parse_amount(t["amount"])[0] if t else None)


def load(path: Path) -> tuple[list[Reservation], Decimal | None]:
    return parse_text(extract_text(path))
