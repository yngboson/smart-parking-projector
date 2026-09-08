"""프레임 공급 — 라이브 스트림과 녹화본이 정말 같은 것을 내보내는가.

발표 당일 서버가 죽으면 녹화본으로 전환한다 (docs/DECISIONS.md D-005). 그 전환이
성립하려면 두 경로가 **글자 그대로 같은 포맷**이어야 한다. 뷰어에는 재생 코드가
한 벌뿐이므로, 여기서 어긋나면 사고 당일에야 알게 된다.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sim.common.lotmap import LotMap
from sim.metrics.trace_writer import TraceWriter, read, record
from sim.world.lot_builder import GridSpec, build_grid_lot
from sim.world.simulation import SimConfig, Simulation

FRAME_KEYS = {"t", "vehicles", "guidance", "slots", "events", "kpi"}


@pytest.fixture(scope="module")
def lot() -> LotMap:
    return build_grid_lot(GridSpec())


@pytest.fixture(scope="module")
def client() -> TestClient:
    from server.app import app

    return TestClient(app)


# ── 녹화본 ────────────────────────────────────────────────────────


def test_recording_round_trips(tmp_path, lot: LotMap) -> None:
    path = record(lot, SimConfig(seed=2, arrival_rate=0.2), 40.0, tmp_path / "run")
    frames = list(read(path))

    assert frames, "녹화본이 비어 있습니다"
    for f in frames:
        assert set(f) == FRAME_KEYS
    assert frames[0]["t"] < frames[-1]["t"]

    meta = json.loads((path.parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["layout"] == lot.name
    assert meta["frames"] == len(frames)
    assert meta["config"]["seed"] == 2


def test_stride_thins_the_file_without_losing_slot_changes(tmp_path, lot: LotMap) -> None:
    """틱을 솎아내도 주차면 상태 변화는 잃지 않아야 한다.

    상태는 델타로 나가므로, 건너뛴 틱의 변화가 사라지면 뷰어의 주차면 색이
    영원히 틀어진다.
    """
    config = SimConfig(seed=5, arrival_rate=0.25)
    dense = list(read(record(lot, config, 120.0, tmp_path / "a", stride=1)))
    thin = list(read(record(lot, config, 120.0, tmp_path / "b", stride=4)))

    assert len(thin) < len(dense) / 3

    def final_status(frames):
        status = {}
        for f in frames:
            for s in f["slots"]:
                status[s["id"]] = s["status"]
        return status

    assert final_status(thin) == final_status(dense)


def test_a_truncated_trace_still_replays(tmp_path, lot: LotMap) -> None:
    """녹화 중 전원이 나가도 거기까지는 재생돼야 한다 — JSON Lines 를 쓴 이유."""
    path = record(lot, SimConfig(seed=1), 30.0, tmp_path / "run")
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: int(len(text) * 0.6)], encoding="utf-8")

    frames = list(read(path))
    assert frames
    assert all(set(f) == FRAME_KEYS for f in frames)


def test_writer_records_the_config_it_ran(tmp_path, lot: LotMap) -> None:
    """어떤 설정으로 녹화한 것인지 모르면 발표에서 방어할 수 없다."""
    config = SimConfig(seed=9, arrival_rate=0.33, dwell_mean=99.0)
    with TraceWriter(tmp_path / "run", lot.name, config) as w:
        sim = Simulation(lot, config=config)
        for frame in sim.run(5.0):
            w.write(frame)
        w.close(frame)

    meta = json.loads((tmp_path / "run" / "meta.json").read_text(encoding="utf-8"))
    assert meta["config"]["arrival_rate"] == 0.33
    assert meta["config"]["dwell_mean"] == 99.0


# ── 서버 ──────────────────────────────────────────────────────────


def test_layout_endpoint_serves_a_loadable_map(client: TestClient, lot: LotMap) -> None:
    data = client.get("/api/layout").json()
    again = LotMap.from_dict(data)
    assert len(again.slots) == len(lot.slots)
    assert again.entry_nodes and again.exit_nodes


def test_viewer_assets_are_served(client: TestClient) -> None:
    """뷰어는 빌드 스텝이 없다. 경로 하나가 어긋나면 화면이 비어 버린다."""
    assert client.get("/").status_code == 200
    for path in ("/js/main.js", "/js/source.js", "/vendor/three.module.min.js"):
        assert client.get(path).status_code == 200, f"{path} 를 서빙하지 못합니다"


def test_traces_endpoint_lists_recordings(client: TestClient, tmp_path, lot: LotMap) -> None:
    body = client.get("/api/traces").json()
    assert "traces" in body
    for t in body["traces"]:
        assert t["path"].startswith("/runs/")
        assert t["bytes"] > 0


# ── 라이브 스트림 ─────────────────────────────────────────────────


def test_live_stream_sends_frames_in_the_same_format(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["live"] is True
        assert hello["dt"] > 0

        for _ in range(5):
            frame = ws.receive_json()
            assert frame["type"] == "frame"
            assert set(frame) == FRAME_KEYS | {"type"}


def test_live_stream_advances_time(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        first = ws.receive_json()["t"]
        for _ in range(9):
            last = ws.receive_json()["t"]
        assert last > first


def test_session_control_commands_take_effect() -> None:
    """일시정지·배속·리셋은 세션 상태에서 검증한다.

    소켓 너머로 시험하지 않는 이유: 일시정지된 서버는 프레임을 보내지 않으므로
    `receive_json()` 이 영원히 블로킹된다. 테스트가 멈추면 CI 도 멈춘다.
    """
    from server.app import MAX_SPEED, LiveSession

    s = LiveSession()
    assert not s.paused and s.speed == 1.0

    s.apply({"cmd": "pause"})
    assert s.paused
    s.apply({"cmd": "resume"})
    assert not s.paused

    s.apply({"cmd": "speed", "value": 2.5})
    assert s.speed == 2.5
    s.apply({"cmd": "speed", "value": 9999})
    assert s.speed == MAX_SPEED, "배속 상한이 없으면 파이썬이 물리를 못 따라간다"

    s.apply({"cmd": "nonsense"})   # 모르는 명령은 조용히 무시한다


def test_reset_restarts_with_the_new_settings() -> None:
    """발표 중 슬라이더를 돌리는 순간. 설정이 바뀌고 시뮬레이션이 처음으로 돌아간다."""
    from server.app import LiveSession

    s = LiveSession()
    for _ in range(20):
        s.sim.step()
    assert s.sim.t > 0

    s.apply({"cmd": "reset", "arrival_rate": 0.4, "seed": 3})
    assert s.config.arrival_rate == 0.4
    assert s.config.seed == 3
    assert s.sim.t == 0.0, "리셋했는데 시각이 되감기지 않았습니다"

    # 지정하지 않은 값은 그대로 유지된다
    assert s.config.dwell_mean == LiveSession().config.dwell_mean
