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
    assert cmd.polyline[0] == lot.node_pos(lot.entry_nodes[0])
    assert cmd.polyline[-1] == lot.slots[cmd.target_slot].center, (
        "유도선이 주차면 안까지 들어가야 어느 자리인지 읽힌다"
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
