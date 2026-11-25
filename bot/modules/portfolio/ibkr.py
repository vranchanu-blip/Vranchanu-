"""Портфель Interactive Brokers через Flex Web Service (только чтение отчётов).

Пользователь создаёт в кабинете IBKR Activity Flex Query (Open Positions, Cash Report,
Account Information; формат XML) и токен Flex Web Service. Бот запрашивает отчёт так:
SendRequest(token, query) -> ReferenceCode -> GetStatement(token, reference).
"""
from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

SEND_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
USER_AGENT = "PersonalTelegramBot/1.0"
IN_PROGRESS = {"1019", "1018"}  # отчёт ещё формируется / слишком частые запросы


class IbkrError(Exception):
    pass


def _f(value: str | None) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y%m%d;%H%M%S", "%Y-%m-%d;%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class Position:
    symbol: str
    description: str
    category: str
    currency: str
    quantity: float
    price: float
    value_base: float
    cost_base: float
    pnl_base: float

    @property
    def pnl_pct(self) -> float | None:
        return self.pnl_base / self.cost_base * 100 if self.cost_base else None


@dataclass
class Portfolio:
    accounts: list[str] = field(default_factory=list)
    base: str = "USD"
    as_of: date | None = None
    positions: list[Position] = field(default_factory=list)
    cash: float | None = None

    @property
    def positions_value(self) -> float:
        return sum(p.value_base for p in self.positions)

    @property
    def total(self) -> float:
        return self.positions_value + (self.cash or 0.0)

    @property
    def pnl(self) -> float:
        return sum(p.pnl_base for p in self.positions)

    @property
    def cost(self) -> float:
        return sum(p.cost_base for p in self.positions)


def parse_statement(xml_text: str) -> Portfolio:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise IbkrError("IBKR прислал отчёт не в формате XML — в Flex Query выберите формат XML.") from exc
    statements = root.findall(".//FlexStatement")
    if not statements:
        raise IbkrError("В ответе IBKR нет отчёта (FlexStatement).")
    pf = Portfolio()
    for st in statements:
        if st.get("accountId"):
            pf.accounts.append(st.get("accountId"))
        pf.as_of = max(filter(None, [pf.as_of, _date(st.get("toDate"))]), default=None)
        info = st.find("AccountInformation")
        if info is not None and info.get("currency"):
            pf.base = info.get("currency")
        for op in st.iter("OpenPosition"):
            if op.get("levelOfDetail", "SUMMARY").upper() not in ("SUMMARY", ""):
                continue  # строки по отдельным лотам дублируют итог по позиции
            fx = _f(op.get("fxRateToBase")) or 1.0
            pf.positions.append(Position(
                symbol=op.get("symbol", "?"),
                description=op.get("description", ""),
                category=op.get("assetCategory", ""),
                currency=op.get("currency", ""),
                quantity=_f(op.get("position")),
                price=_f(op.get("markPrice")),
                value_base=_f(op.get("positionValue")) * fx,
                cost_base=_f(op.get("costBasisMoney")) * fx,
                pnl_base=_f(op.get("fifoPnlUnrealized")) * fx,
            ))
        for cr in st.iter("CashReportCurrency"):
            if cr.get("currency") == "BASE_SUMMARY":
                pf.cash = (pf.cash or 0.0) + _f(cr.get("endingCash"))
    pf.positions.sort(key=lambda p: abs(p.value_base), reverse=True)
    return pf


def _status(xml_text: str) -> tuple[str, dict[str, str]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        snippet = " ".join(xml_text.split())[:120]
        raise IbkrError(f"Непонятный ответ IBKR: {snippet}") from exc
    fields = {child.tag: (child.text or "").strip() for child in root}
    return root.tag, fields


class IbkrClient:
    CACHE_TTL = 15 * 60  # отчёт Flex обновляется раз в день, чаще не дёргаем IBKR

    def __init__(self, timeout: float = 60.0):
        self._http = httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT})
        self._cache: dict[tuple[str, str], tuple[float, Portfolio]] = {}

    async def _get(self, url: str, params: dict) -> httpx.Response:
        """GET без утечки токена в текст ошибок (в URL ошибок httpx он был бы виден)."""
        try:
            resp = await self._http.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise IbkrError("IBKR не ответил вовремя. Попробуйте через пару минут.") from exc
        except httpx.HTTPError as exc:
            raise IbkrError(f"Нет связи с IBKR ({type(exc).__name__}).") from exc
        if resp.status_code >= 400:
            raise IbkrError(f"IBKR ответил ошибкой HTTP {resp.status_code}.")
        return resp

    async def portfolio(self, token: str, query_id: str, fresh: bool = False) -> Portfolio:
        key = (token, query_id)
        hit = self._cache.get(key)
        if hit and not fresh and time.monotonic() - hit[0] < self.CACHE_TTL:
            return hit[1]
        pf = parse_statement(await self._fetch(token, query_id))
        self._cache[key] = (time.monotonic(), pf)
        return pf

    def forget(self, token: str, query_id: str) -> None:
        self._cache.pop((token, query_id), None)

    async def _fetch(self, token: str, query_id: str) -> str:
        resp = await self._get(SEND_URL, {"t": token, "q": query_id, "v": "3"})
        _, fields = _status(resp.text)
        if fields.get("Status") != "Success":
            raise IbkrError(f"IBKR: {fields.get('ErrorMessage') or 'запрос отклонён'} (код {fields.get('ErrorCode', '?')})")
        url, ref = fields.get("Url"), fields.get("ReferenceCode")
        if not url or not ref:
            raise IbkrError("IBKR не вернул ссылку на отчёт.")

        for attempt in range(10):
            await asyncio.sleep(3 if attempt == 0 else 5)
            resp = await self._get(url, {"t": token, "q": ref, "v": "3"})
            if "<FlexQueryResponse" in resp.text[:500]:
                return resp.text
            _, fields = _status(resp.text)
            if fields.get("ErrorCode") in IN_PROGRESS:
                continue
            raise IbkrError(f"IBKR: {fields.get('ErrorMessage') or 'ошибка'} (код {fields.get('ErrorCode', '?')})")
        raise IbkrError("IBKR слишком долго готовит отчёт. Попробуйте через пару минут.")

    async def close(self) -> None:
        await self._http.aclose()
