"""Командная строка: python -m rental_report [--month 2026-10] [--config config.yaml] [--send]"""
import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

from .core import LUNI, SUPPORTED, detect_platform, generate, previous_month  # noqa: F401


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Отчёт Booking + Airbnb для бухгалтера (Saga)")
    ap.add_argument("--month", help="Месяц ГГГГ-ММ (по умолчанию — прошлый месяц)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--send", action="store_true", help="Отправить отчёт бухгалтеру по e-mail")
    ap.add_argument("--rates-file", help="Локальный XML BNR (если нет интернета)")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Нет файла настроек {cfg_path}. Скопируйте config.example.yaml → config.yaml")
        return 2
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    base = cfg_path.resolve().parent
    month = args.month or previous_month(date.today())
    inbox = base / cfg.get("input_dir", "inbox")
    inbox.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in inbox.rglob("*") if p.suffix.lower() in SUPPORTED)

    print(f"Отчёт за {month}. Папка с выгрузками: {inbox}")
    res = generate(cfg, files, month, base / cfg.get("output_dir", "reports") / month,
                   base / "state.json", base / ".cache", args.rates_file)
    print("\n" + res.summary())
    for w in res.warnings:
        print(f"ATENȚIE: {w}")
    for n in res.notes:
        print(f"  ⚠ {n}")
    for o in res.outputs:
        print(f"  → {o}")

    if args.send or cfg.get("email", {}).get("auto_send", False):
        if res.warnings and not cfg.get("email", {}).get("send_with_warnings", False):
            print("Письмо НЕ отправлено из-за предупреждений выше.")
            return 1
        from .mailer import send
        y, m = map(int, month.split("-"))
        body = cfg.get("email", {}).get(
            "body", "Bună ziua,\n\nVă trimit raportul de cazare pentru {luna}.\n\n{rezumat}\n\nMulțumesc!")
        send(cfg, f"Raport cazare {LUNI[m]} {y}",
             body.format(luna=f"{LUNI[m]} {y}", rezumat=res.summary()), res.outputs)
        print("Письмо бухгалтеру отправлено.")
    return 1 if res.warnings else 0


if __name__ == "__main__":
    sys.exit(main())
