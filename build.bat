@echo off
rem Rebuilds AutoSwitcher.exe for distribution. Re-run whenever you touch
rem the code in autoswitch\ or change the factory config.json (the one
rem copied on first launch on a new PC).
cd /d "%~dp0"

.venv\Scripts\python.exe -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    .venv\Scripts\python.exe -m pip install pyinstaller
)

.venv\Scripts\python.exe -m PyInstaller ^
  --onefile ^
  --windowed ^
  --name AutoSwitcher ^
  --distpath dist ^
  --workpath build ^
  --add-data "config.json;." ^
  --add-data "__screenshots;__screenshots" ^
  --add-data "testdata;testdata" ^
  --hidden-import cv2 ^
  --hidden-import tkinter ^
  --collect-all mss ^
  --noconfirm ^
  app.py

echo.
echo Done. The executable is at dist\AutoSwitcher.exe
pause
