@echo off
chcp 65001 >nul
REM Обновление сервера BookReader: перетащите на этот файл архив BookReader_Server_vX.Y.Z_update.zip
REM (или положите архив рядом и запустите). Проверяет контрольные суммы, заменяет файлы, убирает лишние.
REM   update.bat -Check  — есть ли новая версия на GitHub;  update.bat -Online — скачать и обновить
title BookReader - обновление
set "ARGS=%*"
if "%~x1"==".zip" set "ARGS=-Archive %*"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\update.ps1" %ARGS%
pause
