@echo off
chcp 65001 >nul
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION

pushd "%~dp0"

REM --- Python / venv ---
where python >nul 2>&1 && (set "PY=python") || (where py >nul 2>&1 && (set "PY=py") || (echo [ОШИБКА] Не найден Python & goto :halt))

if not exist ".venv\Scripts\python.exe" (
  echo == Создаю venv...
  %PY% -m venv .venv || (echo [ОШИБКА] Не удалось создать .venv & goto :halt)
)

set "VENV_PY=.venv\Scripts\python.exe"
echo == Проверяю зависимости...
call "%VENV_PY%" -m pip install --upgrade pip requests >nul 2>&1

REM --- CSV ---
if exist "iceberg.csv" (
  set "INPUT_FILE=iceberg.csv"
  echo Найден файл iceberg.csv, он будет использован по умолчанию.
  set /p INPUT_FILE=Введите путь к другому CSV или нажмите Enter для значения по умолчанию:
  if "!INPUT_FILE!"=="" set "INPUT_FILE=iceberg.csv"
) else (
  set /p INPUT_FILE=Укажите путь к CSV:
)

REM --- диапазоны ---
set /p START=С какой группы начать (0):
if "!START!"=="" set "START=0"

set /p END=До какой группы (не включительно). Оставьте пустым, чтобы до конца:

REM --- запуск ---
echo == Запуск client_stream.py ...
if "!END!"=="" (
  call "%VENV_PY%" client_stream.py --input-file "!INPUT_FILE!" --start !START!
) else (
  call "%VENV_PY%" client_stream.py --input-file "!INPUT_FILE!" --start !START! --end !END!
)


echo.
echo Готово. Нажмите клавишу для выхода...
pause >nul
goto :eof

:halt
echo.
echo Нажмите клавишу для выхода...
pause >nul
