@echo off
rem One-time bootstrap for a brand-new Windows PC that has never run this
rem project before (e.g. the operator's own laptop, with no dev tools on
rem it). Installs Python if it's missing, then creates .venv and installs
rem requirements.txt into it. Run this once; after that, build.bat, run.bat,
rem gui.bat, test.bat, calib.bat and dryrun.bat all just work.
cd /d "%~dp0"

rem "where py" only proves a py.exe launcher STUB exists - on some PCs it's
rem registered (Store app-execution-alias, or leftover registry entries from
rem an install that got removed) but doesn't actually run. Confirming it
rem really launches a working interpreter avoids the confusing cascade of
rem "system cannot find the file/path specified" errors that follows from
rem trusting a broken launcher (real case: py pointed at a python.exe that
rem no longer existed under AppData\Local\Python\pythoncore-3.14-64\).
set PY_OK=
where py >nul 2>nul
if not errorlevel 1 (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set PY_OK=1
)

if not defined PY_OK (
    echo No working Python installation found on this PC ^(the "py" launcher
    echo is either missing or broken - a leftover/incomplete install can
    echo register it without it actually working^). Trying to install/repair
    echo it via winget...
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
    echo Python installed/repaired. Close this window and re-run setup.bat
    echo once more ^(needed so this terminal picks up the new PATH^).
    pause
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the virtual environment...
    py -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Could not create the virtual environment: "py -m venv .venv" did not
    echo produce .venv\Scripts\python.exe. Your Python installation is likely
    echo broken/incomplete. Try reinstalling it from
    echo https://www.python.org/downloads/ ^(check "Add python.exe to PATH"^),
    echo or run "winget install -e --id Python.Python.3.12" yourself, then
    echo re-run setup.bat.
    pause
    exit /b 1
)

echo Installing dependencies into .venv ...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo.
echo Done. You can now run build.bat, run.bat, gui.bat, test.bat, calib.bat
echo or dryrun.bat.
pause
