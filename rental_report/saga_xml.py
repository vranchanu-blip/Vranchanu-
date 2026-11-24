"""Файл импорта в SAGA (Fișier → Import date → Facturi ieșiri din XML).

Структура соответствует формату XML, который SAGA принимает от программ выставления
счетов (<Facturi><Factura><Antet/><Detalii/><Sumar/></Factura></Facturi>).
ВАЖНО: перед первым использованием пусть бухгалтер проверит импорт на тестовой базе.
"""
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .models import Reservation

Q = Decimal("0.01")


def _q(x: Decimal) -> str:
    return str(x.quantize(Q, ROUND_HALF_UP))


def _sub(parent, tag, value=""):
    el = ET.SubElement(parent, tag)
    el.text = "" if value is None else str(value)
    return el


def _invoice(root, cfg: dict, number: str, inv_date: date, client: str, client_info: str,
             lines: list[tuple[str, Decimal, Decimal]], vat_rate: Decimal):
    f = ET.SubElement(root, "Factura")
    a = ET.SubElement(f, "Antet")
    co = cfg.get("company", {})
    _sub(a, "FurnizorNume", co.get("name", ""))
    _sub(a, "FurnizorCIF", co.get("cif", ""))
    _sub(a, "FurnizorNrRegCom", co.get("reg_com", ""))
    _sub(a, "FurnizorCapital", co.get("capital", ""))
    _sub(a, "FurnizorAdresa", co.get("address", ""))
    _sub(a, "FurnizorBanca", co.get("bank", ""))
    _sub(a, "FurnizorIBAN", co.get("iban", ""))
    _sub(a, "ClientNume", client)
    _sub(a, "ClientInformatiiSuplimentare", client_info)
    _sub(a, "ClientCIF", cfg.get("saga", {}).get("client_cif", ""))
    _sub(a, "ClientNrRegCom", "")
    _sub(a, "ClientJudet", "")
    _sub(a, "ClientAdresa", "")
    _sub(a, "ClientBanca", "")
    _sub(a, "ClientIBAN", "")
    _sub(a, "FacturaNumar", number)
    _sub(a, "FacturaData", inv_date.strftime("%d.%m.%Y"))
    _sub(a, "FacturaScadenta", inv_date.strftime("%d.%m.%Y"))
    _sub(a, "FacturaTaxareInversa", "Nu")
    _sub(a, "FacturaTVAIncasare", "Nu")
    _sub(a, "FacturaInformatiiSuplimentare", "")
    _sub(a, "FacturaMoneda", "RON")
    det = ET.SubElement(ET.SubElement(f, "Detalii"), "Continut")
    tot_val = tot_vat = Decimal(0)
    for i, (descr, qty, total_with_vat) in enumerate(lines, 1):
        base = (total_with_vat / (1 + vat_rate / 100)).quantize(Q, ROUND_HALF_UP)
        vat = total_with_vat - base
        price = (base / qty).quantize(Decimal("0.0001"), ROUND_HALF_UP) if qty else base
        ln = ET.SubElement(det, "Linie")
        _sub(ln, "LinieNrCrt", i)
        _sub(ln, "Descriere", descr)
        _sub(ln, "CodArticolFurnizor", cfg.get("saga", {}).get("article_code", "CAZARE"))
        _sub(ln, "CodArticolClient", "")
        _sub(ln, "InformatiiSuplimentare", "")
        _sub(ln, "UM", cfg.get("saga", {}).get("unit", "BUC"))
        _sub(ln, "Cantitate", qty)
        _sub(ln, "Pret", price)
        _sub(ln, "Valoare", _q(base))
        _sub(ln, "ProcTVA", _q(vat_rate))
        _sub(ln, "TVA", _q(vat))
        tot_val += base
        tot_vat += vat
    s = ET.SubElement(f, "Sumar")
    _sub(s, "TotalValoare", _q(tot_val))
    _sub(s, "TotalTVA", _q(tot_vat))
    _sub(s, "Total", _q(tot_val + tot_vat))


def _nopti(n: int) -> str:
    return f"{n} NOAPTE" if n == 1 else f"{n} NOPTI"


def file_name(cfg: dict, month: str) -> str:
    """SAGA ожидает имя вида F_<cod fiscal>_<...>.xml."""
    cif = str(cfg.get("company", {}).get("cif", "")).upper().replace("RO", "").strip() or "0"
    return f"F_{cif}_{month}.xml"


def write(rows: list[Reservation], path: Path, cfg: dict, period_end: date) -> tuple[Path, int]:
    saga = cfg.get("saga", {})
    mode = saga.get("mode", "per_reservation")
    series = saga.get("series", "CAZ")
    number = int(saga.get("next_number", 1))
    vat_rate = Decimal(str(cfg.get("vat_rate", 0)))
    month_end_date = saga.get("invoice_date", "month_end") == "month_end"
    qty_nights = saga.get("quantity", "one") == "nights"
    upper = saga.get("uppercase_names", True)
    root = ET.Element("Facturi")
    count = 0
    if mode == "per_platform":
        groups = defaultdict(list)
        for r in rows:
            groups[r.platform].append(r)
        for platform, items in sorted(groups.items()):
            lines = [(f"CAZARE {r.guest} {r.checkin:%d.%m}-{r.checkout:%d.%m} {platform.upper()}",
                      Decimal(r.nights or 1) if qty_nights else Decimal(1), r.gross_ron) for r in items]
            _invoice(root, cfg, f"{series}{number}", period_end,
                     f"CLIENTI {platform.upper()}", f"Raport lunar {period_end:%m.%Y}", lines, vat_rate)
            number += 1
            count += 1
    else:
        for r in sorted(rows, key=lambda x: (x.checkout or period_end, x.platform, x.code)):
            d = period_end if month_end_date else min(r.checkout or period_end, period_end)
            descr = saga.get("line_template", "CAZARE {nopti} {platforma}").format(
                nopti=_nopti(r.nights or 1), platforma=r.platform.upper(),
                checkin=f"{r.checkin:%d.%m.%Y}", checkout=f"{r.checkout:%d.%m.%Y}")
            guest = r.guest or "PERSOANA FIZICA"
            _invoice(root, cfg, f"{series}{number}", d, guest.upper() if upper else guest,
                     f"{r.platform} {r.code}".strip() + f", {r.checkin:%d.%m}-{r.checkout:%d.%m.%Y}",
                     [(descr, Decimal(r.nights or 1) if qty_nights else Decimal(1), r.gross_ron)], vat_rate)
            number += 1
            count += 1
    ET.indent(root)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path, number
