"""Разбор выгрузки Airbnb: Menu → Earnings (Доходы) → Transaction history → Export CSV."""
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from .models import Reservation
from .parsing import parse_amount, parse_date, pick, read_table

C = {
    "type": ["Type", "Tip", "Тип"],
    "code": ["Confirmation code", "Confirmation Code", "Cod de confirmare", "Код подтверждения"],
    "date": ["Date", "Data", "Дата"],
    "start": ["Start date", "Start Date", "Data începerii", "Дата начала"],
    "end": ["End date", "End Date", "Data încheierii", "Дата окончания"],
    "nights": ["Nights", "Nopți", "Ночи"],
    "guest": ["Guest", "Oaspete", "Гость"],
    "listing": ["Listing", "Anunț", "Объявление"],
    "currency": ["Currency", "Monedă", "Валюта"],
    "amount": ["Amount", "Sumă", "Сумма"],
    "service_fee": ["Service fee", "Taxa de serviciu", "Сервисный сбор"],
    "gross": ["Gross earnings", "Câștiguri brute", "Валовой доход"],
}

# Строки, относящиеся к конкретному бронированию (Payout — это перевод на счёт, не доход)
BOOKING_TYPES = {"reservation", "adjustment", "resolution adjustment", "cancellation fee",
                 "resolution payout", "rezervare", "ajustare", "бронирование", "корректировка"}
PAYOUT_TYPES = {"payout", "plată", "выплата"}


def load(path: Path) -> list[Reservation]:
    rows = read_table(path)
    by_code: "OrderedDict[str, Reservation]" = OrderedDict()
    for row in rows:
        rtype = str(pick(row, C["type"], "")).strip().lower()
        if rtype in PAYOUT_TYPES:
            continue
        code = str(pick(row, C["code"], "")).strip()
        if not code or (rtype and rtype not in BOOKING_TYPES):
            continue
        amount, cur1 = parse_amount(pick(row, C["amount"]))
        fee, _ = parse_amount(pick(row, C["service_fee"]))
        gross, _ = parse_amount(pick(row, C["gross"]))
        currency = (pick(row, C["currency"]) or cur1 or "EUR").strip().upper()
        if gross == 0 and amount:
            gross = amount + abs(fee)
        start = parse_date(pick(row, C["start"]))
        nights = int(Decimal(str(pick(row, C["nights"], 0) or 0)))
        end = parse_date(pick(row, C["end"])) or (start + timedelta(days=nights) if start else None)

        r = by_code.get(code)
        if r is None:
            by_code[code] = Reservation(
                platform="Airbnb", code=code,
                guest=str(pick(row, C["guest"], "")).strip(),
                checkin=start, checkout=end, nights=nights,
                gross=gross, commission=abs(fee), net=amount, currency=currency,
                listing=str(pick(row, C["listing"], "")).strip(),
                payout_date=parse_date(pick(row, C["date"])),
                status="ok" if rtype in ("reservation", "rezervare", "бронирование", "") else rtype,
            )
        else:  # корректировки по тому же бронированию суммируем
            r.gross += gross
            r.commission += abs(fee)
            r.net += amount
            r.checkin = r.checkin or start
            r.checkout = r.checkout or end
            r.nights = r.nights or nights
            if r.status != "ok" and rtype in ("reservation", "rezervare", "бронирование"):
                r.status = "ok"
    return list(by_code.values())
