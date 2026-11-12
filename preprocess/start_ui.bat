@echo off
chcp 65001 >nul
cd /d "%~dp0"
python serve_ui.py --port 8768 --open
if errorlevel 1 pause
