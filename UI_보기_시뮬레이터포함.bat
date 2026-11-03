@echo off
cd /d "%~dp0"
start "" /min python sim_printer.py
timeout /t 1 >nul
python app.py
