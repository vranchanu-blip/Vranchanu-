"""Запуск: python -m rental_report [--month 2026-10] [--config config.yaml] [--send]"""
import argparse
import json
import shutil
import sys
from calendar import monthrange
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from . import airbnb, booking, booking_pdf, excel_report, saga_xml
from .bnr import BnrRates
from .parsing import read_table

LUNI = ["", "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie",
        "august", "septembrie", "octombrie", "noiembrie", "decembrie"]


def previous_month(today: date) -> str:
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{y}-{m:02d}"


def detect_platform(path: Path) -> str | None:
    if path.suffix.lower() == ".pdf":
        try:
            return "booking_pdf" if booking_pdf.is_booking_statement(booking_pdf.extract_text(path)) else None
        except Exception as e:  # noqa: BLE001
            print(f"  ! {path.name}: {e}")
            return None
    try:
        rows = read_table(path)
    except Exception as e:  # noqa: BLE001
        print(f"  ! {path.name}: {e}")
        return None
    if not rows:
        return None
    keys = {k.lower() for k in rows[0]}
    if keys & {"confirmation code", "cod de confirmare", "код подтверждения"} or (
            keys & {"guest", "oaspete", "гость"} and keys & {"start date", "data de început", "дата начала"}):
        return "airbnb"
    if keys & {"book number", "reservation number", "număr rezervare", "номер бронирования"}:
        return "booking"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Отчёт Booking + Airbnb для бухгалтера (Saga)")
    ap.add_argument("--month", help="Месяц ГГГГ-ММ (по умолчанию — прошлый месяц)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--send", action="store_true", help="Отправить отчёт бухгалтеру по e-mail")
    ap.add_argument("--rates-file", help="Локальный XML BNR (если нет интернета)")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Нет файла настроек {cfg_path}. Скопируйте config.example.yaml → config.yaml")
        return 2
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    base = cfg_path.resolve().parent

    month = args.month or previous_month(date.today())
    y, m = map(int, month.split("-"))
    start, end = date(y, m, 1), date(y, m, monthrange(y, m)[1])
    inbox = base / cfg.get("input_dir", "inbox")
    out_dir = base / cfg.get("output_dir", "reports") / month
    inbox.mkdir(parents=True, exist_ok=True)

    print(f"Отчёт за {LUNI[m]} {y}. Папка с выгрузками: {inbox}")
    reservations, seen, found, notes = [], set(), {"airbnb": 0, "booking": 0}, []
    files = sorted(p for p in inbox.rglob("*") if p.suffix.lower() in (".csv", ".xlsx", ".xls", ".pdf"))
    for p in files:
        kind = detect_platform(p)
        if kind is None:
            print(f"  - пропущен (не распознан): {p.name}")
            continue
        found["airbnb" if kind == "airbnb" else "booking"] += 1
        if kind == "airbnb":
            items = airbnb.load(p, cfg.get("airbnb_currency", "RON"))
        elif kind == "booking_pdf":
            items, total = booking_pdf.load(p)
            s = sum(r.gross for r in items)
            if total is not None and abs(total - s) > Decimal("0.05"):
                notes.append(f"{p.name}: сумма строк {s:,.2f}, а Booking пишет «итого» {total:,.2f} "
                             f"(разница {s - total:,.2f}) — проверьте в Extranet")
        else:
            items = booking.load(p, cfg.get("booking_currency", "EUR"),
                                 cfg.get("include_cancelled_with_fee", True))
        print(f"  + {p.name}: {kind}, {len(items)} записей")
        for r in items:
            if r.key not in seen:
                seen.add(r.key)
                reservations.append(r)

    warnings = [f"Не найден файл {name.capitalize()} в папке {inbox.name}"
                for name, n in found.items() if n == 0 and cfg.get(f"expect_{name}", True)]

    # к какому месяцу относим бронь. platform = как в ежемесячных документах площадок:
    # Airbnb — по дате выплаты, Booking — по дате выезда
    basis = cfg.get("date_basis", "platform")

    def ref_date(r):
        if basis == "checkin":
            return r.checkin
        if basis == "payout" or (basis == "platform" and r.platform == "Airbnb"):
            return r.payout_date or r.checkout
        return r.checkout
    rows = [r for r in reservations if ref_date(r) and start <= ref_date(r) <= end]
    rows.sort(key=lambda r: (ref_date(r), r.platform, r.guest))

    rates = BnrRates(base / ".cache", cfg.get("bnr_mode", "same_day"), args.rates_file)
    for r in rows:
        if r.currency != "RON":
            r.rate, r.rate_date = rates.get(r.currency, min(ref_date(r), date.today()))

    # Сквозная нумерация документов для Saga (повторный запуск месяца не сдвигает номера)
    state_path = base / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    months = state.setdefault("months", {})
    first_no = months.get(month, {}).get("first_number") or state.get(
        "next_number", cfg.get("saga", {}).get("next_number", 1))
    cfg.setdefault("saga", {})["next_number"] = first_no

    title = f"Raport cazare {LUNI[m]} {y} — Booking.com + Airbnb"
    company = cfg.get("company", {}).get("name", "")
    xlsx = excel_report.write(rows, out_dir / f"Raport_cazare_{month}.xlsx", title, company)
    outputs = [xlsx]
    if cfg.get("saga", {}).get("enabled", True) and rows:
        xml, next_no = saga_xml.write(rows, out_dir / f"SAGA_import_{month}.xml", cfg, end)
        outputs.append(xml)
        months[month] = {"first_number": first_no, "last_number": next_no - 1}
        state["next_number"] = max(state.get("next_number", 1), next_no)
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))

    total = sum(r.gross_ron for r in rows)
    summary = (f"{title}\nRezervări: {len(rows)}  |  Total brut: {total:,.2f} RON\n"
               + "".join(f"ATENȚIE: {w}\n" for w in warnings))
    for n in notes:
        print(f"  ⚠ {n}")
    print("\n" + summary)
    for o in outputs:
        print(f"  → {o}")

    if cfg.get("archive_inputs", False) and files:
        arch = base / cfg.get("output_dir", "reports") / month / "surse"
        arch.mkdir(parents=True, exist_ok=True)
        for p in files:
            shutil.copy2(p, arch / p.name)

    if args.send or cfg.get("email", {}).get("auto_send", False):
        if warnings and not cfg.get("email", {}).get("send_with_warnings", False):
            print("Письмо НЕ отправлено из-за предупреждений выше.")
            return 1
        from .mailer import send
        body = cfg.get("email", {}).get("body", "Bună ziua,\n\nVă trimit raportul de cazare pentru {luna}.\n\n{rezumat}\nMulțumesc!")
        send(cfg, f"Raport cazare {LUNI[m]} {y}",
             body.format(luna=f"{LUNI[m]} {y}", rezumat=summary), outputs)
        print("Письмо бухгалтеру отправлено.")
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
