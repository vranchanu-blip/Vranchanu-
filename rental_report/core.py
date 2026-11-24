"""Основная логика: файлы выгрузок → Excel-отчёт + XML для SAGA."""
import json
import shutil
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable

from . import airbnb, booking, booking_pdf, excel_report, saga_xml
from .bnr import BnrRates
from .models import Reservation
from .parsing import read_table

LUNI = ["", "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie",
        "august", "septembrie", "octombrie", "noiembrie", "decembrie"]
SUPPORTED = (".csv", ".xlsx", ".xls", ".pdf")
NAMES = {"airbnb": "Airbnb", "booking": "Booking", "booking_pdf": "Booking"}


def previous_month(today: date) -> str:
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{y}-{m:02d}"


def detect_platform(path: Path) -> str | None:
    path = Path(path)
    try:
        if path.suffix.lower() == ".pdf":
            return "booking_pdf" if booking_pdf.is_booking_statement(booking_pdf.extract_text(path)) else None
        rows = read_table(path)
    except Exception:  # noqa: BLE001
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


@dataclass
class Result:
    month: str
    rows: list[Reservation] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)   # серьёзные (нет файла и т.п.)
    notes: list[str] = field(default_factory=list)      # обратить внимание
    title: str = ""

    @property
    def total_ron(self) -> Decimal:
        return sum((r.gross_ron for r in self.rows), Decimal(0))

    def summary(self) -> str:
        by = {}
        for r in self.rows:
            by.setdefault(r.platform, [0, Decimal(0)])
            by[r.platform][0] += 1
            by[r.platform][1] += r.gross_ron
        lines = [self.title, ""]
        lines += [f"{p}: {n} rezervări, {s:,.2f} RON" for p, (n, s) in sorted(by.items())]
        lines.append(f"TOTAL: {len(self.rows)} rezervări, {self.total_ron:,.2f} RON")
        return "\n".join(lines)


def generate(cfg: dict, files: list[Path], month: str, out_dir: Path, state_path: Path,
             cache_dir: Path, rates_file: Path | None = None,
             log: Callable[[str], None] = print) -> Result:
    y, m = map(int, month.split("-"))
    start, end = date(y, m, 1), date(y, m, monthrange(y, m)[1])
    res = Result(month=month, title=f"Raport cazare {LUNI[m]} {y} — Booking.com + Airbnb")
    reservations, seen, found = [], set(), {"Airbnb": 0, "Booking": 0}

    for p in map(Path, files):
        kind = detect_platform(p)
        if kind is None:
            res.notes.append(f"Файл не распознан и пропущен: {p.name}")
            log(f"  - пропущен (не распознан): {p.name}")
            continue
        found[NAMES[kind]] += 1
        if kind == "airbnb":
            items = airbnb.load(p, cfg.get("airbnb_currency", "RON"))
        elif kind == "booking_pdf":
            items, total = booking_pdf.load(p)
            s = sum((r.gross for r in items), Decimal(0))
            if total is not None and abs(total - s) > Decimal("0.05"):
                res.notes.append(f"Booking ({p.name}): сумма строк {s:,.2f}, а «итого» в PDF {total:,.2f} "
                                 f"(разница {s - total:,.2f}) — проверьте в Extranet")
        else:
            items = booking.load(p, cfg.get("booking_currency", "EUR"),
                                 cfg.get("include_cancelled_with_fee", True))
        log(f"  + {p.name}: {NAMES[kind]}, {len(items)} записей")
        for r in items:
            if r.key not in seen:
                seen.add(r.key)
                reservations.append(r)

    res.warnings = [f"Нет файла {name}" for name, n in found.items()
                    if n == 0 and cfg.get(f"expect_{name.lower()}", True)]

    # К какому месяцу относим бронь. platform = как в ежемесячных документах площадок:
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
    skipped = len(reservations) - len(rows)
    if skipped:
        res.notes.append(f"{skipped} брон. относятся к другому месяцу и не вошли в отчёт")

    rates = BnrRates(cache_dir, cfg.get("bnr_mode", "same_day"), rates_file)
    for r in rows:
        if r.currency != "RON":
            r.rate, r.rate_date = rates.get(r.currency, min(ref_date(r), date.today()))
    res.rows = rows

    # Сквозная нумерация документов SAGA (повторный запуск месяца не сдвигает номера)
    state_path = Path(state_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    months = state.setdefault("months", {})
    first_no = months.get(month, {}).get("first_number") or state.get(
        "next_number", cfg.get("saga", {}).get("next_number", 1))
    cfg.setdefault("saga", {})["next_number"] = first_no

    out_dir = Path(out_dir)
    company = cfg.get("company", {}).get("name", "")
    res.outputs.append(excel_report.write(rows, out_dir / f"Raport_cazare_{month}.xlsx", res.title, company))
    if cfg.get("saga", {}).get("enabled", True) and rows:
        xml, next_no = saga_xml.write(rows, out_dir / f"SAGA_import_{month}.xml", cfg, end)
        res.outputs.append(xml)
        months[month] = {"first_number": first_no, "last_number": next_no - 1}
        state["next_number"] = max(state.get("next_number", 1), next_no)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))

    if cfg.get("archive_inputs", True) and files:
        arch = out_dir / "surse"
        arch.mkdir(parents=True, exist_ok=True)
        for p in map(Path, files):
            if p.resolve().parent != arch.resolve():
                shutil.copy2(p, arch / p.name)
    return res
