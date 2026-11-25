"""Погода от Open-Meteo (бесплатно, без ключа) и название места от OpenStreetMap."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

import httpx

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "PersonalTelegramBot/1.0 (weather)"

# Коды погоды WMO -> (эмодзи днём, эмодзи ночью, описание)
WMO: dict[int, tuple[str, str, str]] = {
    0: ("☀️", "🌙", "ясно"),
    1: ("🌤", "🌙", "преимущественно ясно"),
    2: ("⛅", "☁️", "переменная облачность"),
    3: ("☁️", "☁️", "пасмурно"),
    45: ("🌫", "🌫", "туман"),
    48: ("🌫", "🌫", "туман с изморозью"),
    51: ("🌦", "🌧", "лёгкая морось"),
    53: ("🌦", "🌧", "морось"),
    55: ("🌧", "🌧", "сильная морось"),
    56: ("🌧", "🌧", "ледяная морось"),
    57: ("🌧", "🌧", "сильная ледяная морось"),
    61: ("🌦", "🌧", "небольшой дождь"),
    63: ("🌧", "🌧", "дождь"),
    65: ("🌧", "🌧", "ливень"),
    66: ("🌧", "🌧", "ледяной дождь"),
    67: ("🌧", "🌧", "сильный ледяной дождь"),
    71: ("🌨", "🌨", "небольшой снег"),
    73: ("🌨", "🌨", "снег"),
    75: ("❄️", "❄️", "сильный снег"),
    77: ("🌨", "🌨", "снежная крупа"),
    80: ("🌦", "🌧", "небольшой ливень"),
    81: ("🌧", "🌧", "ливень"),
    82: ("⛈", "⛈", "сильный ливень"),
    85: ("🌨", "🌨", "снегопад"),
    86: ("❄️", "❄️", "сильный снегопад"),
    95: ("⛈", "⛈", "гроза"),
    96: ("⛈", "⛈", "гроза с градом"),
    99: ("⛈", "⛈", "сильная гроза с градом"),
}

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WIND_DIRS = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]


def icon(code: int, is_day: bool = True) -> str:
    day, night, _ = WMO.get(code, ("🌡", "🌡", ""))
    return day if is_day else night


def describe(code: int) -> str:
    return WMO.get(code, ("", "", "без данных"))[2]


def temp(value: float) -> str:
    t = round(value)
    return f"+{t}°" if t > 0 else f"{t}°"


def wind_dir(degrees: float) -> str:
    return WIND_DIRS[round(degrees / 45) % 8]


@dataclass(frozen=True)
class Forecast:
    raw: dict

    def render(self, place: str | None) -> str:
        cur, daily, hourly = self.raw["current"], self.raw["daily"], self.raw["hourly"]
        is_day = bool(cur.get("is_day", 1))
        code = int(cur["weather_code"])
        now = datetime.fromisoformat(cur["time"])

        lines = [
            f"{icon(code, is_day)} <b>{escape(place or 'Ваше местоположение')}</b>",
            "",
            f"<b>{temp(cur['temperature_2m'])}</b>  {describe(code)}",
            f"Ощущается как {temp(cur['apparent_temperature'])}",
            f"💨 {round(cur['wind_speed_10m'])} м/с, {wind_dir(cur['wind_direction_10m'])}"
            f"   💧 {round(cur['relative_humidity_2m'])}%",
            f"Сегодня {temp(daily['temperature_2m_min'][0])}…{temp(daily['temperature_2m_max'][0])}, "
            f"осадки {daily['precipitation_probability_max'][0] or 0}%",
            f"🌅 {daily['sunrise'][0][-5:]}   🌇 {daily['sunset'][0][-5:]}",
        ]

        # Ближайшие часы: каждые 3 часа, 4 отметки
        times = hourly["time"]
        start = next((i for i, t in enumerate(times) if datetime.fromisoformat(t) > now), len(times))
        hours = []
        for i in range(start, min(start + 12, len(times)), 3):
            hour = datetime.fromisoformat(times[i]).hour
            rain = hourly["precipitation_probability"][i] or 0
            mark = f" {rain}%" if rain >= 30 else ""
            hours.append(
                f"<code>{hour:02d}:00</code> {icon(int(hourly['weather_code'][i]), 7 <= hour < 19)} "
                f"{temp(hourly['temperature_2m'][i])}{mark}"
            )
        if hours:
            lines += ["", "<b>Ближайшие часы</b>", *hours]

        lines += ["", "<b>Прогноз</b>"]
        for i in range(1, len(daily["time"])):
            day = datetime.fromisoformat(daily["time"][i])
            rain = daily["precipitation_probability_max"][i] or 0
            lines.append(
                f"<code>{WEEKDAYS[day.weekday()]} {day:%d.%m}</code> {icon(int(daily['weather_code'][i]))} "
                f"{temp(daily['temperature_2m_min'][i])}…{temp(daily['temperature_2m_max'][i])}"
                + (f"  ☔ {rain}%" if rain >= 30 else "")
            )
        lines += ["", f"<i>Обновлено в {now:%H:%M} (местное время)</i>"]
        return "\n".join(lines)


class WeatherClient:
    def __init__(self, timeout: float = 20.0):
        self._http = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT})

    async def forecast(self, lat: float, lon: float) -> Forecast:
        resp = await self._http.get(FORECAST_URL, params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,"
                       "wind_speed_10m,wind_direction_10m,is_day",
            "hourly": "temperature_2m,weather_code,precipitation_probability",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,sunrise,sunset",
            "wind_speed_unit": "ms",
            "timezone": "auto",
            "forecast_days": 4,
        })
        resp.raise_for_status()
        return Forecast(resp.json())

    async def place_name(self, lat: float, lon: float) -> str | None:
        """Город/село по координатам. Ошибка не критична — тогда покажем без названия."""
        try:
            resp = await self._http.get(REVERSE_URL, params={
                "lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10, "accept-language": "ru",
            })
            resp.raise_for_status()
            addr = resp.json().get("address", {})
        except (httpx.HTTPError, ValueError):
            return None
        city = next((addr[k] for k in ("city", "town", "village", "municipality", "county") if addr.get(k)), None)
        country = addr.get("country")
        return ", ".join(x for x in (city, country) if x) or None

    async def close(self) -> None:
        await self._http.aclose()
