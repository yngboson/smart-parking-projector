"""이탈과 강탈 — 이 연구가 실제로 측정하려는 것.

여기서 지켜야 할 성질은 둘이다.

1. **비협조 운전자는 실제로 이탈한다.** 이탈이 안 일어나면 측정할 사건 자체가 없다.
2. **협조적인 운전자만 있으면 강탈은 0 건이다.** 이게 무너지면 센서 오탐이 강탈
   통계에 섞여 들어가고, 복구 전략 비교가 통째로 무의미해진다. 붐비는 조건에서
   특히 위험하므로 만차 근처에서 확인한다.

관제가 강탈을 **센서만으로** 알아채는지는 `test_control.py` 가 본다. 여기서는
차가 실제로 배신하는지를 본다.
"""

from __future__ import annotations

import math
from dataclasses import replace
from random import Random

import pytest

from sim.agents.driver import Driver, DriverPhase, DriverProfile, _keep_right_offset
from sim.common.geometry import Pose, Vec2
from sim.common.ids import SlotId
from sim.common.lotmap import LotMap
from sim.common.messages import Perception, SlotStolen, VisibleSlot
from sim.common.vehicle import SelfState, VehicleSpec
from sim.scenarios.loader import available, load, load_named
from sim.world.lot_builder import GridSpec, build_grid_lot
from sim.world.simulation import SimConfig, Simulation


CROWDED = SimConfig(
    seed=0, arrival_rate=0.25, dwell_mean=700.0, prefill=0.7, noncompliant_share=0.5
)
"""만차에 가까운 조건. 좋은 자리는 이미 임자가 있고 빈 자리는 안쪽에만 남는다."""


@pytest.fixture(scope="module")
def lot() -> LotMap:
    return build_grid_lot(GridSpec())


@pytest.fixture(scope="module")
def crowded(lot: LotMap):
    """붐비는 주차장 한 판. 무거운 실행이라 파일 전체가 나눠 쓴다."""
    # 주차장이 커질수록 사건이 늦게 일어난다 — 차가 더 멀리, 더 오래 달리기 때문이다.
    # 창을 짧게 잡으면 "강탈이 안 일어난다"가 아니라 "아직 안 일어났다"를 보게 된다.
    sim = Simulation(lot, config=CROWDED)
    thefts, rows, peak = [], [], 0.0
    for frame in sim.run(550.0):
        thefts += [i for i in sim.control.inferences if isinstance(i, SlotStolen)]
        rows += [e for e in frame.events if e["type"] == "slot_stolen"]
        peak = max(peak, frame.kpi["occupancy"])
    return sim, thefts, rows, peak


def lane_y(lot: LotMap) -> float:
    """H0 통로(동쪽 일방통행)에서 우측통행 차선의 y 좌표.

    도면에서 계산한다. 좌표를 테스트에 박아 두면 주차 규격을 바꾸는 순간
    조용히 엉뚱한 곳을 시험하게 된다 (CLAUDE.md '도면 좌표를 하드코딩하지 말 것').
    """
    h0 = next(a for a in lot.aisles if a.id == "H0")
    return h0.start.y - _keep_right_offset(lot)


def cruising(lot: LotMap, compliance: float, walk_preference: float = 1.0) -> Driver:
    """H0 통로를 동쪽으로 달리며 먼 자리를 향해 가는 운전자."""
    driver = Driver(
        spec=VehicleSpec(),
        lot=lot,
        profile=DriverProfile(compliance=compliance, walk_preference=walk_preference),
        rng=Random(1),
    )
    y = lane_y(lot)
    east = lot.bounds[2]
    driver._lane = [Vec2(x, y) for x in range(5, int(east) - 5, 5)]
    driver._approach = 0.0
    driver._target_walk = 40.0            # 배정받은 자리는 건물에서 40m
    driver.target_slot = SlotId("B-20")
    driver.phase = DriverPhase.CRUISING
    driver._follower.set_path(driver._lane, gear=1)
    return driver


def on_the_aisle(lot: LotMap, slot_id: str, looks_free: bool = True, walk: float = 8.0):
    return VisibleSlot(
        slot_id=SlotId(slot_id),
        center=lot.slots[SlotId(slot_id)].center,
        looks_free=looks_free,
        walk_distance=walk,
    )


def seeing(lot: LotMap, *slots) -> Perception:
    return Perception(
        t=1.0,
        pose_forward_clearance=math.inf,
        visible_slots=tuple(slots),
    )


def driving_east(lot: LotMap) -> SelfState:
    """통로 서쪽 끝에서 동쪽을 향해 달리는 중."""
    return SelfState(pose=Pose(10.0, lane_y(lot), 0.0), speed=3.0, steer=0.0, gear=1)


# ── 이탈 판단 ─────────────────────────────────────────────────────


def test_a_compliant_driver_never_defects(lot: LotMap) -> None:
    """유혹이 아무리 좋아도 협조적인 운전자는 안내를 따른다."""
    driver = cruising(lot, compliance=1.0)
    before = driver.target_slot

    assert not driver._consider_defection(driving_east(lot), seeing(lot, on_the_aisle(lot, "A-08")))
    assert driver.target_slot == before


def test_a_noncompliant_driver_takes_the_better_spot(lot: LotMap) -> None:
    """눈앞에 더 좋은 자리가 있으면 안내를 무시한다 — 이 연구의 돌발 상황 그 자체."""
    driver = cruising(lot, compliance=0.0)

    assert driver._consider_defection(driving_east(lot), seeing(lot, on_the_aisle(lot, "A-08")))
    assert driver.target_slot == SlotId("A-08")
    assert driver._defected


def test_an_occupied_looking_slot_is_not_tempting(lot: LotMap) -> None:
    driver = cruising(lot, compliance=0.0)
    perception = seeing(lot, on_the_aisle(lot, "A-08", looks_free=False))
    assert not driver._consider_defection(driving_east(lot), perception)


def test_a_slot_already_behind_is_too_late(lot: LotMap) -> None:
    """후진 주차는 자리를 지나쳐 정차한 뒤 들어간다. 코앞의 자리는 이미 늦었다."""
    driver = cruising(lot, compliance=0.0)
    # 통로 동쪽 끝 — A-02 는 한참 뒤에 있다
    state = SelfState(pose=Pose(lot.bounds[2] - 15.0, lane_y(lot), 0.0), speed=3.0, steer=0.0, gear=1)
    assert not driver._consider_defection(state, seeing(lot, on_the_aisle(lot, "A-02")))


def test_a_far_walk_is_not_tempting_even_if_it_is_closer(lot: LotMap) -> None:
    """덜 달리더라도 많이 걸어야 하면 가지 않는다. 사람이 그렇다."""
    driver = cruising(lot, compliance=0.0, walk_preference=1.0)
    driver._target_walk = 5.0        # 배정받은 자리가 이미 건물 코앞이다
    perception = seeing(lot, on_the_aisle(lot, "A-08", walk=60.0))
    assert not driver._consider_defection(driving_east(lot), perception)


def test_the_same_slot_is_only_considered_once(lot: LotMap) -> None:
    """한 번 지나친 자리를 계속 다시 고민하면, 아무리 협조적이어도 결국 이탈한다.

    compliance 가 '유혹 한 번을 참을 확률'이므로 유혹을 반복 판정하면 의미가 없어진다.
    """
    driver = cruising(lot, compliance=0.999)
    perception = seeing(lot, on_the_aisle(lot, "A-08"))
    for _ in range(200):
        driver._consider_defection(driving_east(lot), perception)
    assert driver._considered == {SlotId("A-08")}
    assert not driver._defected


def test_slots_belonging_to_another_aisle_are_ignored(lot: LotMap) -> None:
    """수직 통로를 달릴 때 옆으로 보이는 자리는 **다른 통로 소속**이다.

    그리로 꺾어 들어가면 진입 방향이 맞지 않아 주차가 성립하지 않는다.
    """
    driver = cruising(lot, compliance=0.0)
    west_aisle = next(a for a in lot.aisles if a.axis == "v")
    heading_north = SelfState(
        pose=Pose(west_aisle.start.x, 20.0, math.pi / 2), speed=3.0, steer=0.0, gear=1
    )
    assert not driver._is_on_this_aisle(SlotId("C-01"), heading_north)

    assert driver._is_on_this_aisle(SlotId("A-08"), driving_east(lot))


def test_a_driver_gives_up_when_the_target_is_visibly_taken(lot: LotMap) -> None:
    """가려던 자리가 차 있는 것이 보이면 멈추고 새 안내를 기다린다."""
    driver = cruising(lot, compliance=1.0)
    driver.target_slot = SlotId("A-08")

    taken = seeing(lot, on_the_aisle(lot, "A-08", looks_free=False))
    assert driver._abandon_if_taken(taken)
    assert driver.phase is DriverPhase.ARRIVING
    assert driver.target_slot is None


def test_walk_weight_spans_the_documented_range() -> None:
    assert DriverProfile(walk_preference=0.0).walk_weight == pytest.approx(1.0)
    assert DriverProfile(walk_preference=1.0).walk_weight == pytest.approx(5.0)
    assert DriverProfile(walk_preference=0.5).walk_weight == pytest.approx(3.0)


# ── 시뮬레이션 전체 ───────────────────────────────────────────────


def test_a_crowded_lot_of_compliant_drivers_reports_no_theft(lot: LotMap) -> None:
    """**이 파일에서 가장 중요한 검사.**

    아무도 배신하지 않는데 강탈이 보고되면 그것은 센서 오탐이고, 강탈 통계 전체가
    오염된다. 만차 근처에서 특히 위험하다 — 자리가 계속 주인을 바꾸기 때문이다.
    """
    sim = Simulation(lot, config=replace(CROWDED, noncompliant_share=0.0))
    thefts = []
    peak = 0.0
    for frame in sim.run(250.0):
        thefts += [i for i in sim.control.inferences if isinstance(i, SlotStolen)]
        peak = max(peak, frame.kpi["occupancy"])

    assert peak > 0.6, f"충분히 붐비지 않았습니다 (최대 점유율 {peak:.0%})"
    assert not thefts, f"협조적인 운전자만 있는데 강탈이 {len(thefts)}건 보고됐습니다"


def test_noncompliant_drivers_actually_steal_slots(crowded, lot: LotMap) -> None:
    """비협조 운전자가 섞이면 강탈이 일어나고, 관제가 피해 차량을 재배정한다."""
    sim, thefts, _rows, _peak = crowded

    assert thefts, "비협조 운전자가 절반인데 강탈이 한 건도 없습니다"
    for t in thefts:
        assert t.victim != t.taker
        assert t.slot_id in lot.slots

    reroutes = sum(v.reroute_count for v in sim.control.state.vehicles.values())
    assert reroutes >= len(thefts), "피해 차량이 재배정을 못 받았습니다"


def test_theft_leaves_a_trace_in_the_frame(crowded) -> None:
    """강탈은 화면에도 떠야 한다. 발표에서 가장 중요한 순간이다."""
    _sim, _thefts, rows, _peak = crowded

    assert rows
    assert set(rows[0]) == {"type", "victim", "taker", "slot"}


def test_defection_is_reproducible(lot: LotMap) -> None:
    """같은 시드면 같은 배신. 전략을 비교하려면 이게 성립해야 한다."""
    def thefts(seed: int) -> int:
        sim = Simulation(lot, config=replace(CROWDED, seed=seed))
        n = 0
        for _f in sim.run(150.0):
            n += sum(1 for i in sim.control.inferences if isinstance(i, SlotStolen))
        return n

    assert thefts(4) == thefts(4)


# ── 시나리오 파일 ─────────────────────────────────────────────────


def test_every_committed_scenario_loads() -> None:
    names = available()
    assert {"light", "busy", "rush_hour"} <= set(names)
    for name in names:
        s = load_named(name)
        assert s.description.strip(), f"{name}: 설명이 비어 있습니다"
        assert s.seeds and s.duration > 0
        assert 0.0 <= s.config.noncompliant_share <= 1.0


def test_a_scenario_typo_fails_loudly(tmp_path) -> None:
    """오타 하나로 도착률이 기본값으로 돌아간 채 30번 돌리는 일을 막는다."""
    path = tmp_path / "broken.yaml"
    path.write_text("simulation:\n  arival_rate: 0.5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="arival_rate"):
        load(path)


def test_seeds_are_applied_without_touching_the_rest() -> None:
    s = load_named("rush_hour")
    config = s.config_for(11)
    assert config.seed == 11
    assert config.arrival_rate == s.config.arrival_rate
    assert config.prefill == s.config.prefill
