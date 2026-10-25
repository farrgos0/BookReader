@echo off
chcp 65001 >nul
REM BookReader с синтезом на видеокарте (нужен torch с CUDA). Обычный BookReader.bat и так берёт видеокарту, если она доступна
cd /d "%~dp0"
call BookReader.bat --device cuda
