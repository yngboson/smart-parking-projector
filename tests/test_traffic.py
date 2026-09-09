"""교통 흐름 — 차간거리, 합류, 그리고 **교착이 없는가**.

이 파일의 검사들은 전부 실제로 터진 버그에서 나왔다. 3단계에서 차간거리를 배선한
순간 시뮬레이션이 통째로 굳었고, 원인을 찾는 데 네 번의 시도가 필요했다
(경위는 `sim/world/traffic.py` 와 docs/DECISIONS.md D-012).

**교착은 기능 하나가 고장 난 것이 아니라 실험 자체를 무의미하게 만든다.**
멈춘 시뮬레이션은 어떤 복구 전략이 나은지 말해주지 못한다. 그래서 여기 있는
`test_the_simulation_never_deadlocks` 는 이 저장소에서 두 번째로 중요한 테스트다.
"""

from __future__ import annotations

import math

import pytest

from sim.agents.driver import DriverPhase
from sim.common.geometry import Pose, Vec2
from sim.common.lotmap import LotMap
from sim.common.vehicle import SelfState, VehicleSpec, footprint
from sim.world import physics
from sim.world.lot_builder import GridSpec, build_grid_lot
from sim.world.simulation import SimConfig, Simulation

STUCK_LIMIT = 25.0
"""이보다 오래 꼼짝 못 하는 차가 있으면 교착으로 본다(초).

운전자의 안전망(`STUCK_PATIENCE` = 15초)보다 넉넉하게 잡았다. 안전망이 도는 데
걸리는 시간까지는 봐준다.
"""


@pytest.fixture(scope="module")
def lot() -> LotMap:
    return build_grid_lot(GridSpec())


def rest(x: float, y: float, theta: float, steer: float = 0.0) -> SelfState:
    return SelfState(pose=Pose(x, y, theta), speed=1.0, steer=steer, gear=1)


# ── 여유 거리 측정 ────────────────────────────────────────────────


def test_clearance_measures_the_gap_to_the_bumper_ahead() -> None:
    spec = VehicleSpec()
    me = rest(0.0, 0.0, 0.0)
    ahead = footprint(Pose(10.0, 0.0, 0.0), spec)

    gap = physics.forward_clearance(me, spec, [ahead])
    nose = spec.length - spec.rear_overhang
    tail = ahead[2].x  # 앞차의 뒤쪽 꼭짓점
    assert gap == pytest.approx(tail - nose, abs=1e-6)


def test_nothing_ahead_means_unlimited_clearance() -> None:
    spec = VehicleSpec()
    me = rest(0.0, 0.0, 0.0)
    beside = footprint(Pose(10.0, 6.0, 0.0), spec)
    assert math.isinf(physics.forward_clearance(me, spec, [beside]))


def test_a_car_behind_does_not_block() -> None:
    spec = VehicleSpec()
    me = rest(0.0, 0.0, 0.0)
    behind = footprint(Pose(-10.0, 0.0, 0.0), spec)
    assert math.isinf(physics.forward_clearance(me, spec, [behind]))


def test_a_turning_car_does_not_brake_for_traffic_it_will_curve_away_from() -> None:
    """직선으로만 재면 좌회전 중인 차가 회전 바깥쪽 차를 정면 장애물로 오인한다.

    실제로 그 오인 때문에 교차로마다 차가 굳었다. 조향각이 만드는 원호를 따라
    봐야 한다.
    """
    spec = VehicleSpec()
    turning = rest(0.0, 0.0, 0.0, steer=0.45)      # 좌회전 중
    straight_ahead = footprint(Pose(9.0, 0.0, 0.0), spec)

    assert math.isinf(physics.forward_clearance(turning, spec, [straight_ahead])), (
        "왼쪽으로 꺾고 있는데 정면의 차 때문에 멈췄습니다"
    )
    assert math.isfinite(
        physics.forward_clearance(rest(0.0, 0.0, 0.0), spec, [straight_ahead])
    ), "직진 중이라면 같은 차가 장애물이어야 합니다"


def test_a_turning_car_still_sees_traffic_on_its_arc() -> None:
    """원호를 따라 본다고 해서 눈이 머는 것은 아니다."""
    spec = VehicleSpec()
    steer = 0.45
    turning = rest(0.0, 0.0, 0.0, steer=steer)

    radius = spec.wheelbase / math.tan(steer)
    angle = 0.6
    on_arc = Pose(
        radius * math.sin(angle), radius - radius * math.cos(angle), angle
    )
    gap = physics.forward_clearance(turning, spec, [footprint(on_arc, spec)])
    assert math.isfinite(gap) and gap < radius * angle


# ── 주차면 안의 차는 통로 장애물이 아니다 ─────────────────────────


def test_a_car_in_a_slot_is_not_an_obstacle_on_the_aisle(lot: LotMap) -> None:
    """옆칸에 세워진 차를 장애물로 치면 주차면에서 나오는 차가 스스로 갇힌다.

    통로가 도로이고 주차면은 도로 밖이다.
    """
    sim = Simulation(lot, config=SimConfig(seed=4, arrival_rate=0.15))
    for _f in sim.run(240.0):
        pass

    parked = sim.traffic.in_slot
    assert parked, "주차면 안에 있는 차량이 하나도 잡히지 않았습니다"
    for plate in parked:
        v = next(w for w in sim.vehicles if w.plate == plate)
        assert v.driver.phase in (
            DriverPhase.PARKED,
            DriverPhase.REVERSING,
            DriverPhase.STAGING,
            DriverPhase.LEAVING,
        ), f"{plate} 가 주차면 안에 있는데 상태가 {v.driver.phase}"


# ── 교착 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("seed", [0, 3])
def test_the_simulation_never_deadlocks(lot: LotMap, seed: int) -> None:
    """**이 저장소에서 두 번째로 중요한 테스트.**

    한 대라도 영원히 굳으면 그 통로가 굳고, 결국 주차장이 굳는다. 멈춘 시뮬레이션은
    어떤 복구 전략이 나은지 말해주지 못하므로, 교착은 성능 문제가 아니라
    **실험 타당성 문제**다.
    """
    sim = Simulation(lot, config=SimConfig(seed=seed, arrival_rate=0.15))
    stalled: dict[str, int] = {}
    worst: tuple[float, str] = (0.0, "")

    for f in sim.run(450.0):
        for v in sim.vehicles:
            moving_phase = v.driver.phase in (DriverPhase.CRUISING, DriverPhase.LEAVING)
            if moving_phase and v.state.speed < 0.05:
                stalled[v.plate] = stalled.get(v.plate, 0) + 1
                held = stalled[v.plate] * sim.config.dt
                if held > worst[0]:
                    worst = (held, v.plate)
            else:
                stalled[v.plate] = 0

    assert worst[0] < STUCK_LIMIT, (
        f"{worst[1]} 가 {worst[0]:.0f}초 동안 움직이지 못했습니다 — 교착입니다"
    )


def test_traffic_keeps_flowing_as_arrivals_increase(lot: LotMap) -> None:
    """도착률을 올려도 흐름이 무너지지 않아야 한다.

    교착은 밀도가 올라갈 때 터진다. 낮은 도착률만 시험하면 놓친다.
    """
    for rate in (0.08, 0.20):
        sim = Simulation(lot, config=SimConfig(seed=1, arrival_rate=rate))
        stopped = total = 0
        for _f in sim.run(300.0):
            moving = [
                v for v in sim.vehicles
                if v.driver.phase in (DriverPhase.CRUISING, DriverPhase.LEAVING)
            ]
            total += len(moving)
            stopped += sum(1 for v in moving if v.state.speed < 0.05)

        ratio = stopped / max(1, total)
        assert ratio < 0.35, (
            f"도착률 {rate} 에서 주행 차량의 {ratio:.0%} 가 멈춰 있습니다"
        )


# ── 센서 오탐 ─────────────────────────────────────────────────────


def test_a_car_pulling_out_does_not_trigger_the_neighbours_sensor(lot: LotMap) -> None:
    """빠져나가는 중인 차를 옆칸에 주차한 것으로 읽으면, 관제가 **없던 강탈**을
    보고한다. 강탈 횟수가 이 연구의 핵심 지표이므로 오탐은 결과를 오염시킨다.
    """
    from sim.world.sensors import SensorSuite

    sensors = SensorSuite(lot)
    slot = lot.slots_in_row("E")[3]
    spec = VehicleSpec()

    class Fake:
        """센서가 보는 최소한의 차량 (`world.sensors.VehicleView`)."""

        plate = "99가9999"
        vehicle_class = None

        def __init__(self, state):
            from sim.common.vehicle import body_center

            self.spec = spec
            self.state = state
            self.center = body_center(state.pose, spec)
            self.version = 0

    # 주차면 입구에 걸친 채 통로 쪽으로 빠져나가는 중
    out = Vec2.from_angle(slot.heading) * 1.9
    moving_out = Fake(
        SelfState(
            pose=Pose(slot.center.x + out.x, slot.center.y + out.y, slot.heading),
            speed=0.45, steer=0.2, gear=1,
        )
    )
    assert sensors._slot_under(moving_out) is None or moving_out.state.speed > 0.2

    settled = Fake(
        SelfState(
            pose=Pose(
                slot.center.x - math.cos(slot.heading) * spec.rear_axle_to_center,
                slot.center.y - math.sin(slot.heading) * spec.rear_axle_to_center,
                slot.heading,
            ),
            speed=0.0, steer=0.0, gear=0,
        )
    )
    assert sensors._slot_under(settled) == slot.id, "반듯이 댄 차를 못 읽습니다"
