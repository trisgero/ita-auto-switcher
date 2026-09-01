@echo off
rem Runs both test suites. Double-click, or .\test.bat from PowerShell.
cd /d "%~dp0"
echo ============ test_detection ============
.venv\Scripts\python.exe test_detection.py
echo.
echo ============ test_integration ==========
.venv\Scripts\python.exe test_integration.py
echo.
pause
