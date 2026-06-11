@echo off
cd /d %~dp0
echo [%date% %time%] DashManager starting from %CD%
if not exist .venv\Scripts\python.exe (
  echo ERROR: .venv missing - run: uv venv .venv  then  uv pip install --python .venv -e ".[dev]"
  pause
  exit /b 1
)
echo Opening http://127.0.0.1:8765 ...
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe -m backend
pause
