"""뷰어를 띄우고 시뮬레이션을 흘려보내는 웹 서버.

역할은 셋이다.

1. 정적 서빙 — ``viewer/`` 를 그대로 브라우저에 내려준다 (빌드 스텝 없음).
2. 도면/트레이스 공급 — ``/api/layout``, ``/api/traces``.
3. **라이브 스트림** — ``/ws`` 에서 시뮬레이션을 굴리며 프레임을 밀어 넣는다.

라이브와 녹화본은 **완전히 같은 프레임 포맷**을 쓴다 (docs/DECISIONS.md D-005).
그래서 뷰어에는 재생 코드가 한 벌만 있다. 발표장에서 서버가 죽어도 녹화본으로
같은 화면이 나온다.

**시뮬레이션은 연결마다 하나씩 새로 만든다.** 여러 사람이 붙어도 서로의 화면을
건드리지 않고, 슬라이더를 돌려 파라미터를 바꾸는 것이 남에게 영향을 주지 않는다.
발표자가 브라우저를 새로고침하면 깨끗한 상태에서 다시 시작한다.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sim.common.lotmap import LotMap
from sim.world.simulation import SimConfig, Simulation

REPO = Path(__file__).resolve().parents[1]
VIEWER = REPO / "viewer"
LAYOUTS = REPO / "layouts"
RUNS = REPO / "runs"

DEFAULT_LAYOUT = "mid_grid_120"

MAX_SPEED = 8.0
"""배속 상한. 이보다 빠르면 파이썬이 물리를 못 따라가 화면이 끊긴다."""

app = FastAPI(title="Smart Parking Projector — Viewer")


# ── 정적 데이터 ───────────────────────────────────────────────────


def load_lot(name: str = DEFAULT_LAYOUT) -> LotMap:
    """도면을 읽는다. 없으면 생성기를 돌려 만든다."""
    path = LAYOUTS / f"{name}.json"
    if not path.exists():
        from sim.world.lot_builder import GridSpec, build_grid_lot

        build_grid_lot(GridSpec(name=name)).save(path)
    return LotMap.load(path)


@app.get("/api/layout")
def get_layout(name: str = DEFAULT_LAYOUT) -> JSONResponse:
    return JSONResponse(load_lot(name).to_dict())


@app.get("/api/layouts")
def list_layouts() -> JSONResponse:
    names = sorted(p.stem for p in LAYOUTS.glob("*.json"))
    return JSONResponse({"layouts": names, "default": DEFAULT_LAYOUT})


@app.get("/api/traces")
def list_traces() -> JSONResponse:
    """녹화본 목록. 발표장에서 서버가 죽어도 이걸로 재생한다."""
    if not RUNS.exists():
        return JSONResponse({"traces": []})

    traces = []
    for p in sorted(RUNS.glob("*/trace.jsonl")):
        entry = {
            "id": p.parent.name,
            "path": f"/runs/{p.parent.name}/{p.name}",
            "bytes": p.stat().st_size,
        }
        meta = p.parent / "meta.json"
        if meta.exists():
            try:
                entry.update(json.loads(meta.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
        traces.append(entry)
    return JSONResponse({"traces": traces})


@app.get("/api/models")
def get_models() -> JSONResponse:
    """차량 3D 모델 설정. STL 이 없으면 뷰어가 저폴리 박스로 폴백한다."""
    path = VIEWER / "models" / "models.json"
    if not path.exists():
        return JSONResponse({"models": {}})
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


# ── 라이브 스트림 ─────────────────────────────────────────────────


class LiveSession:
    """WebSocket 연결 하나에 딸린 시뮬레이션."""

    def __init__(self, layout: str = DEFAULT_LAYOUT) -> None:
        self.lot = load_lot(layout)
        self.layout = layout
        self.config = SimConfig()
        self.sim = Simulation(self.lot, config=self.config)
        self.speed = 1.0
        self.paused = False

    def reset(self, **changes) -> None:
        """설정을 바꿔 처음부터 다시. 발표 중 슬라이더를 돌리는 순간이다."""
        clean = {k: v for k, v in changes.items() if v is not None}
        self.config = replace(self.config, **clean)
        self.sim = Simulation(self.lot, config=self.config)

    def apply(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        if cmd == "pause":
            self.paused = True
        elif cmd == "resume":
            self.paused = False
        elif cmd == "speed":
            self.speed = max(0.1, min(MAX_SPEED, float(msg.get("value", 1.0))))
        elif cmd == "reset":
            self.reset(
                seed=_maybe_int(msg.get("seed")),
                arrival_rate=_maybe_float(msg.get("arrival_rate")),
                max_guided=_maybe_int(msg.get("max_guided")),
            )

    @property
    def hello(self) -> dict:
        return {
            "type": "hello",
            "live": True,
            "layout": self.layout,
            "dt": self.config.dt,
            "config": asdict(self.config),
        }


@app.websocket("/ws")
async def live_stream(ws: WebSocket) -> None:
    """시뮬레이션을 실시간으로 흘려보낸다.

    보내기와 받기를 한 루프에 섞지 않는다. 제어 메시지를 기다리느라 프레임이
    밀리면 화면이 끊기기 때문이다. 수신은 별도 태스크가 맡고, 이 루프는
    시간만 지킨다.
    """
    await ws.accept()
    session = LiveSession()
    await ws.send_json(session.hello)

    inbox: asyncio.Queue[dict] = asyncio.Queue()
    reader = asyncio.create_task(_read_commands(ws, inbox))

    try:
        while True:
            while not inbox.empty():
                session.apply(inbox.get_nowait())

            if session.paused:
                await asyncio.sleep(0.05)
                continue

            frame = session.sim.step()
            payload = frame.to_dict()
            payload["type"] = "frame"
            await ws.send_json(payload)

            await asyncio.sleep(session.config.dt / session.speed)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        return
    finally:
        reader.cancel()


async def _read_commands(ws: WebSocket, inbox: asyncio.Queue) -> None:
    try:
        while True:
            await inbox.put(await ws.receive_json())
    except Exception:
        return


def _maybe_int(v) -> int | None:
    return None if v is None else int(v)


def _maybe_float(v) -> float | None:
    return None if v is None else float(v)


# ── 마운트 ────────────────────────────────────────────────────────

RUNS.mkdir(exist_ok=True)
app.mount("/runs", StaticFiles(directory=RUNS), name="runs")
app.mount("/js", StaticFiles(directory=VIEWER / "js"), name="js")
app.mount("/vendor", StaticFiles(directory=VIEWER / "vendor"), name="vendor")
app.mount("/models", StaticFiles(directory=VIEWER / "models"), name="models")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(VIEWER / "index.html")
