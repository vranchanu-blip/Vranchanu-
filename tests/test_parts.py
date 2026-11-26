from datetime import datetime, timedelta, timezone

import httpx
from aiogram.methods import SendMessage, SendPhoto

from bot.modules.parts import ebay as ebay_mod
from bot.modules.parts.ebay import EbayClient, condition_ru, flag, parse_items
from tests.test_bot_flow import env  # noqa: F401

NOW = datetime.now(timezone.utc)


def summary(item_id, title, price, country="DE", currency="EUR", cond="3000", days_ago=1):
    return {
        "itemId": f"v1|{item_id}|0", "legacyItemId": item_id, "title": title,
        "price": {"value": str(price), "currency": currency},
        "itemWebUrl": f"https://www.ebay.de/itm/{item_id}", "image": {"imageUrl": f"https://img/{item_id}.jpg"},
        "itemLocation": {"country": country}, "conditionId": cond,
        "shippingOptions": [{"shippingCost": {"value": "0.00", "currency": currency}}],
        "itemCreationDate": (NOW - timedelta(days=days_ago)).isoformat(),
    }


def test_helpers():
    assert flag("DE") == "🇩🇪" and condition_ru("1000") == "новое" and condition_ru("3000") == "б/у"
    items = parse_items({"itemSummaries": [summary("1", "Harley seat", 100, "GB", "GBP")]}, 0.85)
    assert round(items[0].price_eur) == 118 and items[0].shipping == "бесплатно"


def handler_factory(calls):
    def handler(request):
        if request.url.path.endswith("/token"):
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        mp = request.headers["X-EBAY-C-MARKETPLACE-ID"]
        calls.append((mp, request.url.params["q"], request.url.params["filter"]))
        if mp != "EBAY_DE":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"itemSummaries": [
            summary("1", "Harley Davidson Heritage FLHCS Vance & Hines exhaust", 650, days_ago=0),
            summary("2", "Harley Softail M8 solo seat", 220, days_ago=1),
            summary("3", "Maisto 1:12 Harley Heritage model", 40),          # игрушка
            summary("4", "Harley Heritage saddlebags", 900, country="US"),  # не Европа
            summary("5", "Harley FLDE windshield", 180, days_ago=30),       # старое
            summary("6", "Universal phone holder", 30),                     # не про Harley
            summary("7", "Harley Heritage LED headlight", 150, days_ago=2),
            summary("8", "Harley Heritage chrome mirrors", 60, days_ago=3),
            summary("9", "Harley Heritage foot boards", 120, days_ago=4),
        ]})
    return handler


async def test_search_europe_filters():
    calls = []
    c = EbayClient("id", "secret")
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler_factory(calls)))
    items = await c.search_europe(["Harley FLHCS"], 20, 5000)
    ids = [i.item_id for i in items]
    assert "3" not in ids and "4" not in ids and "6" not in ids
    assert ids[0] == "1"
    assert len(calls) == len(ebay_mod.MARKETPLACES)
    gb = next(f for mp, _, f in calls if mp == "EBAY_GB")
    assert "priceCurrency:GBP" in gb and "price:[17..4250]" in gb
    await c.close()


async def test_parts_flow(env, monkeypatch):  # noqa: F811
    msg, cb, db, session = env
    calls = []
    real_init = EbayClient.__init__

    def patched_init(self, *a, **k):
        real_init(self, *a, **k)
        self._http = httpx.AsyncClient(transport=httpx.MockTransport(handler_factory(calls)))

    monkeypatch.setattr(EbayClient, "__init__", patched_init)

    r = await cb("m:parts")
    assert "eBay ещё не подключён" in r.text and "19:09" in r.text and "Harley FLHCS" in r.text

    await cb("pt:keys")
    r = await msg("short")
    assert "Не похоже" in r.text
    await msg("MyApp-PRD-1234567890")
    r = await msg("PRD-secret-0987654321")
    assert "eBay подключён" in r.text

    await cb("pt:price")
    r = await msg("50-1000")
    assert "от 50 до 1 000 €" in r.text
    await cb("pt:queries")
    r = await msg("Harley FLHCS\nHarley FLDE")
    assert "• Harley FLDE" in r.text and "Softail M8" not in r.text

    before = len(session.calls)
    await cb("pt:now")
    photos = [c for c in session.calls[before:] if isinstance(c, SendPhoto)]
    assert len(photos) == 4
    assert "Vance &amp; Hines" in photos[0].caption and "Германия" in photos[0].caption
    assert all("900" not in p.caption for p in photos)

    # следующая подборка не повторяет показанное
    before = len(session.calls)
    await cb("pt:now")
    photos2 = [c for c in session.calls[before:] if isinstance(c, SendPhoto)]
    assert not {p.caption for p in photos} & {p.caption for p in photos2}

    # расписание: 19:09 по Бухаресту = 17:09 UTC
    tick = session.ctx.state["parts.tick"]
    await db.execute("DELETE FROM parts_seen")
    day = datetime(2026, 11, 26, tzinfo=timezone.utc)
    before = len(session.calls)
    await tick(day.replace(hour=17, minute=0))
    assert len(session.calls) == before
    await tick(day.replace(hour=17, minute=10))
    sent = session.calls[before:]
    assert any(isinstance(c, SendMessage) and "на сегодня" in c.text for c in sent)
    before = len(session.calls)
    await tick(day.replace(hour=17, minute=20))
    assert len(session.calls) == before
