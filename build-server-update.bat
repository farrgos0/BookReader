@echo off
chcp 65001 >nul
REM Архив-обновление сервера: dist\BookReader_Server_vX.Y.Z_update.zip — только программа, поверх установленной
REM Параметры: -Bump patch^|minor^|major^|same  -Version X.Y.Z  -Notes "что нового"  -SkipTests
title BookReader - архив-обновление сервера
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\build-server.ps1" -Kind update %*
pause
