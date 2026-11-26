"""Поиск объявлений через официальный eBay Browse API (ключи разработчика пользователя)."""
from __future__ import annotations

import asyncio
import base64
import re
import time
from dataclasses import dataclass

import httpx

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"

# Европейские площадки eBay: (marketplace, валюта, курс к EUR ≈ сколько единиц за 1 €)
MARKETPLACES = [
    ("EBAY_DE", "EUR", 1.0), ("EBAY_GB", "GBP", 0.85), ("EBAY_IT", "EUR", 1.0), ("EBAY_FR", "EUR", 1.0),
    ("EBAY_ES", "EUR", 1.0), ("EBAY_NL", "EUR", 1.0), ("EBAY_AT", "EUR", 1.0), ("EBAY_BE", "EUR", 1.0),
    ("EBAY_IE", "EUR", 1.0), ("EBAY_PL", "PLN", 4.3), ("EBAY_CH", "CHF", 0.95),
]
EUROPE = {
    "AL", "AD", "AT", "BA", "BE", "BG", "BY", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GR",
    "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU", "LV", "MC", "MD", "ME", "MK", "MT", "NL", "NO", "PL", "PT",
    "RO", "RS", "SE", "SI", "SK", "SM", "UA", "VA",
}
COUNTRY_RU = {
    "DE": "Германия", "GB": "Великобритания", "IT": "Италия", "FR": "Франция", "ES": "Испания", "NL": "Нидерланды",
    "AT": "Австрия", "BE": "Бельгия", "IE": "Ирландия", "PL": "Польша", "CH": "Швейцария", "RO": "Румыния",
    "CZ": "Чехия", "PT": "Португалия", "SE": "Швеция", "DK": "Дания", "HU": "Венгрия", "GR": "Греция",
    "BG": "Болгария", "SK": "Словакия", "SI": "Словения", "HR": "Хорватия", "LT": "Литва", "LV": "Латвия",
    "EE": "Эстония", "FI": "Финляндия", "NO": "Норвегия", "LU": "Люксембург", "MD": "Молдова", "UA": "Украина",
}
# не запчасти: игрушки, одежда, сувениры
JUNK = re.compile(
    r"\b(maisto|1:\d{1,2}|modell?|model kit|spielzeug|toy|poster|t-?shirt|shirt|hoodie|jacke|jacket|"
    r"patch|sticker|aufkleber|keychain|schl[üu]sselanh[äa]nger|mug|tasse|book|buch|manual download)\b",
    re.IGNORECASE,
)
RELEVANT = re.compile(r"harley|\bh-?d\b|softail|flhc|flde", re.IGNORECASE)
DEFAULT_QUERIES = [
    "Harley Heritage Softail 2018",
    "Harley FLHCS",
    "Harley FLHC Heritage",
    "Harley FLDE Deluxe",
    "Harley Softail M8",
]


class EbayError(Exception):
    pass


@dataclass(frozen=True)
class Item:
    item_id: str
    title: str
    price: float
    currency: str
    price_eur: float
    url: str
    image: str | None
    country: str | None
    condition: str
    shipping: str | None
    created: str


def flag(country: str | None) -> str:
    if not country or len(country) != 2:
        return "🌍"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country.upper())


def condition_ru(cond_id: str | None) -> str:
    try:
        c = int(cond_id or 0)
    except ValueError:
        return "—"
    if c in (1000, 1500, 1750):
        return "новое"
    if 2000 <= c < 3000:
        return "восстановлено"
    if c == 7000:
        return "на запчасти"
    return "б/у" if c else "—"


def parse_items(payload: dict, rate: float) -> list[Item]:
    items = []
    for s in payload.get("itemSummaries") or []:
        price = s.get("price") or {}
        try:
            value = float(price.get("value"))
        except (TypeError, ValueError):
            continue
        ship = None
        opts = s.get("shippingOptions") or []
        if opts:
            cost = (opts[0].get("shippingCost") or {})
            if cost.get("value") not in (None, ""):
                ship = "бесплатно" if float(cost["value"]) == 0 else f"{float(cost['value']):.2f} {cost.get('currency', '')}"
        items.append(Item(
            item_id=s.get("legacyItemId") or s.get("itemId", ""),
            title=s.get("title", ""),
            price=value,
            currency=price.get("currency", ""),
            price_eur=value / rate,
            url=s.get("itemWebUrl", ""),
            image=(s.get("image") or {}).get("imageUrl"),
            country=(s.get("itemLocation") or {}).get("country"),
            condition=condition_ru(s.get("conditionId")),
            shipping=ship,
            created=s.get("itemCreationDate") or "",
        ))
    return items


class EbayClient:
    def __init__(self, client_id: str, client_secret: str, timeout: float = 30.0):
        self._auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._http = httpx.AsyncClient(timeout=timeout)
        self._token: tuple[str, float] | None = None

    async def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token[1]:
            return self._token[0]
        try:
            resp = await self._http.post(
                TOKEN_URL,
                headers={"Authorization": f"Basic {self._auth}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials", "scope": SCOPE},
            )
        except httpx.HTTPError as exc:
            raise EbayError(f"Нет связи с eBay ({type(exc).__name__}).") from exc
        if resp.status_code != 200:
            raise EbayError("eBay не принял ключи (проверьте App ID и Cert ID, раздел Production).")
        data = resp.json()
        self._token = (data["access_token"], time.monotonic() + int(data.get("expires_in", 7200)) - 120)
        return self._token[0]

    async def search(self, query: str, marketplace: str, currency: str, rate: float,
                     min_eur: float, max_eur: float, limit: int = 50) -> list[Item]:
        token = await self._get_token()
        flt = (f"price:[{min_eur * rate:.0f}..{max_eur * rate:.0f}],priceCurrency:{currency},"
               "buyingOptions:{FIXED_PRICE|AUCTION|BEST_OFFER}")
        try:
            resp = await self._http.get(
                SEARCH_URL,
                params={"q": query, "filter": flt, "sort": "newlyListed", "limit": str(limit)},
                headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace},
            )
        except httpx.HTTPError as exc:
            raise EbayError(f"Нет связи с eBay ({type(exc).__name__}).") from exc
        if resp.status_code == 401:
            self._token = None
            raise EbayError("eBay отклонил токен, попробую позже.")
        if resp.status_code >= 400:
            raise EbayError(f"eBay ответил ошибкой HTTP {resp.status_code}.")
        return parse_items(resp.json(), rate)

    async def search_europe(self, queries: list[str], min_eur: float, max_eur: float) -> list[Item]:
        """Все запросы по всем европейским площадкам; только товары, которые находятся в Европе."""
        sem = asyncio.Semaphore(4)

        async def one(q: str, mp: tuple[str, str, float]) -> list[Item]:
            async with sem:
                return await self.search(q, mp[0], mp[1], mp[2], min_eur, max_eur)

        results = await asyncio.gather(*(one(q, mp) for q in queries for mp in MARKETPLACES),
                                       return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        if errors and len(errors) == len(results):
            raise errors[0] if isinstance(errors[0], EbayError) else EbayError(str(errors[0]))
        seen: dict[str, Item] = {}
        for r in results:
            if isinstance(r, Exception):
                continue
            for it in r:
                if it.country not in EUROPE or JUNK.search(it.title) or not RELEVANT.search(it.title):
                    continue
                seen.setdefault(it.item_id, it)
        return sorted(seen.values(), key=lambda i: i.created, reverse=True)

    async def close(self) -> None:
        await self._http.aclose()
