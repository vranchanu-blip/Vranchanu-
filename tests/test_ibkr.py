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
    assert "💰 Всего: <b>6 240,50 USD</b>" in text
    assert "<code>AAPL</code> 10 шт · 2 300,00 USD · 36,9%  🟢 <b>+15,00%</b>" in text
    assert "🔴 <b>-6,67%</b>" in text


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
