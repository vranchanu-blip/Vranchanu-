@echo off
chcp 65001 >nul
REM Двойной щелчок: отчёт за прошлый месяц. Другой месяц: run_report.bat --month 2026-09
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% -m rental_report %*
echo.
echo Готовые файлы лежат в папке reports
pause
