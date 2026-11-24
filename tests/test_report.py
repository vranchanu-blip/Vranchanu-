import json
import shutil
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
import yaml

from rental_report import airbnb, booking
from rental_report.__main__ import main
from rental_report.bnr import BnrRates
from rental_report.parsing import parse_amount, parse_date

DATA = Path(__file__).parent / "data"


def test_parse_amount():
    assert parse_amount("412,50 EUR") == (Decimal("412.50"), "EUR")
    assert parse_amount("€ 1,234.50") == (Decimal("1234.50"), "EUR")
    assert parse_amount("1.234,50") == (Decimal("1234.50"), None)
    assert parse_amount("1,234") == (Decimal("1234"), None)
    assert parse_amount(250) == (Decimal("250"), None)


def test_parse_date():
    assert parse_date("10/04/2026") == date(2026, 10, 4)
    assert parse_date("2026-09-20 14:02:11") == date(2026, 9, 20)
    assert parse_date("14.10.2026") == date(2026, 10, 14)


def test_airbnb():
    rows = {r.code: r for r in airbnb.load(DATA / "airbnb_sample.csv")}
    assert set(rows) == {"HMABC12345", "HMXYZ67890", "HMNOV00001"}
    r = rows["HMXYZ67890"]  # корректировка -10 суммируется
    assert (r.gross, r.commission, r.net) == (Decimal("230.00"), Decimal("7.20"), Decimal("222.80"))
    assert r.checkout == date(2026, 10, 23) and r.nights == 3


def test_booking():
    rows = {r.code: r for r in booking.load(DATA / "booking_sample.csv")}
    assert "4123456791" not in rows  # отмена без оплаты
    r = rows["4123456790"]
    assert r.gross == Decimal("412.50") and r.commission == Decimal("61.88") and r.net == Decimal("350.62")
    assert r.country == "PL"


def test_bnr_weekend_fallback():
    rates = BnrRates(Path("/tmp/none-cache"), offline_file=DATA / "bnr_sample.xml")
    rates._years.update({2026, 2025})
    rate, d = rates.get("EUR", date(2026, 10, 4))  # воскресенье → пятница 02.10
    assert d == date(2026, 10, 2)
    prev = BnrRates(Path("/tmp/none-cache"), "previous_day", DATA / "bnr_sample.xml")
    prev._years.update({2026, 2025})
    assert prev.get("EUR", date(2026, 10, 5))[1] == date(2026, 10, 2)
    assert rates.get("HUF", date(2026, 10, 5))[0] == Decimal("0.0125")


def test_full_run(tmp_path, monkeypatch):
    shutil.copytree(DATA, tmp_path / "inbox", ignore=shutil.ignore_patterns("*.xml"))
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yaml").read_text(encoding="utf-8"))
    cfg["saga"]["next_number"] = 100
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(BnrRates, "_load_year", lambda self, y: self._years.add(y))
    args = ["--month", "2026-10", "--config", str(tmp_path / "config.yaml"),
            "--rates-file", str(DATA / "bnr_sample.xml")]
    assert main(args) == 0

    out = tmp_path / "reports" / "2026-10"
    wb = openpyxl.load_workbook(out / "Raport_cazare_2026-10.xlsx")
    codes = [row[2] for row in wb["Toate"].iter_rows(min_row=2, values_only=True) if row[0]]
    # выезд в октябре: 2 Airbnb + 3 Booking (Ivanova выехала 01.10), HMNOV00001 — в ноябре
    assert sorted(codes) == sorted(["HMABC12345", "HMXYZ67890", "4123456789", "4123456790", "4123456792"])

    root = ET.parse(out / "SAGA_import_2026-10.xml").getroot()
    nums = [f.findtext("Antet/FacturaNumar") for f in root.findall("Factura")]
    assert nums == [f"CAZ{n}" for n in range(100, 105)]
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["next_number"] == 105

    # повторный запуск того же месяца не сдвигает нумерацию
    assert main(args) == 0
    root = ET.parse(out / "SAGA_import_2026-10.xml").getroot()
    assert root.find("Factura/Antet/FacturaNumar").text == "CAZ100"


def test_airbnb_new_russian_format():
    rows = airbnb.load(DATA / "airbnb_ru_sample.csv")
    assert [r.guest for r in rows] == ["Test Guest One", "Test Guest Two", "Тест Гость"]
    r = rows[0]
    assert (r.gross, r.commission, r.net, r.currency) == (
        Decimal("1032.50"), Decimal("30.98"), Decimal("1001.52"), "RON")
    assert (r.checkin, r.checkout, r.nights, r.payout_date) == (
        date(2026, 9, 29), date(2026, 10, 2), 3, date(2026, 9, 30))


def test_booking_pdf_text():
    from rental_report import booking_pdf
    text = (DATA / "booking_statement_ru.txt").read_text(encoding="utf-8")
    assert booking_pdf.is_booking_statement(text)
    rows, total = booking_pdf.parse_text(text)
    assert [(r.code, r.guest, r.nights) for r in rows] == [
        ("5816337346", "Karl Test", 1), ("5743695472", "Ana-Maria Test Two", 4)]
    assert rows[1].gross == Decimal("1120") and rows[1].commission == Decimal("166.52")
    assert rows[1].checkin == date(2026, 9, 24) and total == Decimal("1472.00")


def test_platform_basis_airbnb_by_payout(tmp_path, monkeypatch):
    (tmp_path / "inbox").mkdir()
    shutil.copy(DATA / "airbnb_ru_sample.csv", tmp_path / "inbox")
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yaml").read_text(encoding="utf-8"))
    cfg["expect_booking"] = False
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert main(["--month", "2026-09", "--config", str(tmp_path / "config.yaml")]) == 0
    root = ET.parse(tmp_path / "reports" / "2026-09" / "SAGA_import_2026-09.xml").getroot()
    names = [f.findtext("Antet/ClientNume") for f in root.findall("Factura")]
    assert "Test Guest One" in names and len(names) == 3   # выезд 02.10, но выплата 30.09
    assert root.find("Factura/Antet/ClientCIF").text == "0000000000000"
