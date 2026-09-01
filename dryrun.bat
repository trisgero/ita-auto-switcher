@echo off
rem DRY-RUN: detects, decides, and logs WITHOUT sending commands to vMix.
rem This is the test to run before going live. Ctrl+C to stop.
cd /d "%~dp0"
.venv\Scripts\python.exe -m autoswitch.main --dry-run
pause
