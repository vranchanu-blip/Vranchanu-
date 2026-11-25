import httpx

from bot.modules.portfolio import render_ibkr
from bot.modules.portfolio.ibkr import IbkrClient, IbkrError, parse_statement

STATEMENT = """<FlexQueryResponse queryName="bot" type="AF">
<FlexStatements count="1">
<FlexStatement accountId="U1234567" fromDate="20261124" toDate="20261124" period="LastBusinessDay">
<AccountInformation accountId="U1234567" currency="USD" />
<OpenPositions>
<OpenPosition accountId="U1234567" currency="USD" fxRateToBase="1" assetCategory="STK" symbol="AAPL"
 description="APPLE INC" position="10" markPrice="230" positionValue="2300" costBasisMoney="2000"
 fifoPnlUnrealized="300" levelOfDetail="SUMMARY" />
<OpenPosition accountId="U1234567" currency="EUR" fxRateToBase="1.05" assetCategory="STK" symbol="VWCE"
 description="VANGUARD FTSE ALL-WORLD" position="20" markPrice="140" positionValue="2800" costBasisMoney="3000"
 fifoPnlUnrealized="-200" levelOfDetail="SUMMARY" />
<OpenPosition accountId="U1234567" currency="USD" fxRateToBase="1" assetCategory="STK" symbol="AAPL"
 position="10" positionValue="2300" costBasisMoney="2000" fifoPnlUnrealized="300" levelOfDetail="LOT" />
</OpenPositions>
<CashReport>
<CashReportCurrency accountId="U1234567" currency="BASE_SUMMARY" endingCash="1000.5" />
<CashReportCurrency accountId="U1234567" currency="USD" endingCash="1000.5" />
</CashReport>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""


def test_parse_statement():
    pf = parse_statement(STATEMENT)
    assert pf.accounts == ["U1234567"] and pf.base == "USD" and str(pf.as_of) == "2026-11-24"
    assert [p.symbol for p in pf.positions] == ["VWCE", "AAPL"]          # по стоимости, без строк LOT
    assert round(pf.positions[0].value_base, 2) == 2940.0                # EUR -> USD
    assert round(pf.total, 2) == 2940 + 2300 + 1000.5
    assert round(pf.pnl, 2) == 300 - 210
    assert pf.cash == 1000.5


def test_render_ibkr():
    text = render_ibkr(parse_statement(STATEMENT))
    assert "U•••4567" in text and "24.11.2026" in text
    assert "💰 <b>6 240,50 $</b>" in text
    assert "├ 📈 Бумаги: 5 240,00 $" in text and "└ 💵 Наличные: 1 000,50 $" in text
    assert "🟢 Результат: <b>+90,00 $</b>" in text
    assert "Лучшая" not in text and "Худшая" not in text
    table = text[text.index("<pre>") + 5:text.index("</pre>")].split("\n")
    assert table[0].split() == ["Тикер", "Сумма", "Доля", "Итог"]
    assert table[2].split() == ["VWCE", "2", "940", "47,1%", "-6,7%"]
    assert len({len(line) for line in table}) == 1        # все строки одной ширины


def make_client(handler):
    c = IbkrClient()
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


async def test_fetch_waits_for_statement(monkeypatch):
    import bot.modules.portfolio.ibkr as mod

    async def no_sleep(_):
        pass

    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
    polls = []

    def handler(request):
        if "SendRequest" in request.url.path:
            return httpx.Response(200, text="<FlexStatementResponse><Status>Success</Status>"
                                            "<ReferenceCode>42</ReferenceCode>"
                                            "<Url>https://ibkr.test/GetStatement</Url></FlexStatementResponse>")
        polls.append(request.url.params["q"])
        if len(polls) < 2:
            return httpx.Response(200, text="<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode>"
                                            "<ErrorMessage>in progress</ErrorMessage></FlexStatementResponse>")
        return httpx.Response(200, text=STATEMENT)

    c = make_client(handler)
    pf = await c.portfolio("tok", "1")
    assert polls == ["42", "42"] and len(pf.positions) == 2
    await c.close()


async def test_fetch_error_message():
    def handler(request):
        return httpx.Response(200, text="<FlexStatementResponse><Status>Fail</Status><ErrorCode>1012</ErrorCode>"
                                        "<ErrorMessage>Token has expired.</ErrorMessage></FlexStatementResponse>")

    c = make_client(handler)
    try:
        await c.portfolio("tok", "1")
    except IbkrError as exc:
        assert "Token has expired" in str(exc) and "1012" in str(exc)
    else:
        raise AssertionError("expected IbkrError")
    await c.close()


async def test_http_error_hides_token():
    def handler(request):
        return httpx.Response(403, text="denied")

    c = make_client(handler)
    try:
        await c.portfolio("SECRETTOKEN123", "1")
    except IbkrError as exc:
        assert "HTTP 403" in str(exc) and "SECRETTOKEN123" not in str(exc)
    else:
        raise AssertionError("expected IbkrError")
    await c.close()


async def test_follows_redirect(monkeypatch):
    import bot.modules.portfolio.ibkr as mod

    async def no_sleep(_):
        pass

    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)

    def handler(request):
        if request.url.host == "ndcdyn.interactivebrokers.com":
            return httpx.Response(302, headers={"Location": "https://gdcdyn.ibkr.test/SendRequest?t=x&q=1&v=3"})
        if "SendRequest" in request.url.path:
            return httpx.Response(200, text="<FlexStatementResponse><Status>Success</Status>"
                                            "<ReferenceCode>7</ReferenceCode>"
                                            "<Url>https://gdcdyn.ibkr.test/GetStatement</Url></FlexStatementResponse>")
        return httpx.Response(200, text=STATEMENT)

    c = IbkrClient()
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    assert len((await c.portfolio("tok", "1")).positions) == 2
    await c.close()


YTD_STATEMENT = STATEMENT.replace(
    'fromDate="20261124" toDate="20261124" period="LastBusinessDay">',
    'fromDate="20260101" toDate="20261124" period="YearToDate">'
    '<ChangeInNAV accountId="U1234567" fromDate="20260101" toDate="20261124" startingValue="5800" '
    'endingValue="6240.5" twr="5.23" />'
    '<EquitySummaryInBase>'
    '<EquitySummaryByReportDateInBase reportDate="20261121" total="6300" />'
    '<EquitySummaryByReportDateInBase reportDate="20261124" total="6240.5" />'
    '</EquitySummaryInBase>',
)


def test_day_and_ytd():
    pf = parse_statement(YTD_STATEMENT)
    day, pct = pf.day_change
    assert str(day) == "2026-11-24" and round(pct, 2) == round((6240.5 / 6300 - 1) * 100, 2)
    assert pf.ytd_pct == 5.23


def test_ytd_missing_for_last_day_query():
    pf = parse_statement(STATEMENT)
    assert pf.day_change is None and pf.ytd_pct is None


def test_markets_portfolio_row_under_indices():
    from bot.modules.portfolio import render_markets
    from bot.modules.portfolio.market import Instrument

    text = render_markets(
        [("📊 Индексы", True, [(Instrument("^GSPC", "S&P 500", "🇺🇸"), RuntimeError())]),
         ("🪙 Сырьё", False, [(Instrument("SI=F", "Серебро", "🥈"), RuntimeError())])],
        parse_statement(YTD_STATEMENT),
    )
    assert text.index("S&amp;P 500") < text.index("Мой портфель") < text.index("🪙 Сырьё")
    assert "<code>6 240</code> <i>$</i>" in text
    assert "За день (24.11): 🔴 <b>-0,94%</b>" in text and "С начала года: 🟢 <b>+5,23%</b>" in text
    assert "настройка отчёта" not in text

    old = render_markets([("📊", True, []), ("🪙", False, [])], parse_statement(STATEMENT))
    assert "За день: —" in old and "настройка отчёта IBKR" in old
    assert "Мой портфель" not in render_markets([("📊", True, []), ("🪙", False, [])], None)
