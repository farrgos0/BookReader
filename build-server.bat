@echo off
chcp 65001 >nul
REM Сборка архивов сервера: спросит, какой — полный (первая установка), обновление или оба (для выпуска)
REM Параметры: -Bump patch^|minor^|major^|same  -Version X.Y.Z  -Notes "что нового"  -SkipTests
title BookReader - сборка архивов сервера
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\build-server.ps1" %*
pause
