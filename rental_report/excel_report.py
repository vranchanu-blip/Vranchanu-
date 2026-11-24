"""Excel-отчёт для бухгалтера (заголовки на румынском)."""
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import Reservation, status_ro

HEAD = ["Nr.", "Platformă", "Cod rezervare", "Client", "Țara", "Unitate", "Check-in", "Check-out",
        "Nopți", "Stare", "Monedă", "Încasat brut", "Comision platformă", "Net de primit",
        "Curs BNR", "Data curs", "Brut RON", "Comision RON", "Net RON"]
WIDTH = [5, 10, 16, 28, 6, 18, 11, 11, 6, 12, 7, 13, 13, 13, 9, 11, 13, 13, 13]
MONEY = "#,##0.00"
BOLD = Font(bold=True)
FILL = PatternFill("solid", fgColor="DDEBF7")
THIN = Border(bottom=Side(style="thin", color="999999"))


def _sheet(ws, rows: list[Reservation]):
    ws.append(HEAD)
    for c, w in enumerate(WIDTH, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
        cell = ws.cell(row=1, column=c)
        cell.font, cell.fill, cell.border = BOLD, FILL, THIN
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for i, r in enumerate(rows, 1):
        ws.append([i, r.platform, r.code, r.guest, r.country, r.listing, r.checkin, r.checkout,
                   r.nights, status_ro(r.status), r.currency, float(r.gross), float(r.commission), float(r.net),
                   float(r.rate) if r.rate else 1.0, r.rate_date, float(r.gross_ron),
                   float(r.commission_ron), float(r.net_ron)])
    n = len(rows) + 1
    total_row = n + 1
    ws.cell(row=total_row, column=4, value="TOTAL").font = BOLD
    ws.cell(row=total_row, column=9, value=f"=SUM(I2:I{n})").font = BOLD
    for col in (17, 18, 19):
        L = get_column_letter(col)
        ws.cell(row=total_row, column=col, value=f"=SUM({L}2:{L}{n})").font = BOLD
    for row in ws.iter_rows(min_row=2, max_row=total_row):
        for c in row:
            if c.column in (7, 8, 16):
                c.number_format = "DD.MM.YYYY"
            elif c.column in (12, 13, 14, 17, 18, 19):
                c.number_format = MONEY
            elif c.column == 15:
                c.number_format = "0.0000"
    for col in (3, 5, 6):  # Cod rezervare / Țara / Unitate — скрыть, если данных нет
        if not any(ws.cell(row=i, column=col).value for i in range(2, n + 1)):
            ws.column_dimensions[get_column_letter(col)].hidden = True
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEAD))}{n}"


def write(rows: list[Reservation], path: Path, title: str, company: str = "") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Rezumat"
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14)
    if company:
        ws["A2"] = company
    ws.append([])
    ws.append(["Platformă", "Monedă", "Rezervări", "Nopți", "Brut (valută)", "Comision (valută)",
               "Net (valută)", "Brut RON", "Comision RON", "Net RON"])
    for c in ws[4]:
        c.font, c.fill = BOLD, FILL
    groups = defaultdict(list)
    for r in rows:
        groups[(r.platform, r.currency)].append(r)
    tot = defaultdict(Decimal)
    for (platform, cur), items in sorted(groups.items()):
        line = [platform, cur, len(items), sum(r.nights for r in items),
                sum(r.gross for r in items), sum(r.commission for r in items), sum(r.net for r in items),
                sum(r.gross_ron for r in items), sum(r.commission_ron for r in items),
                sum(r.net_ron for r in items)]
        for k, v in zip(("g", "c", "n"), line[7:]):
            tot[k] += v
        ws.append([float(v) if isinstance(v, Decimal) else v for v in line])
    ws.append(["TOTAL", "", len(rows), sum(r.nights for r in rows), None, None, None,
               float(tot["g"]), float(tot["c"]), float(tot["n"])])
    for c in ws[ws.max_row]:
        c.font = BOLD
    for row in ws.iter_rows(min_row=5, max_row=ws.max_row):
        for c in row[4:]:
            c.number_format = MONEY
    for i, w in enumerate([12, 8, 10, 8, 14, 16, 14, 14, 14, 14], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    _sheet(wb.create_sheet("Toate"), rows)
    for platform in sorted({r.platform for r in rows}):
        _sheet(wb.create_sheet(platform), [r for r in rows if r.platform == platform])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
