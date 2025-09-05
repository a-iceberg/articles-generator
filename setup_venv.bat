@echo off
echo === Создание виртуального окружения .venv ===
python -m venv .venv

echo === Установка pip и зависимостей ===
call .venv\Scripts\python.exe -m pip install --upgrade pip
call .venv\Scripts\python.exe -m pip install requests

echo ✅ Venv создан и requests установлен
pause
