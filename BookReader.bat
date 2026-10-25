@echo off
chcp 65001 >nul
setlocal
REM BookReader: если сервер уже запущен — просто открываем читалку в браузере,
REM иначе запускаем сервер озвучки (он сам откроет браузер).
REM Если чего-то не хватает (Python, библиотеки) — предлагаем запустить установщик, а не падаем с ошибкой.
cd /d "%~dp0"

REM свой Python от установщика (install.bat), иначе — системный
set "PY="
if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  set "MISSING=Python"
  goto need_install
)

REM нужные библиотеки на месте? Быстрая проверка без загрузки torch: Python сам смотрит модули и возвращает код
REM (0 — всё есть, 1 — чего-то нет: список — в файле). В тестовом режиме --fake нужен только numpy.
set "MODS=numpy torch omegaconf scipy"
if /i "%~1"=="--fake" set "MODS=numpy"
set "MISSING="
set "CHK=%TEMP%\bookreader-missing.txt"
"%PY%" -c "import sys, importlib.util as u; m=[n for n in sys.argv[1:] if not u.find_spec(n)]; print(', '.join(m)); sys.exit(1 if m else 0)" %MODS% > "%CHK%" 2>nul
if errorlevel 1 goto missing_mods
goto mods_ok
:missing_mods
if exist "%CHK%" set /p MISSING=<"%CHK%"
if not defined MISSING set "MISSING=Python не запускается"
goto need_install
:mods_ok

REM сервер уже работает — открываем читалку и выходим
REM (порт и прочие настройки — в server.json в папке данных: %%LOCALAPPDATA%%\BookReader)
"%PY%" server.py --open-if-running %* >nul 2>nul
if %errorlevel%==0 exit /b

title BookReader - TTS server
REM кодировщик Opus для приложения на телефоне (ставится один раз)
"%PY%" -c "import shutil,sys,importlib.util as u; sys.exit(0 if shutil.which('ffmpeg') or u.find_spec('imageio_ffmpeg') else 1)"
if errorlevel 1 (
  echo Устанавливаю кодировщик Opus: imageio-ffmpeg, около 30 МБ, только один раз ...
  "%PY%" -m pip install --progress-bar on imageio-ffmpeg
)
"%PY%" server.py %*
if errorlevel 1 (
  echo.
  echo Сервер остановился с ошибкой. Подробности — выше и в журнале: %LOCALAPPDATA%\BookReader\logs\server.log
  echo Если не хватает компонентов — запустите install.bat ещё раз: он доустановит недостающее.
)
pause
exit /b

:need_install
echo.
if "%MISSING%"=="Python" (
  echo  BookReader ещё не установлен на этом компьютере.
  echo  Установщик поставит свой Python, библиотеки и модель голосов.
) else (
  echo  Не хватает компонентов BookReader: %MISSING%.
  echo  Установщик доустановит только недостающее — уже установленное заново не скачивается.
)
echo.
set "ANS="
set /p "ANS=Запустить установщик сейчас? Enter - да, N и Enter - нет: "
if defined ANS (
  echo Установка отменена. Запустить её можно в любой момент: install.bat в папке BookReader.
  pause
  exit /b 1
)
call "%~dp0install.bat"
exit /b
