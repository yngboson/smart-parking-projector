"""관제 — 센서 관측만으로 세계를 이해하고 유도선을 발급하는가.

여기서 검사하는 것들은 전부 **관제가 무엇을 알 수 있는가**에 관한 것이다.
관제에 넘기는 입력은 이 파일 안에서도 오직 `SensorEvent` 뿐이며, 그것만으로
강탈과 이탈을 알아채야 한다.
"""

from __future__ import annotations

import pytest

from sim.common.ids import PlateId, SlotId, SlotStatus, SlotType, VehicleClass
from sim.common.lotmap import LotMap
from sim.common.messages import (
    ClearGuidance,
    GuidanceCommand,
    GuidanceReason,
    LaneDetection,
    RouteDeviation,
    SlotOccupancyChanged,
    SlotStolen,
    VehicleEntered,
    VehicleExited,
)
from sim.control.cost import CostWeights, slot_cost, slot_is_eligible
from sim.control.routing import LaneRouter
from sim.control.system import ProjectorControl
from sim.world.lot_builder import GridSpec, build_grid_lot

A = PlateId("11가1111")
B = PlateId("22나2222")
C = PlateId("33다3333")


@pytest.fixture(scope="module")
def lot() -> LotMap:
    return build_grid_lot(GridSpec())


@pytest.fixture
def control(lot: LotMap) -> ProjectorControl:
    return ProjectorControl(lot)


def enter(control: ProjectorControl, plate: PlateId, t: float = 0.0) -> GuidanceCommand:
    """차량을 입장시키고 발급된 유도선을 돌려준다."""
    cmds = control.on_events(
        [VehicleEntered(t=t, plate=plate, vehicle_class=VehicleClass.SEDAN)]
    )
    guidance = [c for c in cmds if isinstance(c, GuidanceCommand)]
    assert len(guidance) == 1, f"{plate} 에게 유도선이 발급되지 않았습니다"
    return guidance[0]


# ── 배정 ──────────────────────────────────────────────────────────


def test_an_arriving_car_gets_a_guidance_line(control: ProjectorControl, lot: LotMap) -> None:
    cmd = enter(control, A)
    assert cmd.reason is GuidanceReason.INITIAL
    assert cmd.target_slot in lot.slots
    assert cmd.polyline[-1] == lot.slots[cmd.target_slot].center, (
        "유도선이 주차면 안까지 들어가야 어느 자리인지 읽힌다"
    )

    # 선은 통로 중심선이 아니라 **실제로 달릴 차선** 위에 그려진다.
    # 중심선에 그리면 우측통행으로 달리는 차가 선 밖으로 가는 것처럼 보인다.
    entry = lot.node_pos(lot.entry_nodes[0])
    sideways = cmd.polyline[0].distance_to(entry)
    assert sideways == pytest.approx(lot.travel_lane_offset, abs=0.05), (
        f"유도선이 차선 위에 있지 않습니다 (중심선에서 {sideways:.2f}m, "
        f"차선은 {lot.travel_lane_offset:.2f}m)"
    )


def test_the_slot_becomes_reserved_not_occupied(control: ProjectorControl) -> None:
    """예약은 물리적 점유가 아니다. **이 틈이 강탈이 일어나는 자리다.**"""
    cmd = enter(control, A)
    belief = control.state.slots[cmd.target_slot]
    assert belief.status is SlotStatus.RESERVED
    assert belief.reserved_for == A
    assert belief.occupant is None


def test_two_cars_never_get_the_same_slot(control: ProjectorControl) -> None:
    first = enter(control, A, t=0.0)
    second = enter(control, B, t=0.1)
    assert first.target_slot != second.target_slot


def test_special_slots_are_never_handed_to_ordinary_cars(control: ProjectorControl, lot: LotMap) -> None:
    """장애인·EV 면은 배정 대상이 아니다. 좋은 자리가 줄어야 강탈 압력이 현실적이다."""
    special = {
        sid for sid, s in lot.slots.items()
        if s.slot_type in (SlotType.DISABLED, SlotType.EV)
    }
    assert special, "도면에 특수 주차면이 없습니다"

    handed = set()
    for i in range(30):
        plate = PlateId(f"{i:02d}가{i:04d}")
        handed.add(enter(control, plate, t=i * 0.1).target_slot)
    assert not (handed & special)


def test_a_van_is_not_sent_to_a_slot_it_cannot_use(lot: LotMap) -> None:
    compact = next(s for s in lot.slots.values() if s.slot_type is SlotType.STANDARD)
    assert slot_is_eligible(compact, VehicleClass.VAN)

    narrow = type(compact)(
        id=compact.id, center=compact.center, heading=compact.heading,
        length=compact.length, width=1.7, slot_type=SlotType.STANDARD,
        access_node=compact.access_node, row=compact.row, index=compact.index,
    )
    assert not slot_is_eligible(narrow, VehicleClass.VAN)


# ── 강탈 감지 ─────────────────────────────────────────────────────


def test_control_detects_a_stolen_slot_from_the_sensor_alone(control: ProjectorControl) -> None:
    """관제는 B 의 성향을 모른다. 예약자가 아닌 번호판이 눌렸다는 사실만 안다."""
    victim_cmd = enter(control, A)
    enter(control, B, t=0.1)
    stolen = victim_cmd.target_slot

    cmds = control.on_events(
        [SlotOccupancyChanged(t=5.0, slot_id=stolen, occupied=True, plate=B)]
    )

    events = [i for i in control.inferences if isinstance(i, SlotStolen)]
    assert len(events) == 1
    assert events[0].victim == A and events[0].taker == B and events[0].slot_id == stolen

    reroute = [
        c for c in cmds
        if isinstance(c, GuidanceCommand) and c.plate == A
    ]
    assert reroute, "피해 차량이 새 유도선을 받지 못했습니다"
    assert reroute[0].reason is GuidanceReason.REROUTE
    assert reroute[0].target_slot != stolen
    assert control.state.vehicles[A].reroute_count == 1


def test_parking_in_your_own_slot_is_not_theft(control: ProjectorControl) -> None:
    cmd = enter(control, A)
    control.on_events(
        [SlotOccupancyChanged(t=5.0, slot_id=cmd.target_slot, occupied=True, plate=A)]
    )
    assert not [i for i in control.inferences if isinstance(i, SlotStolen)]
    assert control.state.vehicles[A].reroute_count == 0


def test_the_thief_loses_their_own_reservation(control: ProjectorControl) -> None:
    """남의 자리를 차지했으면 자기에게 잡혀 있던 자리는 풀려야 한다."""
    victim_cmd = enter(control, A)
    thief_cmd = enter(control, B, t=0.1)

    control.on_events(
        [SlotOccupancyChanged(t=5.0, slot_id=victim_cmd.target_slot, occupied=True, plate=B)]
    )
    assert control.state.slots[thief_cmd.target_slot].status is SlotStatus.FREE


def test_theft_is_recorded_against_the_plate(control: ProjectorControl) -> None:
    """번호판별 관측 이력은 쌓아도 된다 (D-003). 성향이 아니라 행동 기록이다."""
    victim_cmd = enter(control, A)
    enter(control, B, t=0.1)
    control.on_events(
        [SlotOccupancyChanged(t=5.0, slot_id=victim_cmd.target_slot, occupied=True, plate=B)]
    )
    assert control.state.log.of(B).steals == 1
    assert control.state.log.of(A).victim_count == 1
    assert control.state.log.trust(B) < control.state.log.trust(C)


def test_presence_mode_infers_the_occupant_from_lane_detections(lot: LotMap) -> None:
    """번호판을 못 읽는 센서에서는 통로 기록으로 점유자를 추론한다 (D-002)."""
    control = ProjectorControl(lot, slot_sensor_mode="presence")
    victim_cmd = enter(control, A)
    enter(control, B, t=0.1)

    access = lot.slots[victim_cmd.target_slot].access_node
    control.on_events([LaneDetection(t=4.0, node_id=access, plate=B)])
    control.on_events(
        [SlotOccupancyChanged(t=5.0, slot_id=victim_cmd.target_slot, occupied=True, plate=None)]
    )

    stolen = [i for i in control.inferences if isinstance(i, SlotStolen)]
    assert stolen and stolen[0].taker == B


# ── 경로 이탈 ─────────────────────────────────────────────────────


def test_a_car_off_its_route_is_reported_once(control: ProjectorControl, lot: LotMap) -> None:
    cmd = enter(control, A)
    route = control.state.vehicles[A].route
    assert route is not None

    stray = next(n for n in lot.nodes if n not in route.nodes)
    control.on_events([LaneDetection(t=3.0, node_id=stray, plate=A)])
    first = [i for i in control.inferences if isinstance(i, RouteDeviation)]
    assert len(first) == 1 and first[0].plate == A

    control.on_events([LaneDetection(t=3.5, node_id=stray, plate=A)])
    assert not [i for i in control.inferences if isinstance(i, RouteDeviation)], (
        "같은 안내에서 이탈을 두 번 보고하면 안 됩니다"
    )
    assert control.state.log.of(A).deviations == 1


def test_following_the_route_raises_no_deviation(control: ProjectorControl) -> None:
    enter(control, A)
    route = control.state.vehicles[A].route
    assert route is not None
    for i, node in enumerate(route.nodes):
        control.on_events([LaneDetection(t=1.0 + i * 0.5, node_id=node, plate=A)])
        assert not [i for i in control.inferences if isinstance(i, RouteDeviation)]


def test_detectors_past_the_destination_are_not_deviations(control: ProjectorControl, lot: LotMap) -> None:
    """주차면을 지나쳐 정차하는 것은 후진 주차의 정상 절차다.

    목적지 노드 이후의 검지기 반응을 이탈로 세면 모든 주차가 이탈로 기록된다.
    """
    cmd = enter(control, A)
    route = control.state.vehicles[A].route
    assert route is not None
    for node in route.nodes:
        control.on_events([LaneDetection(t=1.0, node_id=node, plate=A)])

    aisle = lot.nodes[route.nodes[-1]].aisle
    beyond = next(
        n for n, node in lot.nodes.items()
        if node.aisle == aisle and n not in route.nodes
    )
    control.on_events([LaneDetection(t=9.0, node_id=beyond, plate=A)])
    assert not [i for i in control.inferences if isinstance(i, RouteDeviation)]


# ── 유도선 소거 ───────────────────────────────────────────────────


def test_guidance_is_cleared_when_the_car_parks(control: ProjectorControl) -> None:
    cmd = enter(control, A)
    cmds = control.on_events(
        [SlotOccupancyChanged(t=5.0, slot_id=cmd.target_slot, occupied=True, plate=A)]
    )
    assert any(isinstance(c, ClearGuidance) and c.plate == A for c in cmds)
    assert control.state.slots[cmd.target_slot].status is SlotStatus.OCCUPIED


def test_leaving_frees_the_slot_and_clears_the_line(control: ProjectorControl) -> None:
    cmd = enter(control, A)
    control.on_events(
        [SlotOccupancyChanged(t=5.0, slot_id=cmd.target_slot, occupied=True, plate=A)]
    )
    cmds = control.on_events(
        [
            SlotOccupancyChanged(t=60.0, slot_id=cmd.target_slot, occupied=False, plate=None),
            VehicleExited(t=65.0, plate=A),
        ]
    )
    assert any(isinstance(c, ClearGuidance) and c.plate == A for c in cmds)
    assert control.state.slots[cmd.target_slot].status is SlotStatus.FREE
    assert control.state.vehicles[A].exited


def test_a_full_lot_hands_out_nothing_and_does_not_crash(lot: LotMap) -> None:
    """만차에서도 죽지 않아야 한다. 자리가 없으면 안내하지 않고 기다린다."""
    control = ProjectorControl(lot)
    for sid in lot.slots:
        control.state.slots[sid].status = SlotStatus.OCCUPIED

    cmds = control.on_events(
        [VehicleEntered(t=0.0, plate=A, vehicle_class=VehicleClass.SEDAN)]
    )
    assert not [c for c in cmds if isinstance(c, GuidanceCommand)]
    assert control.state.vehicles[A].target_slot is None


# ── 비용 함수 ─────────────────────────────────────────────────────


def test_walk_distance_pushes_the_choice_toward_the_building(lot: LotMap) -> None:
    """도보거리 가중치를 0 으로 두면 입구에 가까운 자리를, 크게 두면 건물 앞자리를."""
    entry = lot.entry_nodes[0]
    router = LaneRouter(lot)

    def best(weights: CostWeights) -> SlotId:
        scored = []
        for sid, slot in lot.slots.items():
            if not slot_is_eligible(slot, VehicleClass.SEDAN):
                continue
            route = router.route(entry, slot.access_node)
            if route is None:
                continue
            scored.append((slot_cost(lot, sid, VehicleClass.SEDAN, route, {}, weights), sid))
        return min(scored)[1]

    near_entry = best(CostWeights(walk=0.0))
    near_gate = best(CostWeights(walk=6.0))
    assert lot.walk_distance(near_gate) < lot.walk_distance(near_entry)


def test_congestion_makes_a_busy_route_less_attractive(lot: LotMap) -> None:
    entry = lot.entry_nodes[0]
    router = LaneRouter(lot)
    sid = next(iter(lot.slots))
    route = router.route(entry, lot.slots[sid].access_node)
    assert route is not None

    quiet = slot_cost(lot, sid, VehicleClass.SEDAN, route, {})
    busy = slot_cost(lot, sid, VehicleClass.SEDAN, route, {e: 2 for e in route.edges})
    assert busy > quiet


# ── 유도선의 모양 ─────────────────────────────────────────────────


def test_every_guidance_line_turns_at_right_angles(lot: LotMap) -> None:
    """바닥에 그리는 선은 **지하철 노선도처럼** 직각으로만 꺾인다.

    운전하는 것은 사람이므로 선이 차의 실제 궤적일 필요는 없다. 사람이 한눈에
    읽을 수 있는 모양이면 된다.

    이 검사가 실제로 잡는 것은 미관이 아니라 기하 버그다. 폴리라인을 옆으로 밀 때
    꼭짓점을 이등분선 방향으로만 밀면 밀어낸 선이 원래 코너를 통과하지 못하고
    안쪽으로 잘려, 직각 코너 하나가 **13m 짜리 사선**으로 변한다. 통로가 넓을수록
    길어지므로 도면을 키우면 조용히 나빠진다.
    """
    import math

    from sim.control.system import guidance_polyline

    router = LaneRouter(lot)
    entry = lot.entry_nodes[0]
    checked = 0

    for slot_id, slot in lot.slots.items():
        route = router.route(entry, slot.access_node)
        if route is None:
            continue
        checked += 1
        for a, b in zip(poly := guidance_polyline(lot, route, slot_id), poly[1:]):
            d = b - a
            if d.length < 1e-6:
                continue
            off = math.degrees(math.atan2(d.y, d.x)) % 90.0
            assert min(off, 90.0 - off) < 0.5, (
                f"{slot_id} 유도선에 사선 구간이 있습니다: {d.length:.1f}m, "
                f"{math.degrees(d.angle):.1f}°"
            )

    assert checked > 50, "검사한 유도선이 너무 적습니다"


def test_the_line_is_drawn_on_the_lane_the_car_will_drive(lot: LotMap) -> None:
    """직각으로 만드느라 선이 차선 밖으로 나가면 D-021 이 무너진다."""
    from sim.common.geometry import Vec2
    from sim.control.system import guidance_polyline

    router = LaneRouter(lot)
    slot_id = next(iter(lot.slots))
    slot = lot.slots[slot_id]
    route = router.route(lot.entry_nodes[0], slot.access_node)
    poly = guidance_polyline(lot, route, slot_id)

    aisle = min(a.width for a in lot.aisles)
    for centre in route.polyline:
        nearest = min(p.distance_to(centre) for p in poly)
        assert nearest <= aisle / 2.0, (
            f"유도선이 통로 밖으로 나갔습니다 (중심선에서 {nearest:.1f}m)"
        )


# ── 재방문 ────────────────────────────────────────────────────────


def test_a_returning_car_sets_off_the_gate_camera_again(lot: LotMap) -> None:
    """같은 번호판이 **나갔다 오면** 입구 카메라가 다시 울려야 한다.

    번호판이 관측 목록에 처음 나타날 때만 입장으로 봤더니, 나간 바로 다음 틱에
    돌아온 차는 목록에 빈틈이 없어 입장도 퇴장도 기록되지 않았다. 관제는 그 차를
    영원히 '나가는 중'으로 믿고 자리를 주지 않았고, 그 차가 입구에 서서 나머지
    전부를 막았다 (docs/DECISIONS.md D-026).
    """
    from sim.common.geometry import Pose
    from sim.common.messages import VehicleEntered, VehicleExited
    from sim.common.vehicle import SelfState, VehicleSpec
    from sim.world.sensors import SensorSuite

    class Car:
        def __init__(self, entered_t: float):
            self.plate = PlateId("11가1111")
            self.vehicle_class = VehicleClass.SEDAN
            self.spec = VehicleSpec()
            self.entered_t = entered_t
            self.state = SelfState(pose=Pose(5.0, -3.0, 0.0), speed=1.0, steer=0.0, gear=1)
            self.center = self.state.pose.position
            self.version = 0

    sensors = SensorSuite(lot)
    first = sensors.observe(0.0, [Car(entered_t=0.0)])
    assert any(isinstance(e, VehicleEntered) for e in first)

    # 같은 번호판, 새 방문 — 사이에 빈 틱이 **없다**
    again = sensors.observe(1.0, [Car(entered_t=1.0)])
    assert any(isinstance(e, VehicleExited) for e in again), "이전 방문의 퇴장이 없습니다"
    assert any(isinstance(e, VehicleEntered) for e in again), "재방문이 기록되지 않았습니다"

    order = [type(e).__name__ for e in again if isinstance(e, (VehicleEntered, VehicleExited))]
    assert order.index("VehicleExited") < order.index("VehicleEntered"), (
        "퇴장이 입장보다 먼저 와야 관제가 자리와 유도선을 정리하고 새로 시작한다"
    )


def test_nobody_waits_forever_at_the_entrance(lot: LotMap) -> None:
    """**입구에 갇힌 차 한 대가 주차장 전체를 세운다.**

    자리를 못 받은 차는 입구에 선다. 입구에 선 차는 뒷차의 진입을 막는다. 그러면
    이벤트를 만들 차가 없어 관제의 시계도 멈춘다 (D-025) — 스스로를 가둔다.

    재방문이 잦고 대기열이 긴 조건에서 특히 위험하므로 그 조건에서 확인한다.
    """
    from sim.agents.driver import DriverPhase
    from sim.world.simulation import SimConfig, Simulation

    sim = Simulation(
        lot,
        config=SimConfig(seed=0, arrival_rate=0.25, dwell_mean=200.0, prefill=0.7,
                         returning_share=0.6, noncompliant_share=0.35),
    )
    worst = 0.0
    waiting_since: dict = {}
    for _frame in sim.run(400.0, stride=50):
        arriving = {v.plate for v in sim.vehicles if v.driver.phase is DriverPhase.ARRIVING}
        for plate in arriving:
            waiting_since.setdefault(plate, sim.t)
            worst = max(worst, sim.t - waiting_since[plate])
        for plate in [p for p in waiting_since if p not in arriving]:
            del waiting_since[plate]

    assert worst < 90.0, f"어떤 차가 입구에서 {worst:.0f}초를 기다렸습니다"


# ── 자리 배정 방식 (D-030 · D-031) ────────────────────────────────


def test_walk_only_puts_people_closer_to_the_building(lot: LotMap) -> None:
    """`walk_only` 는 도보 거리 하나만 본다. 그것이 이 극단의 정의다."""
    from sim.control.allocators import api as alloc_api

    router = LaneRouter(lot)
    entry = lot.entry_nodes[0]
    ctx = alloc_api.AllocationContext(
        lot=lot, router=router, available=list(lot.slots), load={}
    )
    req = alloc_api.AllocationRequest(
        plate=PlateId("11가1111"), vehicle_class=VehicleClass.SEDAN, from_node=entry
    )

    got = alloc_api.get("walk_only").allocate([req], ctx)
    assert got, "빈 주차장인데 아무것도 배정하지 않았습니다"

    eligible = [s for s in ctx.candidates(req)]
    best = min(eligible, key=lot.walk_distance)
    assert lot.walk_distance(got[0].slot_id) <= lot.walk_distance(best) + 1e-6


def test_spread_only_fills_every_zone_before_doubling_up(lot: LotMap) -> None:
    """`spread_only` 는 이미 몰린 구역을 피한다.

    한 배치에 구역 수만큼 요청이 들어오면 **서로 다른 구역**으로 흩어져야 한다.
    """
    from sim.control.allocators import api as alloc_api

    router = LaneRouter(lot)
    entry = lot.entry_nodes[0]
    ctx = alloc_api.AllocationContext(
        lot=lot, router=router, available=list(lot.slots), load={}
    )
    requests = [
        alloc_api.AllocationRequest(
            plate=PlateId(f"{i:02d}가1111"),
            vehicle_class=VehicleClass.SEDAN,
            from_node=entry,
            t=float(i),
        )
        for i in range(len(lot.rows))
    ]

    got = alloc_api.get("spread_only").allocate(requests, ctx)
    zones = [lot.slots[a.slot_id].row for a in got]
    assert len(set(zones)) == len(zones), f"같은 구역으로 몰렸습니다: {zones}"


def test_the_two_extremes_disagree_about_where_to_park(lot: LotMap) -> None:
    """**둘이 같은 답을 내면 좌표축이 아니라 점 하나다.**

    극단 대조군의 존재 이유가 "나머지 전략이 어디쯤인지 보여주는 것"이므로,
    둘이 실제로 반대쪽을 가리켜야 한다 (docs/ALLOCATION_MODEL.md 5절).
    """
    from sim.control.allocators import api as alloc_api

    router = LaneRouter(lot)
    entry = lot.entry_nodes[0]
    req = alloc_api.AllocationRequest(
        plate=PlateId("11가1111"), vehicle_class=VehicleClass.SEDAN, from_node=entry
    )

    picks = {}
    for name in ("walk_only", "spread_only"):
        ctx = alloc_api.AllocationContext(
            lot=lot, router=router, available=list(lot.slots), load={}
        )
        got = alloc_api.get(name).allocate([req], ctx)
        picks[name] = lot.walk_distance(got[0].slot_id)

    assert picks["walk_only"] < picks["spread_only"], (
        f"도보 거리가 {picks} — 두 극단이 같은 방향을 가리킵니다"
    )
