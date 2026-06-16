@echo off
cd /d %~dp0
echo [%date% %time%] DashManager starting from %CD%
set "URL=http://127.0.0.1:8765"

REM --- If the app is already serving on :8765, just open the browser. ---
REM Avoids the cryptic WinError 10048 (port already in use) when re-launching.
netstat -ano | findstr /R /C:"LISTENING" | findstr /C:":8765 " >nul 2>&1
if %errorlevel%==0 (
  echo DashManager already running - opening %URL%
  start "" %URL%
  exit /b 0
)

if not exist .venv\Scripts\python.exe (
  echo ERROR: .venv missing - run: uv venv .venv  then  uv pip install --python .venv -e ".[dev]"
  pause
  exit /b 1
)

echo Opening %URL% ...
start "" %URL%
.venv\Scripts\python.exe -m backend
pause
