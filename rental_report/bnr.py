"""Курсы Национального банка Румынии (BNR) — https://www.bnr.ro/nbrfxrates.xml."""
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

YEAR_URL = "https://www.bnr.ro/files/xml/years/nbrfxrates{year}.xml"
NS = "{http://www.bnr.ro/xsd}"


def parse_xml(content: bytes) -> dict[date, dict[str, Decimal]]:
    root = ET.fromstring(content)
    out: dict[date, dict[str, Decimal]] = {}
    for cube in root.iter(f"{NS}Cube"):
        d = date.fromisoformat(cube.get("date"))
        rates = {}
        for rate in cube.iter(f"{NS}Rate"):
            mult = Decimal(rate.get("multiplier", "1"))
            rates[rate.get("currency")] = Decimal(rate.text.strip()) / mult
        out[d] = rates
    return out


class BnrRates:
    """mode = 'same_day': курс, опубликованный в эту дату (или последний до неё);
    mode = 'previous_day': курс последнего банковского дня строго ДО даты."""

    def __init__(self, cache_dir: Path, mode: str = "same_day", offline_file: Optional[Path] = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self._data: dict[date, dict[str, Decimal]] = {}
        self._years: set[int] = set()
        self.offline = bool(offline_file)   # курсы из файла — интернет не нужен
        if offline_file:
            self._data.update(parse_xml(Path(offline_file).read_bytes()))

    def _load_year(self, year: int) -> None:
        if year in self._years:
            return
        self._years.add(year)
        cache = self.cache_dir / f"nbrfxrates{year}.xml"
        fresh_needed = year >= date.today().year
        content = None
        if not self.offline and (fresh_needed or not cache.exists()):
            try:
                req = urllib.request.Request(YEAR_URL.format(year=year),
                                             headers={"User-Agent": "rental-report/0.1"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    content = resp.read()
                cache.write_bytes(content)
            except OSError:
                content = None
        if content is None and cache.exists():
            content = cache.read_bytes()
        if content:
            self._data.update(parse_xml(content))

    def get(self, currency: str, on: date) -> tuple[Decimal, date]:
        currency = currency.upper()
        if currency == "RON":
            return Decimal("1"), on
        self._load_year(on.year)
        if on.month == 1 and on.day < 10:
            self._load_year(on.year - 1)
        d = on - timedelta(days=1) if self.mode == "previous_day" else on
        for _ in range(15):  # выходные / праздники
            if d.year not in self._years:
                self._load_year(d.year)
            rates = self._data.get(d)
            if rates and currency in rates:
                return rates[currency], d
            d -= timedelta(days=1)
        raise LookupError(f"Нет курса BNR для {currency} на {on} (нет интернета?)")
