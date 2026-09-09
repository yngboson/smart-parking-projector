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

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sim.common.lotmap import LotMap
from sim.scenarios.loader import available, load_named
from sim.world.simulation import SimConfig, Simulation

REPO = Path(__file__).resolve().parents[1]
VIEWER = REPO / "viewer"
LAYOUTS = REPO / "layouts"
RUNS = REPO / "runs"

DEFAULT_LAYOUT = "mid_grid_120"

DEFAULT_SCENARIO = "busy"
"""라이브 데모가 기본으로 트는 시나리오.

빈 주차장으로 시작하면 아무도 남의 자리를 건드릴 이유가 없어 **이 연구의 질문이
화면에 나타나지 않는다.** 절반쯤 찬 주차장에서 시작해야 강탈이 보인다.
"""

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


@app.get("/api/scenarios")
def list_scenarios() -> JSONResponse:
    """실험 시나리오 목록. 발표 중에 조건을 갈아 끼울 때 쓴다."""
    out = []
    for name in available():
        s = load_named(name)
        out.append(
            {
                "name": s.name,
                "description": " ".join(s.description.split()),
                "arrival_rate": s.config.arrival_rate,
                "prefill": s.config.prefill,
                "noncompliant_share": s.config.noncompliant_share,
            }
        )
    return JSONResponse({"scenarios": out, "default": DEFAULT_SCENARIO})


MODELS_JSON = VIEWER / "models" / "models.json"

TUNABLE = {
    "file": str,
    "autoFitLength": float,
    "upAxis": str,
    "headingOffsetDeg": float,
    "pivot": str,
    "scaleOverride": float,
    "offset": list,
}
"""조정 UI 가 쓸 수 있는 항목과 형. **여기 없는 키는 저장하지 않는다.**

브라우저에서 온 JSON 을 그대로 파일에 쓰면 뷰어 설정 파일이 아무 데이터나 담는
통로가 된다. 화이트리스트로 걸러 두면 그 통로가 없다.
"""


@app.get("/api/models")
def get_models() -> JSONResponse:
    """차량 3D 모델 설정 + 폴더에 실제로 있는 STL 파일 목록.

    파일 목록을 함께 주는 이유: STL 을 폴더에 넣은 사람이 `models.json` 을 손으로
    고치지 않아도 뷰어에서 골라 등록할 수 있어야 한다 (docs/PLAN.md 9단계).
    """
    body = (
        json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        if MODELS_JSON.exists()
        else {"models": {}}
    )
    body["files"] = sorted(
        p.name for p in (VIEWER / "models").glob("*.stl") if p.is_file()
    )
    return JSONResponse(body)


@app.put("/api/models")
async def put_models(request: Request) -> JSONResponse:
    """조정 UI 가 맞춘 값을 `models.json` 에 되쓴다.

    STL 은 단위도 축 방향도 파일마다 다르다. 그 값을 눈으로 맞춰 놓고 저장하지
    못하면 브라우저를 새로고침할 때마다 처음부터 다시 맞춰야 한다 — 발표 준비
    중에 그럴 시간은 없다.

    **화이트리스트 밖의 키와 경로가 섞인 파일 이름은 버린다.** 브라우저에서
    오는 값이므로 그대로 믿지 않는다.
    """
    body = await request.json()
    incoming = body.get("models")
    if not isinstance(incoming, dict):
        return JSONResponse({"error": "models 가 객체여야 합니다"}, status_code=400)

    clean: dict[str, dict] = {}
    for name, cfg in incoming.items():
        if not isinstance(name, str) or not isinstance(cfg, dict):
            continue
        entry = {}
        for key, kind in TUNABLE.items():
            if key not in cfg or cfg[key] is None:
                continue
            value = cfg[key]
            if key == "file":
                # 경로가 아니라 이 폴더 안의 파일 이름이어야 한다
                value = Path(str(value)).name
                if not (VIEWER / "models" / value).exists():
                    continue
            elif kind is float and isinstance(value, (int, float)):
                value = float(value)
            elif not isinstance(value, kind):
                continue
            entry[key] = value
        if entry.get("file"):
            clean[name] = entry

    existing = (
        json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        if MODELS_JSON.exists()
        else {}
    )
    existing["models"] = clean
    MODELS_JSON.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return JSONResponse({"models": clean, "saved": len(clean)})


# ── 라이브 스트림 ─────────────────────────────────────────────────


class LiveSession:
    """WebSocket 연결 하나에 딸린 시뮬레이션."""

    def __init__(self, layout: str = DEFAULT_LAYOUT, scenario: str = DEFAULT_SCENARIO) -> None:
        self.lot = load_lot(layout)
        self.layout = layout
        self.scenario = scenario
        self.config = _scenario_config(scenario)
        self.sim = self._build()
        self.speed = 1.0
        self.paused = False

    def _build(self) -> Simulation:
        """시나리오가 지정한 할당·복구 전략으로 시뮬레이션을 만든다."""
        try:
            control = load_named(self.scenario).build_control(self.lot)
        except FileNotFoundError:
            control = None
        return Simulation(self.lot, control=control, config=self.config)

    def reset(self, **changes) -> None:
        """설정을 바꿔 처음부터 다시. 발표 중 슬라이더를 돌리는 순간이다."""
        clean = {k: v for k, v in changes.items() if v is not None}
        self.config = replace(self.config, **clean)
        self.sim = self._build()

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
                noncompliant_share=_maybe_float(msg.get("noncompliant_share")),
                prefill=_maybe_float(msg.get("prefill")),
                max_guided=_maybe_int(msg.get("max_guided")),
            )
        elif cmd == "scenario":
            name = str(msg.get("name", DEFAULT_SCENARIO))
            self.scenario = name
            self.config = _scenario_config(name)
            self.sim = self._build()

    @property
    def hello(self) -> dict:
        return {
            "type": "hello",
            "live": True,
            "layout": self.layout,
            "scenario": self.scenario,
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


def _scenario_config(name: str) -> SimConfig:
    """시나리오 파일에서 설정을 읽는다. 없는 이름이면 기본값으로 돈다."""
    try:
        return load_named(name).config
    except FileNotFoundError:
        return SimConfig()


def _maybe_int(v) -> int | None:
    return None if v is None else int(v)


def _maybe_float(v) -> float | None:
    return None if v is None else float(v)


# ── 마운트 ────────────────────────────────────────────────────────

RUNS.mkdir(exist_ok=True)
app.mount("/runs", StaticFiles(directory=RUNS), name="runs")
@app.middleware("http")
async def no_stale_viewer_code(request: Request, call_next):
    """뷰어 코드는 **항상 다시 확인하게** 한다.

    이 뷰어에는 빌드 스텝이 없다 (D-004). 파일 이름에 해시가 붙지 않으므로 브라우저는
    `/js/main.js` 를 한 번 받으면 계속 쓴다 — ES 모듈은 특히 끈질기게 캐시된다.
    고친 코드가 화면에 안 나타나는데 원인이 캐시라는 것을 알아채기까지가 오래 걸리고,
    그동안 있지도 않은 버그를 쫓게 된다. 실제로 그랬다.

    `no-cache` 는 "저장하지 마라"가 아니라 "쓰기 전에 물어봐라"이므로, 안 바뀐
    파일은 304 로 끝나 비용이 거의 없다.
    """
    response = await call_next(request)
    if request.url.path.startswith(("/js/", "/models/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/js", StaticFiles(directory=VIEWER / "js"), name="js")
app.mount("/vendor", StaticFiles(directory=VIEWER / "vendor"), name="vendor")
app.mount("/models", StaticFiles(directory=VIEWER / "models"), name="models")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(VIEWER / "index.html")
