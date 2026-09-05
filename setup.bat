@echo off
rem One-time bootstrap for a brand-new Windows PC that has never run this
rem project before (e.g. the operator's own laptop, with no dev tools on
rem it). Installs Python if it's missing, then creates .venv and installs
rem requirements.txt into it. Run this once; after that, build.bat, run.bat,
rem gui.bat, test.bat, calib.bat and dryrun.bat all just work.
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python not found on this PC. Trying to install it via winget...
    where winget >nul 2>nul
    if errorlevel 1 (
        echo.
        echo winget isn't available either. Install Python by hand:
        echo   1. Open https://www.python.org/downloads/
        echo   2. Download and run the Windows installer
        echo   3. On the FIRST screen, CHECK "Add python.exe to PATH"
        echo   4. Re-run setup.bat afterwards.
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo.
        echo winget install failed. Install Python by hand from
        echo https://www.python.org/downloads/ - remember to check "Add
        echo python.exe to PATH" - then re-run setup.bat.
        pause
        exit /b 1
    )
    echo.
    echo Python installed. Close this window and re-run setup.bat once more
    echo ^(needed so this terminal picks up the new PATH^).
    pause
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the virtual environment...
    py -m venv .venv
)

echo Installing dependencies into .venv ...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo.
echo Done. You can now run build.bat, run.bat, gui.bat, test.bat, calib.bat
echo or dryrun.bat.
pause
