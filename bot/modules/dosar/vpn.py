"""VPN для запросов к ANC через Xray.

Сайт ANC не отвечает облачным серверам. Владелец отправляет боту JSON-настройку Xray
(например, из приложения VPN); бот скачивает Xray, запускает его локально и ходит на ANC
через его HTTP/SOCKS-вход на 127.0.0.1. Остальной трафик бота идёт напрямую.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import platform
import stat
import zipfile
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

XRAY_URL = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-{arch}.zip"
_ARCH = {"x86_64": "64", "amd64": "64", "aarch64": "arm64-v8a", "arm64": "arm64-v8a"}
_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", " ": " "})


class VpnConfigError(ValueError):
    pass


def parse_config(raw: str) -> dict:
    """Разобрать JSON Xray (с исправлением «умных» кавычек) и проверить, что он пригоден."""
    text = raw.translate(_QUOTES).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise VpnConfigError("Не нашёл JSON-настройку (текст должен начинаться с «{»).")
    try:
        cfg = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise VpnConfigError(f"JSON повреждён: {exc.msg}, строка {exc.lineno}.") from exc
    if not isinstance(cfg, dict) or not cfg.get("outbounds"):
        raise VpnConfigError("В настройке нет раздела «outbounds» — это не конфигурация Xray.")
    proxy_url(cfg)  # проверка, что есть локальный вход
    return cfg


def proxy_url(cfg: dict) -> str:
    """Адрес локального HTTP-входа Xray (SOCKS не используем: httpx нужен доп. пакет)."""
    for inbound in cfg.get("inbounds") or []:
        if inbound.get("protocol") != "http":
            continue
        host = inbound.get("listen") or "127.0.0.1"
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = inbound.get("port")
        accounts = (inbound.get("settings") or {}).get("accounts") or []
        auth = ""
        if accounts:
            a = accounts[0]
            auth = f"{quote(str(a.get('user', '')), safe='')}:{quote(str(a.get('pass', '')), safe='')}@"
        return f"http://{auth}{host}:{port}"
    raise VpnConfigError("В настройке нет HTTP-входа (inbound с protocol «http»).")


class XrayManager:
    def __init__(self, data_dir: str):
        self.dir = os.path.abspath(data_dir)
        self.bin = os.path.join(self.dir, "bin", "xray")
        self.config_path = os.path.join(self.dir, "xray.json")
        self.log_path = os.path.join(self.dir, "xray.log")
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def configured(self) -> bool:
        return os.path.exists(self.config_path)

    def load(self) -> dict | None:
        if not self.configured:
            return None
        with open(self.config_path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, cfg: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        fd = os.open(self.config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def remove(self) -> None:
        if self.configured:
            os.remove(self.config_path)

    async def ensure_binary(self) -> None:
        if os.path.exists(self.bin):
            return
        arch = _ARCH.get(platform.machine().lower())
        if not arch:
            raise RuntimeError(f"Неизвестная архитектура: {platform.machine()}")
        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as http:
            resp = await http.get(XRAY_URL.format(arch=arch))
            resp.raise_for_status()
        os.makedirs(os.path.dirname(self.bin), exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for name in zf.namelist():
                if name.endswith((".dat",)) or name == "xray":
                    target = os.path.join(os.path.dirname(self.bin), os.path.basename(name))
                    with open(target, "wb") as out:
                        out.write(zf.read(name))
        os.chmod(self.bin, os.stat(self.bin).st_mode | stat.S_IXUSR)

    async def start(self) -> str:
        """(Пере)запустить Xray с сохранённой настройкой. Возвращает адрес прокси."""
        cfg = self.load()
        if cfg is None:
            raise VpnConfigError("VPN не настроен.")
        await self.stop()
        await self.ensure_binary()
        logf = open(self.log_path, "ab")
        self._proc = await asyncio.create_subprocess_exec(
            self.bin, "run", "-c", self.config_path,
            stdout=logf, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "XRAY_LOCATION_ASSET": os.path.dirname(self.bin)},
        )
        logf.close()
        await asyncio.sleep(2)
        if self._proc.returncode is not None:
            raise RuntimeError(f"Xray не запустился, код {self._proc.returncode}. {self.tail_log()}")
        return proxy_url(cfg)

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

    @property
    def running(self) -> bool:
        return bool(self._proc and self._proc.returncode is None)

    def tail_log(self, lines: int = 5) -> str:
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-lines:]).strip()
        except OSError:
            return ""
