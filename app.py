"""Точка входа для RaportCazare.exe.
Без параметров — открывается окно. С параметром --batch — режим без окна (для проверки сборки)."""
import sys
import traceback
from pathlib import Path


def batch() -> int:
    args = [a for a in sys.argv[1:] if a != "--batch"]
    log = None
    if "--config" in args:
        log = Path(args[args.index("--config") + 1]).resolve().parent / "batch.log"
    try:
        if log:  # в exe без консоли print() никуда не пишет — сохраняем вывод в файл
            sys.stdout = sys.stderr = open(log, "w", encoding="utf-8")
        from rental_report.__main__ import main
        return main(args)
    except SystemExit as e:
        return int(e.code or 0)
    except Exception:  # noqa: BLE001 — не показывать окно с ошибкой, а записать её
        traceback.print_exc()
        return 3
    finally:
        if log:
            sys.stdout.flush()


if __name__ == "__main__":
    if "--batch" in sys.argv:
        sys.exit(batch())
    from rental_report.gui import run
    run()
