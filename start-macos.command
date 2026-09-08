#!/usr/bin/env bash
# 주차 유도 시뮬레이터 — macOS / Linux 실행 스크립트
#
# macOS 에서는 Finder 에서 이 파일을 더블클릭하면 됩니다.
# 처음 실행할 때 "확인되지 않은 개발자" 경고가 뜨면,
# 파일을 우클릭 → "열기" 를 선택하세요.
#
# 실행 권한이 없다는 오류가 나면 터미널에서 한 번만:
#   chmod +x start-macos.command

set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  =========================================="
echo "   주차 유도 시뮬레이터"
echo "  =========================================="
echo

# ── 1) 파이썬 확인 ───────────────────────────────────────────
PY=""
for cand in python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$cand"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "  [오류] Python 3.11 이상을 찾을 수 없습니다."
  echo
  echo "  Homebrew 가 있다면:   brew install python@3.12"
  echo "  없다면:               https://www.python.org/downloads/"
  echo
  read -r -p "  엔터를 누르면 닫힙니다." _
  exit 1
fi

# ── 2) 가상환경 ──────────────────────────────────────────────
if [ ! -x ".venv/bin/python" ]; then
  echo "  [1/3] 가상환경을 만드는 중... (처음 한 번만)"
  "$PY" -m venv .venv
else
  echo "  [1/3] 가상환경 확인됨"
fi
VPY=".venv/bin/python"

# ── 3) 의존성 ────────────────────────────────────────────────
if ! "$VPY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "  [2/3] 필요한 패키지를 설치하는 중... (1~2분 걸릴 수 있습니다)"
  "$VPY" -m pip install --quiet --upgrade pip
  "$VPY" -m pip install --quiet -e ".[dev]"
else
  echo "  [2/3] 패키지 확인됨"
fi

# ── 4) 도면 ──────────────────────────────────────────────────
if [ ! -f "layouts/mid_grid_120.json" ]; then
  echo "  [3/3] 주차장 도면을 생성하는 중..."
  "$VPY" -m sim.world.lot_builder
else
  echo "  [3/3] 도면 확인됨"
fi

# ── 5) 서버 ──────────────────────────────────────────────────
URL="http://127.0.0.1:8000"
echo
echo "  브라우저에서 여는 중: $URL"
echo "  종료하려면 이 창에서 Ctrl+C 를 누르세요."
echo

( sleep 1.5
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  fi ) >/dev/null 2>&1 &

exec "$VPY" -m uvicorn server.app:app --host 127.0.0.1 --port 8000
