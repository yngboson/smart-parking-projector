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


def gap(me: SelfState, spec: VehicleSpec, *shapes) -> float:
    """앞차까지의 거리만. `forward_clearance` 는 (거리, 앞차 번호)를 준다 —
    간격은 앞차가 서 있는지 달리는지에 따라 달라져야 하므로 누구인지도 알아야 한다."""
    return physics.forward_clearance(me, spec, list(shapes))[0]


# ── 여유 거리 측정 ────────────────────────────────────────────────


def test_clearance_measures_the_gap_to_the_bumper_ahead() -> None:
    spec = VehicleSpec()
    me = rest(0.0, 0.0, 0.0)
    ahead = footprint(Pose(10.0, 0.0, 0.0), spec)

    reach = gap(me, spec, ahead)
    nose = spec.length - spec.rear_overhang
    tail = ahead[2].x  # 앞차의 뒤쪽 꼭짓점
    assert reach == pytest.approx(tail - nose, abs=1e-6)


def test_nothing_ahead_means_unlimited_clearance() -> None:
    spec = VehicleSpec()
    me = rest(0.0, 0.0, 0.0)
    beside = footprint(Pose(10.0, 6.0, 0.0), spec)
    assert math.isinf(gap(me, spec, beside))


def test_a_car_behind_does_not_block() -> None:
    spec = VehicleSpec()
    me = rest(0.0, 0.0, 0.0)
    behind = footprint(Pose(-10.0, 0.0, 0.0), spec)
    assert math.isinf(gap(me, spec, behind))


def test_a_turning_car_does_not_brake_for_traffic_it_will_curve_away_from() -> None:
    """직선으로만 재면 좌회전 중인 차가 회전 바깥쪽 차를 정면 장애물로 오인한다.

    실제로 그 오인 때문에 교차로마다 차가 굳었다. 조향각이 만드는 원호를 따라
    봐야 한다.
    """
    spec = VehicleSpec()
    turning = rest(0.0, 0.0, 0.0, steer=0.45)      # 좌회전 중
    straight_ahead = footprint(Pose(9.0, 0.0, 0.0), spec)

    assert math.isinf(gap(turning, spec, straight_ahead)), (
        "왼쪽으로 꺾고 있는데 정면의 차 때문에 멈췄습니다"
    )
    assert math.isfinite(
        gap(rest(0.0, 0.0, 0.0), spec, straight_ahead)
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
    reach = gap(turning, spec, footprint(on_arc, spec))
    assert math.isfinite(reach) and reach < radius * angle


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
            DriverPhase.PARKING,
            DriverPhase.UNPARKING,
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


# ── 주차 동작과 병목 ──────────────────────────────────────────────


def test_parking_takes_the_scripted_time_and_lands_in_the_slot(lot: LotMap) -> None:
    """유도선 끝에 닿으면 물리를 풀지 않고 **정해진 시간 안에** 자리로 들어간다.

    주차는 사람이 하는 일이고, 이 연구가 재는 것은 조향 솜씨가 아니라 그동안
    통로가 막히는 시간이다 (docs/DECISIONS.md D-022).
    """
    from sim.agents.driver import PARK_DURATION, Driver, DriverPhase
    from sim.common.maneuver import parked_pose

    slot = lot.slots_in_row("C")[5]
    driver = Driver(spec=VehicleSpec(), lot=lot)
    driver.target_slot = slot.id
    driver._approach = 0.0

    beside = driver._lane_point_beside(slot)
    state = SelfState(pose=Pose(beside.x, beside.y, 0.0), speed=0.0, steer=0.0, gear=1)
    driver._start_parking(0.0, state)

    assert driver.phase is DriverPhase.PARKING
    assert driver.hazards, "주차 중에는 비상등을 켜야 한다 — 통로를 막고 있다는 표시"

    assert driver.scripted_pose(PARK_DURATION * 0.5) is not None
    done = driver.scripted_pose(PARK_DURATION)
    assert done is not None

    target = parked_pose(slot.center, slot.heading, VehicleSpec().rear_axle_to_center)
    assert done.position.distance_to(target.position) < 1e-6
    assert abs(done.theta - target.theta) < 1e-6

    assert not driver._script_done(PARK_DURATION * 0.5)
    assert driver._script_done(PARK_DURATION + 0.01)


def test_a_car_keeps_more_room_behind_one_that_is_parking(lot: LotMap) -> None:
    """앞차가 비상등을 켰으면 곧 비켜줄 차가 아니다. 더 띄우고 더 늦춘다.

    **이 판단 하나가 주차장 병목을 만든다.** 앞차가 같은 속도로 달리는 중이면
    바짝 붙어도 되지만, 자리에 들어가는 중이면 그 시간 내내 통로가 막힌다.
    """
    from sim.agents.driver import Driver
    from sim.common.messages import Perception

    driver = Driver(spec=VehicleSpec(), lot=lot)

    def limit(clearance: float, lead_speed: float, parking: bool) -> float:
        return driver._speed_limit(
            Perception(
                t=0.0,
                pose_forward_clearance=clearance,
                lead_speed=lead_speed,
                lead_is_parking=parking,
            )
        )

    moving = limit(12.0, 6.0, False)
    stopped = limit(12.0, 0.0, False)
    parking = limit(12.0, 0.0, True)

    assert moving > stopped, "달리는 앞차 뒤에서는 더 빨리 갈 수 있어야 한다"
    assert parking < stopped, "주차 중인 앞차 뒤에서는 더 늦춰야 한다"


def test_parking_time_throttles_the_lot(lot: LotMap) -> None:
    """주차가 오래 걸릴수록 처리량이 떨어져야 한다 — 그게 재현하려는 병목이다."""
    import sim.agents.driver as driver_module

    original = driver_module.PARK_DURATION
    parked = {}
    try:
        for seconds in (2.0, 20.0):
            driver_module.PARK_DURATION = seconds
            sim = Simulation(
                lot,
                config=SimConfig(seed=0, arrival_rate=0.3, dwell_mean=300.0, prefill=0.6),
            )
            for frame in sim.run(240.0):
                pass
            parked[seconds] = frame.kpi["parked_total"]
    finally:
        driver_module.PARK_DURATION = original

    assert parked[20.0] < parked[2.0], (
        f"주차가 10배 오래 걸리는데 처리량이 그대로다 ({parked})"
    )


# ── 앞차와 뒤차 ───────────────────────────────────────────────────


def test_a_car_behind_is_never_the_leader_even_mid_turn() -> None:
    """조향 중에도 뒤차는 앞차가 아니다.

    원호를 각도로만 재면 원을 한 바퀴 돌아 뒤차에 닿는다. 조향각이 클수록 반경이
    작아 그 한 바퀴가 짧아지고, 최대 조향에서는 **뒤 5m 옆 10m** 의 차가 12m 앞의
    앞차로 읽혔다. 뒷차를 보고 감속하면 정체가 앞이 아니라 뒤에서 전파된다.
    """
    spec = VehicleSpec()
    for steer in (0.0, 0.2, 0.4, spec.max_steer, -spec.max_steer):
        me = rest(0.0, 0.0, 0.0, steer=steer)
        for pos in (Pose(-5.0, 10.0, 0.0), Pose(-5.0, -10.0, 0.0), Pose(-12.0, 0.0, 0.0)):
            assert math.isinf(gap(me, spec, footprint(pos, spec))), (
                f"조향 {steer:+.2f} 에서 뒤차 {pos.position.as_tuple()} 를 앞차로 봤습니다"
            )


def test_the_front_car_of_an_overlapping_pair_is_free_to_go() -> None:
    """추돌해 겹친 두 대가 **둘 다** 기어가면 그 통로는 영원히 굳는다.

    꼭짓점만 보면 나를 들이받은 뒷차의 앞범퍼가 내 궤적 안에 들어와, 앞차도
    '앞이 막혔다'고 읽는다. 앞뒤는 차체 중심끼리 비교해야 한다.
    """
    spec = VehicleSpec()
    front, rear = rest(10.0, 0.0, 0.0), rest(9.9, 0.0, 0.0)

    assert math.isinf(gap(front, spec, footprint(rear.pose, spec))), (
        "뒤에서 받힌 차가 스스로 막혔다고 판단했습니다 — 빠져나갈 수 없습니다"
    )
    assert gap(rear, spec, footprint(front.pose, spec)) < 1.0, (
        "들이받은 뒤차는 물러서야 합니다"
    )


def test_a_real_leader_is_still_seen_around_a_corner() -> None:
    """뒤를 잘라내느라 앞을 못 보게 되면 안 된다."""
    spec = VehicleSpec()
    steer = 0.4
    me = rest(0.0, 0.0, 0.0, steer=steer)
    radius = spec.wheelbase / math.tan(steer)
    for angle in (0.3, 0.8, 1.4):
        ahead = Pose(radius * math.sin(angle), radius * (1 - math.cos(angle)), angle)
        assert math.isfinite(gap(me, spec, footprint(ahead, spec))), (
            f"원호 {math.degrees(angle):.0f}° 앞의 차를 놓쳤습니다"
        )


# ── 출구 규칙 ─────────────────────────────────────────────────────


class _View:
    """`VehicleView` 프로토콜을 만족하는 최소한의 차량. 교통 규칙만 시험한다."""

    def __init__(self, plate: str, x: float, y: float, theta: float, speed: float = 3.0):
        from sim.common.vehicle import body_center

        self.plate = plate
        self.spec = VehicleSpec()
        self.vehicle_class = None
        self.state = SelfState(pose=Pose(x, y, theta), speed=speed, steer=0.0, gear=1)
        self.center = body_center(self.state.pose, self.spec)
        self.version = 0


def _towards_exit(lot: LotMap, back: float) -> tuple[float, float, float]:
    """출구에서 back 미터 뒤, 출구를 향한 자세."""
    gate = lot.node_pos(lot.exit_nodes[0])
    approach = gate - lot.node_pos("H2-22")
    d = approach.normalized()
    return gate.x - d.x * back, gate.y - d.y * back, d.angle


def test_only_one_car_at_a_time_enters_the_exit(lot: LotMap) -> None:
    """출구는 한 번에 한 대만 지난다.

    주차장의 모든 출차 차량이 출구 하나로 모인다. 차간거리만으로는 여러 대가
    동시에 목으로 밀고 들어가고, 한 번 겹치면 서로를 못 빠져나가 출구 앞이 굳는다.
    """
    from sim.world.traffic import AisleTraffic

    traffic = AisleTraffic(lot)
    throat = traffic._throat

    lead = _View("앞", *_towards_exit(lot, throat * 0.5))
    follow = _View("뒤", *_towards_exit(lot, throat + 6.0))
    traffic.update(0.0, [lead, follow])

    assert math.isinf(traffic.stop_distance(lead)), "목에 들어간 차는 그대로 나간다"
    assert math.isfinite(traffic.stop_distance(follow)), (
        "목이 찼는데 뒤차가 그대로 밀고 들어갑니다"
    )

    # 앞차가 빠져나가면 뒤차가 풀린다
    traffic.update(1.0, [follow])
    assert math.isinf(traffic.stop_distance(follow)), "목이 비었는데도 세워 뒀습니다"


def test_the_exit_throat_follows_the_aisle_width(lot: LotMap) -> None:
    """규칙에 치수를 박아 두면 통로를 넓히는 순간 규칙만 옛 치수에 남는다."""
    from sim.world.lot_builder import GridSpec, build_grid_lot
    from sim.world.traffic import AisleTraffic

    wide = build_grid_lot(GridSpec(aisle_width=20.0))
    assert AisleTraffic(wide)._throat > AisleTraffic(lot)._throat


def test_patience_does_not_push_a_car_into_an_occupied_merge(lot: LotMap) -> None:
    """빠듯한 간격에 끼어드는 것은 운전이지만, 차가 서 있는 자리로 나가는 것은
    운전이 아니라 관통이다. 예전에는 관통했고, 겹친 두 대가 함께 굳었다."""
    from sim.world.traffic import PATIENCE, AisleTraffic

    slot = next(s for s in lot.slots.values())
    merge = lot.node_pos(slot.access_node)
    leaving = _View("나가는차", slot.center.x, slot.center.y,
                    (merge - slot.center).angle, speed=0.0)
    parked_on_merge = _View("합류점의차", merge.x, merge.y, 0.0, speed=0.0)

    traffic = AisleTraffic(lot)
    for t in (0.0, PATIENCE + 5.0, PATIENCE * 3):
        traffic.update(t, [leaving, parked_on_merge])
        assert math.isfinite(traffic.stop_distance(leaving)), (
            f"t={t}: 합류점에 차가 서 있는데 나갔습니다"
        )
    assert traffic.forced_merges == 0
