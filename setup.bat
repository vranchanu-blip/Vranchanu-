@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Установите его с https://www.python.org/downloads/
  echo и при установке отметьте галочку "Add Python to PATH".
  pause
  exit /b 1
)
echo Устанавливаю нужные библиотеки...
%PY% -m pip install -r requirements.txt
if not exist config.yaml copy config.example.yaml config.yaml >nul
if not exist inbox mkdir inbox
echo.
echo Готово! Откройте config.yaml в Блокноте и впишите данные фирмы.
echo Затем положите файлы Airbnb и Booking в папку inbox и запустите run_report.bat
pause
