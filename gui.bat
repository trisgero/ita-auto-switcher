@echo off
rem Opens the GUI (template management, screenshots, inputs) from source,
rem without going through the exe. Useful for development/testing.
cd /d "%~dp0"
.venv\Scripts\python.exe app.py
