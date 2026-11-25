from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.modules.tasks.parse import describe_repeat, next_due, parse

TZ = ZoneInfo("Europe/Bucharest")
NOW = datetime(2026, 11, 25, 15, 30, tzinfo=TZ)   # среда


def at(*args):
    return datetime(*args, tzinfo=TZ)


@pytest.mark.parametrize("text, due, body, repeat", [
    ("напомни завтра в 9 позвонить в банк", at(2026, 11, 26, 9, 0), "Позвонить в банк", None),
    ("Напомни мне через 30 минут выключить духовку", at(2026, 11, 25, 16, 0), "Выключить духовку", None),
    ("через 2 часа забрать посылку", at(2026, 11, 25, 17, 30), "Забрать посылку", None),
    ("через час позвонить", at(2026, 11, 25, 16, 30), "Позвонить", None),
    ("через полчаса чай", at(2026, 11, 25, 16, 0), "Чай", None),
    ("через 3 дня оплатить счёт", at(2026, 11, 28, 15, 30), "Оплатить счёт", None),
    ("25.12 в 10 поздравить маму", at(2026, 12, 25, 10, 0), "Поздравить маму", None),
    ("1 января в 00:00 с новым годом", at(2027, 1, 1, 0, 0), "С новым годом", None),
    ("в 18:45 выйти из дома", at(2026, 11, 25, 18, 45), "Выйти из дома", None),
    ("в 9 утра зарядка", at(2026, 11, 26, 9, 0), "Зарядка", None),
    ("в 3 дня совещание", at(2026, 11, 26, 15, 0), "Совещание", None),
    ("в 8 вечера кино", at(2026, 11, 25, 20, 0), "Кино", None),
    ("в пятницу вечером позвонить Ивану", at(2026, 11, 27, 20, 0), "Позвонить Ивану", None),
    ("в среду в 10 стрижка", at(2026, 12, 2, 10, 0), "Стрижка", None),   # среда сегодня, 10:00 прошло
    ("каждый день в 8:00 витамины", at(2026, 11, 26, 8, 0), "Витамины", "daily"),
    ("каждый понедельник в 8 вынести мусор", at(2026, 11, 30, 8, 0), "Вынести мусор", "weekly:0"),
    ("по будням в 7:30 будильник", at(2026, 11, 26, 7, 30), "Будильник", "weekdays"),
    ("каждый месяц 1-го в 10 оплатить квартиру", at(2026, 12, 1, 10, 0), "Оплатить квартиру", "monthly:1"),
    ("каждое 31 число зарплата", at(2026, 11, 30, 9, 0), "Зарплата", "monthly:31"),
])
def test_parse(text, due, body, repeat):
    p = parse(text, NOW)
    assert (p.due, p.body, p.repeat) == (due, body, repeat)


def test_without_time():
    p = parse("купить молоко", NOW)
    assert p.due is None and p.body == "Купить молоко"


def test_bad_date():
    assert parse("31.02 в 10 что-то", NOW).due is None


def test_today_past_time_moves_to_tomorrow():
    assert parse("в 9 зарядка", NOW).due == at(2026, 11, 26, 9, 0)


def test_next_due():
    assert next_due(at(2026, 11, 27, 7, 30), "weekdays", at(2026, 11, 27, 8, 0)) == at(2026, 11, 30, 7, 30)
    assert next_due(at(2026, 1, 31, 9, 0), "monthly:31", at(2026, 1, 31, 9, 0)) == at(2026, 2, 28, 9, 0)
    assert next_due(at(2026, 11, 30, 8, 0), "weekly:0", at(2026, 11, 30, 8, 0)) == at(2026, 12, 7, 8, 0)
    assert describe_repeat("weekly:2") == "каждую среду" and describe_repeat("monthly:5") == "каждый месяц 5-го"
