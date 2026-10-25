@echo off
chcp 65001 >nul
title BookReader - удаление
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\uninstall.ps1"
