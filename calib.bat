@echo off
rem Live calibration viewer: ROIs drawn on the captured frame, q to quit.
rem Also accepts flags: calib.bat --monitors   /   calib.bat --rois
cd /d "%~dp0"
.venv\Scripts\python.exe calibrate.py %*
pause
