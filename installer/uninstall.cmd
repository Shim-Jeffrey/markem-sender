@echo off
rem Copy the uninstaller to TEMP first: a script cannot delete the folder it runs from.
copy /y "%~dp0uninstall.ps1" "%TEMP%\mk_uninstall.ps1" >nul
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%TEMP%\mk_uninstall.ps1" -App "%~dp0."
