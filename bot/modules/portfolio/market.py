"""Котировки индексов из Yahoo Finance (бесплатно, без ключа, задержка до 15 минут)."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (compatible; PersonalTelegramBot/1.0)"

# (тикер Yahoo, название, флаг)
INDICES = [
    ("^GSPC", "S&P 500", "🇺🇸"),
    ("^IXIC", "Nasdaq Composite", "🇺🇸"),
]


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    market_date: date          # дата последней торговой сессии
    day_pct: float | None      # к закрытию предыдущей сессии
    ytd_pct: float | None      # к закрытию последнего дня прошлого года
    year_pct: float | None     # к закрытию ~год назад
    is_open: bool


def pct(now: float, base: float | None) -> float | None:
    return (now / base - 1) * 100 if base else None


def parse_chart(payload: dict, now: datetime | None = None) -> Quote:
    """Разобрать ответ Yahoo chart (range=2y, interval=1d)."""
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    closes = result["indicators"]["quote"][0]["close"]
    bars = [
        (datetime.fromtimestamp(ts, tz).date(), c)
        for ts, c in zip(result["timestamp"], closes)
        if c is not None
    ]
    price = float(meta.get("regularMarketPrice") or bars[-1][1])
    market_time = meta.get("regularMarketTime")
    market_date = datetime.fromtimestamp(market_time, tz).date() if market_time else bars[-1][0]

    before_today = [c for d, c in bars if d < market_date]
    prev_close = before_today[-1] if before_today else None
    last_year = [c for d, c in bars if d.year < market_date.year]
    ytd_base = last_year[-1] if last_year else None
    year_ago = market_date - timedelta(days=365)
    year_base = next((c for d, c in reversed(bars) if d <= year_ago), None)

    now = now or datetime.now(timezone.utc)
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    is_open = bool(period) and period.get("start", 0) <= now.timestamp() < period.get("end", 0)

    return Quote(
        symbol=meta.get("symbol", ""),
        price=price,
        market_date=market_date,
        day_pct=pct(price, prev_close),
        ytd_pct=pct(price, ytd_base),
        year_pct=pct(price, year_base),
        is_open=is_open,
    )


class MarketClient:
    CACHE_TTL = 60

    def __init__(self, timeout: float = 20.0):
        self._http = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT})
        self._cache: dict[str, tuple[float, Quote]] = {}

    async def quote(self, symbol: str) -> Quote:
        hit = self._cache.get(symbol)
        if hit and time.monotonic() - hit[0] < self.CACHE_TTL:
            return hit[1]
        resp = await self._http.get(
            CHART_URL.format(symbol=symbol), params={"range": "2y", "interval": "1d"}
        )
        resp.raise_for_status()
        q = parse_chart(resp.json())
        self._cache[symbol] = (time.monotonic(), q)
        return q

    async def quotes(self, symbols: list[str]) -> list[Quote | Exception]:
        return await asyncio.gather(*(self.quote(s) for s in symbols), return_exceptions=True)

    async def close(self) -> None:
        await self._http.aclose()
