"""Разбор выгрузки Airbnb: Доходы (Earnings) → Выплаченные (Completed payouts) → Экспорт CSV.

Поддерживаются старый формат (с колонками Type / Confirmation code / Service fee)
и новый (Дата, Дата начала, Дата окончания, Ночи, Гость, Сумма, Выплачено,
Заработок до вычета налогов ...) — на русском, английском и румынском.
"""
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from .models import Reservation
from .parsing import parse_amount, parse_date, pick, read_table

C = {
    "type": ["Type", "Tip", "Тип"],
    "code": ["Confirmation code", "Confirmation Code", "Cod de confirmare", "Код подтверждения"],
    "date": ["Date", "Data", "Dată", "Дата"],
    "start": ["Start date", "Start Date", "Data de început", "Data începerii", "Дата начала"],
    "end": ["End date", "End Date", "Data de sfârșit", "Data încheierii", "Дата окончания"],
    "nights": ["Nights", "Nopți", "Ночи"],
    "guest": ["Guest", "Oaspete", "Гость"],
    "listing": ["Listing", "Anunț", "Объявление"],
    "currency": ["Currency", "Monedă", "Валюта"],
    "amount": ["Amount", "Sumă", "Сумма"],
    "service_fee": ["Service fee", "Taxa de serviciu", "Сервисный сбор"],
    "gross": ["Gross earnings", "Câștiguri brute", "Câștiguri înainte de taxe",
              "Заработок до вычета налогов", "Валовой доход"],
}

BOOKING_TYPES = {"reservation", "adjustment", "resolution adjustment", "cancellation fee",
                 "resolution payout", "rezervare", "ajustare", "бронирование", "корректировка"}
PAYOUT_TYPES = {"payout", "plată", "выплата"}
MAIN_TYPES = {"reservation", "rezervare", "бронирование", ""}


def load(path: Path, default_currency: str = "RON") -> list[Reservation]:
    rows = read_table(path)
    by_key: "OrderedDict[tuple, Reservation]" = OrderedDict()
    for row in rows:
        rtype = str(pick(row, C["type"], "")).strip().lower()
        guest = str(pick(row, C["guest"], "")).strip()
        if rtype in PAYOUT_TYPES or not guest:   # строка «выплата на счёт», а не бронь
            continue
        if rtype and rtype not in BOOKING_TYPES:
            continue
        code = str(pick(row, C["code"], "")).strip()
        amount, cur1 = parse_amount(pick(row, C["amount"]))
        fee, _ = parse_amount(pick(row, C["service_fee"]))
        gross, _ = parse_amount(pick(row, C["gross"]))
        if gross == 0:
            gross = amount + abs(fee)
        commission = abs(fee) if fee else gross - amount   # комиссия Airbnb (обычно 3%)
        currency = (pick(row, C["currency"]) or cur1 or default_currency).strip().upper()
        start = parse_date(pick(row, C["start"]))
        nights = int(Decimal(str(pick(row, C["nights"], 0) or 0)))
        end = parse_date(pick(row, C["end"])) or (start + timedelta(days=nights) if start else None)

        r = Reservation(platform="Airbnb", code=code, guest=guest, checkin=start, checkout=end,
                        nights=nights, gross=gross, commission=commission, net=amount,
                        currency=currency, listing=str(pick(row, C["listing"], "")).strip(),
                        payout_date=parse_date(pick(row, C["date"])),
                        status="ok" if rtype in MAIN_TYPES else rtype)
        old = by_key.get(r.key)
        if old is None:
            by_key[r.key] = r
        else:  # корректировки по тому же бронированию суммируем
            old.gross += gross
            old.commission += commission
            old.net += amount
            old.checkin = old.checkin or start
            old.checkout = old.checkout or end
            old.nights = old.nights or nights
            if old.status != "ok" and rtype in MAIN_TYPES:
                old.status = "ok"
    return list(by_key.values())
