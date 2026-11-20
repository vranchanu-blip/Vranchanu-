"""Разбор выгрузки Booking.com: Extranet → Reservations (Бронирования) → Download (CSV/XLSX)."""
from decimal import Decimal
from pathlib import Path

from .models import Reservation
from .parsing import parse_amount, parse_date, pick, read_table

C = {
    "code": ["Book number", "Reservation number", "Număr rezervare", "Номер бронирования"],
    "guest": ["Guest name(s)", "Guest name", "Booked by", "Nume oaspete", "Имя гостя"],
    "booked_by": ["Booked by", "Rezervat de", "Кем забронировано"],
    "checkin": ["Check-in", "Arrival", "Sosire", "Заезд"],
    "checkout": ["Check-out", "Departure", "Plecare", "Отъезд"],
    "status": ["Status", "Stare", "Статус"],
    "price": ["Price", "Total price", "Preț", "Цена"],
    "commission": ["Commission amount", "Valoare comision", "Сумма комиссии"],
    "commission_pct": ["Commission %", "Comision %", "Комиссия %"],
    "nights": ["Duration (nights)", "Nights", "Durată (nopți)", "Ночей"],
    "country": ["Booker country", "Țara clientului", "Страна гостя"],
    "unit": ["Unit type", "Tip unitate", "Тип номера"],
    "currency": ["Currency", "Monedă", "Валюта"],
}


def load(path: Path, default_currency: str = "EUR", include_cancelled_with_fee: bool = True) -> list[Reservation]:
    result = []
    for row in read_table(path):
        code = str(pick(row, C["code"], "")).strip()
        if not code:
            continue
        code = code[:-2] if code.endswith(".0") else code  # Excel превращает номер в число
        status = str(pick(row, C["status"], "ok")).strip().lower()
        price, cur = parse_amount(pick(row, C["price"]))
        commission, cur2 = parse_amount(pick(row, C["commission"]))
        if commission == 0:
            pct, _ = parse_amount(pick(row, C["commission_pct"]))
            commission = (price * pct / 100).quantize(Decimal("0.01"))
        currency = (pick(row, C["currency"]) or cur or cur2 or default_currency).strip().upper()

        cancelled = "cancel" in status or "anulat" in status or "отмен" in status
        if cancelled and not (include_cancelled_with_fee and price > 0):
            continue

        checkin = parse_date(pick(row, C["checkin"]))
        checkout = parse_date(pick(row, C["checkout"]))
        nights = pick(row, C["nights"])
        nights = int(Decimal(str(nights))) if nights not in (None, "") else (
            (checkout - checkin).days if checkin and checkout else 0)

        result.append(Reservation(
            platform="Booking", code=code,
            guest=str(pick(row, C["guest"], "")).strip(),
            checkin=checkin, checkout=checkout, nights=nights,
            gross=price, commission=commission, net=price - commission,
            currency=currency, status=status or "ok",
            listing=str(pick(row, C["unit"], "")).strip(),
            country=str(pick(row, C["country"], "")).strip().upper(),
        ))
    return result
