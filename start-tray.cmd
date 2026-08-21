@echo off
REM Double-click to launch the tray version (resident backend + system tray icon).
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" desktop_launcher.py
) else (
  start "" pythonw desktop_launcher.py
)
