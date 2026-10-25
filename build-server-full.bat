@echo off
chcp 65001 >nul
REM Полный архив сервера для первой установки: dist\BookReader_Server_vX.Y.Z_full.zip (+ части _NVIDIA_) — всё без интернета
REM Параметры: -Bump patch^|minor^|major^|same  -Version X.Y.Z  -Notes "что нового"  -SkipTests
title BookReader - полный архив сервера
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\build-server.ps1" -Kind full %*
pause
