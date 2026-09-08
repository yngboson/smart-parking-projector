@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   ==========================================
echo    주차 유도 시뮬레이터
echo   ==========================================
echo.

REM ── 1) 파이썬 확인 ───────────────────────────────────────────
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo   [오류] 파이썬을 찾을 수 없습니다.
  echo.
  echo   https://www.python.org/downloads/ 에서 Python 3.11 이상을 설치한 뒤
  echo   설치 화면에서 "Add Python to PATH" 를 반드시 체크하세요.
  echo.
  pause
  exit /b 1
)

REM ── 2) 가상환경 ──────────────────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
  echo   [1/3] 가상환경을 만드는 중... ^(처음 한 번만^)
  %PY% -m venv .venv
  if errorlevel 1 (
    echo   [오류] 가상환경 생성에 실패했습니다.
    pause
    exit /b 1
  )
) else (
  echo   [1/3] 가상환경 확인됨
)
set "VPY=.venv\Scripts\python.exe"

REM ── 3) 의존성 ────────────────────────────────────────────────
"%VPY%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo   [2/3] 필요한 패키지를 설치하는 중... ^(1~2분 걸릴 수 있습니다^)
  "%VPY%" -m pip install --quiet --upgrade pip
  "%VPY%" -m pip install --quiet -e ".[dev]"
  if errorlevel 1 (
    echo   [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하세요.
    pause
    exit /b 1
  )
) else (
  echo   [2/3] 패키지 확인됨
)

REM ── 4) 도면 ──────────────────────────────────────────────────
if not exist "layouts\mid_grid_120.json" (
  echo   [3/3] 주차장 도면을 생성하는 중...
  "%VPY%" -m sim.world.lot_builder
) else (
  echo   [3/3] 도면 확인됨
)

REM ── 5) 서버 ──────────────────────────────────────────────────
echo.
echo   브라우저에서 여는 중: http://127.0.0.1:8000
echo   종료하려면 이 창에서 Ctrl+C 를 누르세요.
echo.
start "" http://127.0.0.1:8000
"%VPY%" -m uvicorn server.app:app --host 127.0.0.1 --port 8000

endlocal
