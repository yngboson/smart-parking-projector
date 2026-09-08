"""뷰어를 띄우는 웹 서버.

역할은 두 가지다.

1. 정적 서빙 — ``viewer/`` 를 그대로 브라우저에 내려준다 (빌드 스텝 없음).
2. 도면/트레이스 공급 — ``/api/layout`` 과 ``/api/traces``.

라이브 시뮬레이션 스트리밍(WebSocket ``/ws``)은 4단계에서 채운다. 지금은 연결만
받아주고 도면과 하트비트를 보낸다. 프레임 포맷은 트레이스 재생과 동일하므로
(docs/DECISIONS.md D-005) 뷰어 코드는 한 벌만 유지된다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

REPO = Path(__file__).resolve().parents[1]
VIEWER = REPO / "viewer"
LAYOUTS = REPO / "layouts"
RUNS = REPO / "runs"

DEFAULT_LAYOUT = "mid_grid_120"

app = FastAPI(title="Smart Parking Projector — Viewer")


@app.get("/api/layout")
def get_layout(name: str = DEFAULT_LAYOUT) -> JSONResponse:
    """주차장 도면을 내려준다. 없으면 생성기를 돌려 만든다."""
    path = LAYOUTS / f"{name}.json"
    if not path.exists():
        from sim.world.lot_builder import GridSpec, build_grid_lot

        build_grid_lot(GridSpec(name=name)).save(path)
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/layouts")
def list_layouts() -> JSONResponse:
    names = sorted(p.stem for p in LAYOUTS.glob("*.json"))
    return JSONResponse({"layouts": names, "default": DEFAULT_LAYOUT})


@app.get("/api/traces")
def list_traces() -> JSONResponse:
    """녹화본 목록. 발표장에서 서버가 죽어도 이걸로 재생한다."""
    if not RUNS.exists():
        return JSONResponse({"traces": []})
    traces = [
        {
            "id": p.parent.name,
            "path": f"/runs/{p.parent.name}/{p.name}",
            "bytes": p.stat().st_size,
        }
        for p in sorted(RUNS.glob("*/trace.jsonl"))
    ]
    return JSONResponse({"traces": traces})


@app.get("/api/models")
def get_models() -> JSONResponse:
    """차량 3D 모델 설정. STL 이 없으면 뷰어가 저폴리 박스로 폴백한다."""
    path = VIEWER / "models" / "models.json"
    if not path.exists():
        return JSONResponse({"models": {}})
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.websocket("/ws")
async def live_stream(ws: WebSocket) -> None:
    """라이브 시뮬레이션 스트림.

    4단계에서 실제 프레임을 흘려보낸다. 지금은 뷰어가 연결 상태를 표시할 수 있도록
    핸드셰이크만 처리한다.
    """
    await ws.accept()
    await ws.send_json({"type": "hello", "stage": "layout-only", "live": False})
    try:
        while True:
            await asyncio.sleep(5.0)
            await ws.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        return


if RUNS.exists():
    app.mount("/runs", StaticFiles(directory=RUNS), name="runs")

app.mount("/", StaticFiles(directory=VIEWER, html=True), name="viewer")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(VIEWER / "index.html")
