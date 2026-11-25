import copy
from datetime import datetime, timezone

import pytest

from bot.db import Database
from bot.modules.weather.api import Forecast
from bot.modules.weather.notifier import WeatherNotifier
from bot.modules.weather.store import PlaceStore


def make_forecast(codes=None, probs=None, temps=None, gusts=None, current_temp=5.0):
    n = 48
    times = [f"2026-11-{25 + h // 24}T{h % 24:02d}:00" for h in range(n)]
    return Forecast({
        "timezone": "Europe/Bucharest",
        "current": {"time": "2026-11-25T08:00", "temperature_2m": current_temp, "apparent_temperature": 3,
                    "relative_humidity_2m": 70, "weather_code": 3, "wind_speed_10m": 3,
                    "wind_direction_10m": 90, "is_day": 1},
        "hourly": {"time": times,
                   "temperature_2m": temps or [5.0] * n,
                   "weather_code": codes or [3] * n,
                   "precipitation_probability": probs or [0] * n,
                   "wind_gusts_10m": gusts or [5] * n},
        "daily": {"time": ["2026-11-25", "2026-11-26"], "weather_code": [3, 3],
                  "temperature_2m_max": [6, 6], "temperature_2m_min": [1, 1],
                  "precipitation_probability_max": [0, 0],
                  "sunrise": ["2026-11-25T07:30"] * 2, "sunset": ["2026-11-25T16:40"] * 2},
    })


def test_no_alerts_in_calm_weather():
    assert make_forecast().alerts() == []


def test_alerts_detected_and_earliest_kept():
    codes = [3] * 48
    probs = [0] * 48
    codes[11], probs[11] = 95, 60     # гроза в 11:00
    codes[14], probs[14] = 65, 90     # ливень в 14:00
    codes[15], probs[15] = 65, 90
    temps = [5.0] * 48
    temps[19] = -2.0                  # заморозки к 19:00
    gusts = [5] * 48
    gusts[10] = 18
    codes[30], probs[30] = 95, 90     # гроза вне окна 12 часов — не считается
    alerts = make_forecast(codes, probs, temps, gusts).alerts()
    assert [a.key for a in alerts] == ["storm", "rain", "wind", "frost"]
    assert alerts[0].text == "⛈ Гроза около 11:00"
    assert "около 14:00" in alerts[1].text and "90%" in alerts[1].text
    assert "порывы до 18 м/с" in alerts[2].text
    assert alerts[3].text == "🥶 Заморозки: до -2° к 19:00"


def test_light_rain_is_not_alert():
    codes = [61] * 48
    probs = [60] * 48
    assert make_forecast(codes, probs).alerts() == []


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


class FakeClient:
    def __init__(self, fc):
        self.fc = fc
        self.calls = 0

    async def forecast(self, lat, lon, fresh=False):
        self.calls += 1
        return self.fc


async def test_notifier_morning_and_alerts(db):
    await db.upsert_user(1, "u", "U")
    store = PlaceStore(db)
    pid = await store.add(1, "Дом", 44.5, 26.0)
    await store.set_active(1, pid)
    await store.set_tz(pid, "Europe/Bucharest")
    await store.set_morning(1, "07:30")
    await store.toggle_alerts(1)

    codes = [3] * 48
    probs = [0] * 48
    codes[12], probs[12] = 95, 80
    sent = []

    async def send(uid, text):
        sent.append(text)

    n = WeatherNotifier(db, store, FakeClient(make_forecast(codes, probs)), send)

    # 07:00 по Бухаресту (05:00 UTC): ещё рано для утреннего, но предупреждение уже можно
    await n.tick(datetime(2026, 11, 25, 5, 0, tzinfo=timezone.utc))
    assert len(sent) == 1 and "Гроза" in sent[0] and "Непогода — Дом" in sent[0]

    # 07:31 — утренний прогноз; та же гроза повторно не приходит
    await n.tick(datetime(2026, 11, 25, 5, 31, tzinfo=timezone.utc))
    assert len(sent) == 2 and sent[1].startswith("☀️ <b>Доброе утро!</b>") and "Внимание" not in sent[1]

    # 07:40 — утренний уже был
    await n.tick(datetime(2026, 11, 25, 5, 40, tzinfo=timezone.utc))
    assert len(sent) == 2

    # ночью предупреждения не шлются
    n._last_alert_check.clear()
    await n.tick(datetime(2026, 11, 25, 21, 0, tzinfo=timezone.utc))  # 23:00 местного
    assert len(sent) == 2


async def test_legacy_location_migrated(db):
    await db.upsert_user(1, "u", "U")
    await db.execute("UPDATE users SET lat = 44.5, lon = 26.0, place = 'Отопень' WHERE id = 1")
    store = PlaceStore(db)
    await store.migrate_legacy()
    place = await store.active(1)
    assert place["name"] == "Отопень" and place["lat"] == 44.5
    await store.migrate_legacy()
    assert len(await store.list(1)) == 1
