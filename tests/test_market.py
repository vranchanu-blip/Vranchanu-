from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.modules.portfolio import render
from bot.modules.portfolio.market import parse_chart

NY = ZoneInfo("America/New_York")


def ts(y, m, d):
    return int(datetime(y, m, d, 16, 0, tzinfo=NY).timestamp())


def payload():
    days = [(2024, 11, 22, 4000.0), (2024, 11, 25, 4100.0), (2025, 12, 31, 5000.0),
            (2026, 1, 2, 5050.0), (2026, 11, 24, 5900.0), (2026, 11, 25, 6000.0)]
    return {"chart": {"result": [{
        "meta": {"symbol": "^GSPC", "regularMarketPrice": 6000.0, "regularMarketTime": ts(2026, 11, 25),
                 "exchangeTimezoneName": "America/New_York",
                 "currentTradingPeriod": {"regular": {"start": ts(2026, 11, 25) - 6 * 3600,
                                                      "end": ts(2026, 11, 25) + 1800}}},
        "timestamp": [ts(y, m, d) for y, m, d, _ in days] + [ts(2026, 11, 26)],
        "indicators": {"quote": [{"close": [c for *_, c in days] + [None]}]},
    }]}}


def test_parse_chart():
    q = parse_chart(payload(), now=datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc))
    assert q.price == 6000.0 and q.is_open
    assert round(q.day_pct, 4) == round((6000 / 5900 - 1) * 100, 4)
    assert round(q.ytd_pct, 4) == 20.0
    assert round(q.year_pct, 4) == round((6000 / 4100 - 1) * 100, 4)   # 25.11.2025 нет, берём ближайший раньше


def test_render():
    q = parse_chart(payload(), now=datetime(2026, 11, 26, 3, 0, tzinfo=timezone.utc))
    text = render([("S&P 500", "🇺🇸", q), ("Nasdaq Composite", "🇺🇸", RuntimeError())])
    assert "<code>6 000,00</code>" in text
    assert "За день (25.11): 🟢 <b>+1,69%</b>" in text
    assert "С начала года: 🟢 <b>+20,00%</b>" in text
    assert "Nasdaq Composite</b> — нет данных" in text
    assert "биржа закрыта" in text
