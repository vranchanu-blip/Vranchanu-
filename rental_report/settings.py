"""Где программа хранит настройки и куда кладёт отчёты (для версии с окном)."""
import copy
import json
import os
import sys
from pathlib import Path

import yaml

DEFAULTS = {
    "company": {"name": "", "cif": "", "reg_com": "", "address": "", "bank": "", "iban": "", "capital": ""},
    "date_basis": "platform",
    "bnr_mode": "same_day",
    "airbnb_currency": "RON",
    "booking_currency": "EUR",
    "include_cancelled_with_fee": True,
    "archive_inputs": True,
    "vat_rate": 0,
    "saga": {"enabled": True, "mode": "per_reservation", "series": "CAZ", "next_number": 1,
             "article_code": "CAZARE", "unit": "noapte", "client_cif": "0000000000000"},
}


def app_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "RaportCazare"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = Path.home()
    d = docs / "Raport cazare"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def config_path() -> Path:
    return app_dir() / "settings.yaml"


def state_path() -> Path:
    return app_dir() / "state.json"


def load() -> dict:
    p = config_path()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _merge(DEFAULTS, data or {})


def save(cfg: dict) -> None:
    config_path().write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("company", {}).get("name"))


def next_number(cfg: dict) -> int:
    p = state_path()
    if p.exists():
        return int(json.loads(p.read_text()).get("next_number", cfg["saga"]["next_number"]))
    return int(cfg["saga"]["next_number"])


def set_next_number(n: int) -> None:
    p = state_path()
    state = json.loads(p.read_text()) if p.exists() else {}
    state["next_number"] = int(n)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False))
