"""Реестр модулей экосистемы. Каждый модуль = кнопка в главном меню + свой Router.

Чтобы добавить модуль (портфель, напоминания и т.д.):
  1. создать пакет bot/modules/<name>/ с функцией setup(ctx) -> Module;
  2. добавить его в MODULES в bot/__main__.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aiogram import Bot, Router

from bot.config import Settings
from bot.db import Database


@dataclass
class AppContext:
    settings: Settings
    db: Database
    bot: Bot
    background: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    shutdown: list[Callable[[], Awaitable[None]]] = field(default_factory=list)


@dataclass(frozen=True)
class Module:
    key: str            # используется в callback_data: "m:<key>"
    title: str          # текст кнопки в главном меню
    router: Router
    enabled: bool = True
