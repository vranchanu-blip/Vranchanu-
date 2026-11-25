"""Фоновые уведомления: утренний прогноз и предупреждения о непогоде."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bot.db import Database
from bot.modules.weather.api import WeatherClient
from bot.modules.weather.store import DEFAULT_TZ, PlaceStore

log = logging.getLogger(__name__)

Send = Callable[[int, str], Awaitable[None]]

MORNING_WINDOW = timedelta(hours=2)   # если бот был выключен в нужное время — догоняем 2 часа
ALERT_EVERY = 30 * 60                 # проверка непогоды раз в 30 минут
QUIET_FROM, QUIET_TO = 22, 7          # ночью предупреждения не шлём


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


class WeatherNotifier:
    def __init__(self, db: Database, store: PlaceStore, client: WeatherClient, send: Send):
        self.db = db
        self.store = store
        self.client = client
        self.send = send
        self._last_alert_check: dict[int, float] = {}

    async def run_forever(self) -> None:
        await self.store.migrate_legacy()
        while True:
            try:
                await self.tick(datetime.now(timezone.utc))
            except Exception:  # noqa: BLE001 — цикл не должен падать
                log.exception("weather notifier tick failed")
            await asyncio.sleep(60)

    async def tick(self, now: datetime) -> None:
        for s in await self.store.subscribers():
            try:
                await self._handle(s, now)
            except Exception:  # noqa: BLE001
                log.exception("weather notify for %s failed", s["user_id"])

    async def _handle(self, s, now: datetime) -> None:
        local = now.astimezone(zone(s["tz"]))
        today = local.date().isoformat()

        if s["morning_time"] and s["morning_sent"] != today:
            hh, mm = map(int, s["morning_time"].split(":"))
            target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= local < target + MORNING_WINDOW:
                await self._morning(s, today)
                return

        quiet = local.hour >= QUIET_FROM or local.hour < QUIET_TO
        last = self._last_alert_check.get(s["user_id"])
        if s["alerts"] and not quiet and (last is None or time.monotonic() - last >= ALERT_EVERY):
            self._last_alert_check[s["user_id"]] = time.monotonic()
            fc = await self.client.forecast(s["lat"], s["lon"])
            await self.store.set_tz(s["place_id"], fc.tz)
            fresh = await self._unsent(s, today, fc.alerts())
            if fresh:
                await self.send(
                    s["user_id"],
                    f"⚠️ <b>Непогода — {s['name']}</b>\n\n" + "\n".join(a.text for a in fresh),
                )

    async def _morning(self, s, today: str) -> None:
        fc = await self.client.forecast(s["lat"], s["lon"], fresh=True)
        await self.store.set_tz(s["place_id"], fc.tz)
        text = "☀️ <b>Доброе утро!</b> Погода на сегодня\n\n" + fc.render(s["name"])
        if s["alerts"]:
            fresh = await self._unsent(s, today, fc.alerts())
            if fresh:
                text += "\n\n⚠️ <b>Внимание</b>\n" + "\n".join(a.text for a in fresh)
        await self.send(s["user_id"], text)
        await self.store.mark_morning(s["user_id"], today)

    async def _unsent(self, s, today: str, alerts) -> list:
        """Каждый тип предупреждения — не чаще раза в день для места."""
        fresh = []
        for a in alerts:
            key = f"alert:{s['user_id']}:{s['place_id']}:{a.key}:{today}"
            if await self.db.get_kv(key) is None:
                await self.db.set_kv(key, "1")
                fresh.append(a)
        return fresh
