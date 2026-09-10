"""시뮬레이션 배선 — 세 계층이 실제로 맞물려 도는가.

3단계의 완료 기준은 하나다 (docs/PLAN.md 구현 순서):

    **유도선 커맨드가 생성되고 차량이 그것을 따라간다.**

그래서 여기서는 단위 동작이 아니라 **끝까지 굴러가는지**를 본다.
차가 안내를 받고, 통로를 달리고, 후진으로 주차면 안에 들어가고, 다시 나가는가.
"""

from __future__ import annotations

import pytest

from sim.agents.driver import DriverPhase
from sim.common.ids import SlotStatus
from sim.common.lotmap import LotMap
from sim.common.maneuver import slot_contains
from sim.common.vehicle import footprint
from sim.world.lot_builder import GridSpec, build_grid_lot
from sim.world.simulation import Frame, SimConfig, Simulation

DURATION = 200.0


@pytest.fixture(scope="module")
def lot() -> LotMap:
    return build_grid_lot(GridSpec())


@pytest.fixture(scope="module")
def run(lot: LotMap) -> tuple[Simulation, list[Frame]]:
    sim = Simulation(lot, config=SimConfig(seed=7, arrival_rate=0.10))
    frames = list(sim.run(DURATION))
    return sim, frames


# ── 생애주기 ──────────────────────────────────────────────────────


def test_cars_arrive_get_guided_and_park(run) -> None:
    sim, frames = run
    assert sim._park_times, "아무도 주차하지 못했습니다"
    assert any(f.guidance for f in frames), "유도선이 한 번도 발급되지 않았습니다"
    assert frames[-1].kpi["parked_total"] >= 12, "주차 처리량이 무너졌습니다"


def test_every_parked_car_is_actually_inside_its_slot(run, lot: LotMap) -> None:
    """가장 중요한 검사. 유도선을 따라간 결과가 주차면 밖이면 이 시스템은 거짓말이다."""
    sim, _frames = run
    outside = []
    for v in sim.vehicles:
        if v.parked_slot is None:
            continue
        s = lot.slots[v.parked_slot]
        if not slot_contains(
            footprint(v.state.pose, v.spec), s.center, s.heading, s.length, s.width, 0.25
        ):
            outside.append((v.plate, v.parked_slot))
    assert not outside, f"주차면을 벗어난 차량: {outside}"


def test_parked_cars_face_the_aisle(run, lot: LotMap) -> None:
    """후진 주차이므로 앞머리는 통로 쪽이어야 한다. 전진으로 들어갔다면 여기서 걸린다."""
    sim, _frames = run
    for v in sim.vehicles:
        if v.parked_slot is None:
            continue
        slot = lot.slots[v.parked_slot]
        to_aisle = (lot.node_pos(slot.access_node) - slot.center).normalized()
        facing = v.state.pose.forward
        assert to_aisle.dot(facing) > 0.9, f"{v.plate} 가 통로를 등지고 서 있습니다"


def test_cars_eventually_leave(lot: LotMap) -> None:
    """체류 시간이 끝나면 스스로 출구를 찾아 나가야 한다."""
    sim = Simulation(
        lot, config=SimConfig(seed=3, arrival_rate=0.10, dwell_mean=25.0, dwell_min=10.0)
    )
    seen_leaving = False
    for _f in sim.run(300.0):
        if any(v.driver.phase is DriverPhase.LEAVING for v in sim.vehicles):
            seen_leaving = True
    assert seen_leaving, "출차하는 차량이 없었습니다"
    assert sim.sensors._present is not None


def test_no_car_is_assigned_a_slot_another_car_occupies(run) -> None:
    sim, _frames = run
    taken = [v.parked_slot for v in sim.vehicles if v.parked_slot is not None]
    assert len(taken) == len(set(taken)), "두 대가 같은 주차면에 들어갔습니다"


# ── 관제 ↔ 월드 정합 ──────────────────────────────────────────────


def settled_before_the_end(sim, v) -> bool:
    """센서가 점유를 확정할 시간이 있었는가.

    주차면 센서는 같은 판정이 `OCCUPANCY_DEBOUNCE` 만큼 이어져야 발행한다.
    실행이 끝나기 직전에 주차한 차는 관제가 **아직 모르는 것이 정상**이다.
    """
    from sim.world.sensors import OCCUPANCY_DEBOUNCE

    return v.parked_t is not None and sim.t - v.parked_t > OCCUPANCY_DEBOUNCE + sim.config.dt


def test_control_beliefs_match_what_actually_happened(run) -> None:
    """관제의 믿음이 현실과 어긋나면 재할당이 엉뚱한 자리를 준다.

    관제는 센서만 보고 판단하지만, 센서에 노이즈가 없으므로 결과는 일치해야 한다.
    """
    sim, _frames = run
    for v in sim.vehicles:
        if v.parked_slot is None or not settled_before_the_end(sim, v):
            continue
        belief = sim.control.state.slots[v.parked_slot]
        assert belief.status is SlotStatus.OCCUPIED
        assert belief.occupant == v.plate


def test_occupied_slot_count_matches_the_parked_cars(run) -> None:
    sim, _frames = run
    occupied = {
        sid for sid, b in sim.control.state.slots.items()
        if b.status is SlotStatus.OCCUPIED
    }
    parked = {
        v.parked_slot for v in sim.vehicles
        if v.parked_slot is not None and settled_before_the_end(sim, v)
    }
    assert parked <= occupied


# ── 프레임 포맷 ───────────────────────────────────────────────────


def test_frame_has_the_agreed_shape(run) -> None:
    """뷰어와 합의된 포맷 (docs/PLAN.md 10). 여기가 바뀌면 뷰어가 깨진다."""
    _sim, frames = run
    d = frames[-1].to_dict()
    assert set(d) == {"t", "vehicles", "guidance", "slots", "events", "kpi"}
    assert set(frames[-1].kpi) >= {"occupancy", "avg_park_time", "reroutes"}

    for row in d["vehicles"]:
        assert set(row) == {"id", "pose", "state", "color", "model"}
        assert len(row["pose"]) == 3


def test_first_frame_carries_every_slot_then_only_changes(run, lot: LotMap) -> None:
    """주차면 상태는 델타로 보낸다. 첫 프레임만 전수, 이후엔 바뀐 것만."""
    _sim, frames = run
    assert len(frames[0].slots) == len(lot.slots)
    assert all(len(f.slots) < len(lot.slots) for f in frames[1:])


def test_polyline_is_sent_once_per_revision(run) -> None:
    """바뀌지 않은 유도선은 진행률만 보낸다 — trace 파일 크기를 억제한다."""
    _sim, frames = run
    with_polyline = sum(
        1 for f in frames for g in f.guidance if "polyline" in g
    )
    total = sum(len(f.guidance) for f in frames)
    assert with_polyline > 0
    assert with_polyline < total * 0.2, "델타 압축이 동작하지 않습니다"


def test_guidance_progress_never_goes_backwards(run) -> None:
    """지웠던 선이 다시 나타나면 운전자가 혼란스럽다."""
    _sim, frames = run
    last: dict[str, float] = {}
    for f in frames:
        for g in f.guidance:
            prev = last.get(g["id"])
            if prev is not None and "polyline" not in g:
                assert g["progress"] >= prev - 1e-9
            last[g["id"]] = g["progress"]


# ── 재현성 ────────────────────────────────────────────────────────


def test_same_seed_gives_the_same_run(lot: LotMap) -> None:
    """시드가 같으면 결과가 같아야 실험이 성립한다 (전략 비교의 전제)."""
    def final_kpi(seed: int) -> dict:
        sim = Simulation(lot, config=SimConfig(seed=seed, arrival_rate=0.12))
        last = None
        for f in sim.run(120.0):
            last = f
        return last.kpi

    assert final_kpi(11) == final_kpi(11)


def test_different_seeds_give_different_runs(lot: LotMap) -> None:
    def plates(seed: int) -> set[str]:
        sim = Simulation(lot, config=SimConfig(seed=seed, arrival_rate=0.12))
        for _f in sim.run(60.0):
            pass
        return {v.plate for v in sim.vehicles}

    assert plates(1) != plates(2)


# ── 계층 경계 ─────────────────────────────────────────────────────


def test_control_only_ever_sees_sensor_events(lot: LotMap) -> None:
    """관제에 들어가는 값이 전부 SensorEvent 인지 실제 실행 중에 확인한다.

    import 검사(`test_layer_isolation`)는 정적이다. 이 검사는 **런타임에** 월드가
    관제에게 무엇을 건네는지 본다 — 차량 객체가 새어 들어가면 여기서 잡힌다.
    """
    from sim.common.messages import SensorEvent

    sim = Simulation(lot, config=SimConfig(seed=5, arrival_rate=0.2))
    original = sim.control.on_events
    seen: list[object] = []

    def spy(events):
        seen.extend(events)
        return original(events)

    sim.control.on_events = spy  # type: ignore[method-assign]
    for _f in sim.run(60.0):
        pass

    assert seen, "관제가 아무 이벤트도 받지 못했습니다"
    for e in seen:
        assert isinstance(e, SensorEvent), f"{type(e).__name__} 은 센서 이벤트가 아닙니다"
        for value in vars(e).values() if hasattr(e, "__dict__") else ():
            assert not hasattr(value, "spec"), "차량 객체가 이벤트에 실려 있습니다"


# ── 두 개의 상한 (D-030) ──────────────────────────────────────────


def test_the_palette_limit_never_throttles_the_unguided_mode(lot: LotMap) -> None:
    """**유도선 색 팔레트의 한계가 무안내 모드를 막으면 안 된다.**

    `max_guided` = 20 은 색 구분의 한계에서 나온 숫자다 (D-006). 무안내 모드에는
    유도선이 없으니 걸릴 이유가 없는데도 똑같이 걸리고 있었다. 그러면 "무안내가
    처리량이 낮다"가 물리적 혼잡 때문인지 팔레트 때문인지 구분되지 않는다
    (docs/ALLOCATION_MODEL.md 7절).
    """
    from sim.control.api import NullControl

    config = SimConfig(seed=0, arrival_rate=0.4, dwell_mean=600.0, max_guided=5)
    blind = Simulation(lot, control=NullControl(), config=config)
    for _frame in blind.run(240.0, stride=100):
        pass

    circulating = sum(1 for v in blind.vehicles if v.parked_t is None)
    assert circulating > config.max_guided, (
        f"무안내인데 주행 차량이 {circulating}대에서 멈췄습니다 "
        f"— 팔레트 상한 {config.max_guided} 이 걸리고 있습니다"
    )


def test_the_road_capacity_comes_from_the_layout(lot: LotMap) -> None:
    """포화는 상수가 아니라 **도로 용량**이어야 한다.

    이게 상수면 포화 실험이 주차장의 용량이 아니라 그 상수를 재게 된다.
    """
    from sim.world.lot_builder import GridSpec, build_grid_lot

    wide = build_grid_lot(GridSpec(aisle_width=20.0))
    assert wide.road_capacity() > lot.road_capacity(), (
        "통로를 넓혔는데 도로 용량이 그대로입니다 — 도면과 연동돼 있지 않습니다"
    )

    sim = Simulation(lot, config=SimConfig())
    assert sim.max_circulating == lot.road_capacity()

    # 명시하면 그것을 쓴다 — 실험이 용량을 스윕할 수 있어야 한다
    pinned = Simulation(lot, config=SimConfig(max_circulating=7))
    assert pinned.max_circulating == 7
