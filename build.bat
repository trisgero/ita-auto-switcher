@echo off
rem Rebuilds AutoSwitcher.exe for distribution. Re-run whenever you touch
rem the code in autoswitch\ or change the factory config.json (the one
rem copied on first launch on a new PC).
rem
rem The exe filename and the "Build:" label inside the app itself both come
rem from build_info.py, which stamps the exact git commit + build timestamp
rem into version_info.json - so months later you can tell which .exe someone
rem is running just by looking at the filename or opening the app.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo .venv not found - running setup.bat first...
    call setup.bat
    if not exist ".venv\Scripts\python.exe" (
        echo Setup did not finish ^(see messages above^). Re-run build.bat once it has.
        exit /b 1
    )
)

.venv\Scripts\python.exe -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    .venv\Scripts\python.exe -m pip install pyinstaller
)

for /f "delims=" %%i in ('.venv\Scripts\python.exe build_info.py') do set EXE_NAME=%%i
echo Building %EXE_NAME%.exe ...

.venv\Scripts\python.exe -m PyInstaller ^
  --onefile ^
  --windowed ^
  --name "%EXE_NAME%" ^
  --distpath dist ^
  --workpath build ^
  --add-data "config.json;." ^
  --add-data "version_info.json;." ^
  --add-data "__screenshots;__screenshots" ^
  --add-data "testdata;testdata" ^
  --hidden-import cv2 ^
  --hidden-import tkinter ^
  --collect-all mss ^
  --noconfirm ^
  app.py

echo.
echo Done. The executable is at dist\%EXE_NAME%.exe
pause
