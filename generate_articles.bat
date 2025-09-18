@echo off
chcp 65001 >nul
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION

pushd "%~dp0"

rem === Поиск Python ===
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
  where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
  echo [ОШИБКА] Не найден Python 3.10+ в PATH
  goto :halt
)

rem === Виртуальное окружение ===
if not exist ".venv\Scripts\python.exe" (
  echo == Создаю venv...
  %PY% -m venv .venv || (echo [ОШИБКА] Не удалось создать .venv & goto :halt)
)

set "VENV_PY=.venv\Scripts\python.exe"
echo == Проверяю зависимости...
"%VENV_PY%" -m pip install --upgrade pip >nul
"%VENV_PY%" -m pip install requests >nul || (echo [ОШИБКА] Не удалось установить зависимости & goto :halt)

rem === Выбор CSV ===
set "DEFAULT=iceberg.csv"
if exist "%DEFAULT%" (
  echo Найден файл %DEFAULT%, он будет использован по умолчанию.
  set /p INPUT_FILE=Введите путь к другому CSV или нажмите Enter для значения по умолчанию: 
  if not defined INPUT_FILE set "INPUT_FILE=%DEFAULT%"
) else (
  set /p INPUT_FILE=Укажите путь к CSV: 
)

rem === Диапазон групп ===
set /p START=С какой группы начать (0):
if "%START%"=="" set "START=0"
set /p END=До какой группы (не включительно). Оставьте пустым, чтобы до конца:
if "%END%"=="" set "END=None"

echo == Запуск client_stream.py ...
if /i "%END%"=="None" (
  "%VENV_PY%" client_stream.py --input-file "%INPUT_FILE%" --start %START%
) else (
  "%VENV_PY%" client_stream.py --input-file "%INPUT_FILE%" --start %START% --end %END%
)

echo.
echo Готово. Нажмите клавишу для выхода...
pause >nul
goto :eof

:halt
echo.
echo Нажмите клавишу для выхода...
pause >nul
