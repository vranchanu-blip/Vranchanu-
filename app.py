"""Точка входа для RaportCazare.exe.
Без параметров — открывается окно. С параметром --batch — режим командной строки (для проверки)."""
import sys

if __name__ == "__main__":
    if "--batch" in sys.argv:
        from rental_report.__main__ import main
        sys.exit(main([a for a in sys.argv[1:] if a != "--batch"]))
    from rental_report.gui import run
    run()
