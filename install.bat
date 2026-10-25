@echo off
chcp 65001 >nul
REM Установщик сервера BookReader: соглашение, Python, библиотеки, модель голосов, ярлык на рабочем столе.
REM Повторный запуск безопасен — доустановит недостающее.
title BookReader - установка
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" %*
if errorlevel 1 pause
