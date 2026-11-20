"""Общие функции чтения файлов, дат и сумм."""
import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y",
    "%Y/%m/%d", "%d %b %Y", "%b %d, %Y", "%d %B %Y", "%B %d, %Y",
)


def read_table(path: Path) -> list[dict]:
    """Читает CSV или XLSX и возвращает список строк-словарей."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            return []
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        return [
            {header[i]: v for i, v in enumerate(r) if i < len(header) and header[i]}
            for r in rows[1:] if any(v not in (None, "") for v in r)
        ]
    if path.suffix.lower() == ".xls":
        raise ValueError(
            f"{path.name}: старый формат .xls не поддерживается — "
            "выгрузите в CSV или откройте в Excel и сохраните как .xlsx"
        )
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [
        {(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        for row in reader if any((v or "").strip() for v in row.values() if isinstance(v, str))
    ]


def pick(row: dict, names: Iterable[str], default=None):
    """Возвращает значение первой существующей колонки (без учёта регистра)."""
    lower = {k.lower().strip(): v for k, v in row.items() if k}
    for n in names:
        v = lower.get(n.lower())
        if v not in (None, ""):
            return v
    return default


MONTHS = {  # ru / ro / en сокращения → номер месяца
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8,
    "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
    "ian": 1, "feb": 2, "mar": 3, "apr": 4, "mai": 5, "iun": 6, "iul": 7, "aug": 8,
    "sep": 9, "oct": 10, "noi": 11, "nov": 11, "dec": 12, "jan": 1, "may": 5, "jun": 6, "jul": 7,
}
_TEXT_DATE = re.compile(r"^(\d{1,2})\s+([^\d\s.,]+)\.?,?\s+(\d{4})$")


def parse_date(value) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    s = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?.*$", "", s)  # отбросить время
    m = _TEXT_DATE.match(s.replace("\xa0", " "))
    if m:
        month = MONTHS.get(m.group(2).lower()[:3])
        if month:
            return date(int(m.group(3)), month, int(m.group(1)))
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Не удалось распознать дату: {value!r}")


def parse_amount(value) -> tuple[Decimal, Optional[str]]:
    """'1.234,50 EUR' / '€ 1,234.50' / 250 -> (Decimal, 'EUR')."""
    if value in (None, ""):
        return Decimal("0"), None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)), None
    s = str(value).strip()
    currency = None
    m = re.search(r"\b([A-Z]{3})\b", s)
    if m:
        currency = m.group(1)
    symbols = {"€": "EUR", "$": "USD", "£": "GBP", "lei": "RON"}
    for sym, code in symbols.items():
        if sym in s:
            currency = currency or code
    num = re.sub(r"[^\d,.\-]", "", s)
    if not num or num in "-.,":
        return Decimal("0"), currency
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        num = (head.replace(",", "") + "." + tail) if len(tail) != 3 else num.replace(",", "")
    try:
        return Decimal(num), currency
    except InvalidOperation:
        raise ValueError(f"Не удалось распознать сумму: {value!r}")
