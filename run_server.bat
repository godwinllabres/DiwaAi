@echo off
setlocal
cd /d "%~dp0"
if not defined SEVI_API_PORT set "SEVI_API_PORT=8010"
set "SEVI_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "SEVI_PYTHON=.venv\Scripts\python.exe"
echo Starting CvSU Chatbot API Server...
echo.
REM api.app is the only entrypoint. The legacy root app.py was retired — it
REM served the same logger routes without authentication.
"%SEVI_PYTHON%" -m uvicorn api.app:app --env-file .env --host 127.0.0.1 --port %SEVI_API_PORT%
pause
