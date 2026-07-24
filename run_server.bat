@echo off
echo Starting CvSU Chatbot API Server...
echo.
REM api.app is the only entrypoint. The legacy root app.py was retired — it
REM served the same logger routes without authentication.
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000
pause
