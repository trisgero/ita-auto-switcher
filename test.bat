@echo off
rem Runs all three test suites. Double-click, or .\test.bat from PowerShell.
cd /d "%~dp0"
echo ============ test_detection ============
.venv\Scripts\python.exe test_detection.py
echo.
echo ============ test_overlays =============
.venv\Scripts\python.exe test_overlays.py
echo.
echo ============ test_integration ==========
.venv\Scripts\python.exe test_integration.py
echo.
pause
