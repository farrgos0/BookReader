@echo off
chcp 65001 >nul
REM Обновление сервера BookReader. Просто запустите: проверит GitHub и скачает новую версию.
REM Без интернета — перетащите на этот файл архив BookReader_Server_vX.Y.Z_update.zip (или положите его рядом).
REM Проверяет контрольные суммы, заменяет файлы, убирает лишние; при ошибке возвращает прежнюю версию.
REM   update.bat -Check — есть ли новая версия;  -Online — только с GitHub;  -Offline — только из архива
title BookReader - обновление
set "ARGS=%*"
if "%~x1"==".zip" set "ARGS=-Archive %*"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\update.ps1" %ARGS%
pause
