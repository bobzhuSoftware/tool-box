@echo off
REM Double-click to launch the native window (starts backend, opens the app window).
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" desktop_app.py
) else (
  start "" pythonw desktop_app.py
)
