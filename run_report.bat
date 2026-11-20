@echo off
REM Двойной щелчок — отчёт за прошлый месяц. Для отправки бухгалтеру: run_report.bat --send
cd /d "%~dp0"
python -m rental_report %*
pause
