"""관제의 세계 모델 — 센서 이벤트만으로 갱신되는 추정.

**여기 있는 것은 사실이 아니라 믿음이다.** 관제는 주차장을 내려다볼 수 없다.
입구 카메라가 번호판을 읽었고, 어느 주차면 센서가 눌렸고, 어느 통로 검지기를
누가 지나갔다 — 그것뿐이다. 그 사이의 일은 전부 추론이다.

그래서 클래스 이름이 `Belief` 로 끝난다. 이름이 계속 상기시켜 주지 않으면
언젠가 누군가 "월드에서 그냥 가져오면 되는데" 라고 생각하게 된다.

**강탈은 여기서 발견된다.** 방법은 하나뿐이다:

    예약해 둔 주차면에 예약자가 아닌 번호판이 들어왔다.

운전자의 성향을 보고 미리 아는 것이 아니라, 이미 벌어진 뒤에 센서로 안다.
이 지연이 이 연구가 측정하려는 대상 그 자체다.

번호판별 과거 관측 이력(`ObservationLog`)은 쌓아도 된다 —
센서 기록의 집계일 뿐이며 실제 시스템도 할 수 있는 일이다 (docs/DECISIONS.md D-003).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sim.common.ids import NodeId, PlateId, SlotId, SlotStatus, VehicleClass
from sim.common.lotmap import LotMap
from sim.common.messages import (
    ControlInference,
    LaneDetection,
    RouteDeviation,
    SensorEvent,
    SlotOccupancyChanged,
    SlotStolen,
    VehicleEntered,
    VehicleExited,
)
from sim.control.routing import EdgeKey, Route


@dataclass(slots=True)
class SlotBelief:
    """주차면 하나에 대한 관제의 믿음."""

    slot_id: SlotId
    status: SlotStatus = SlotStatus.FREE
    occupant: PlateId | None = None
    """센서가 읽은 점유 차량. presence 모드에서는 추론값이라 틀릴 수 있다."""

    reserved_for: PlateId | None = None
    since: float = 0.0

    @property
    def is_available(self) -> bool:
        return self.status is SlotStatus.FREE


@dataclass(slots=True)
class VehicleBelief:
    """차량 하나에 대한 관제의 믿음. 차량 '객체'가 아니라 번호판에 붙은 기록이다."""

    plate: PlateId
    vehicle_class: VehicleClass
    entered_t: float

    last_node: NodeId | None = None
    prev_node: NodeId | None = None
    """직전에 지나간 노드. 재할당 경로의 첫 회전 비용을 제대로 매기려면 필요하다."""

    last_seen_t: float = 0.0

    target_slot: SlotId | None = None
    route: Route | None = None
    route_index: int = 0
    """경로 노드 중 어디까지 지나갔는지. 이탈 판정의 기준점."""

    revision: int = 0
    reroute_count: int = 0
    """자기 자리를 빼앗겨 다시 안내받은 횟수."""

    reshuffle_count: int = 0
    """남의 강탈을 흡수하느라 목적지가 바뀐 횟수. 본인은 피해자가 아니다."""

    parked_slot: SlotId | None = None
    exited: bool = False
    deviated: bool = False
    """이번 안내에서 이미 이탈 판정을 냈는가. 한 번만 보고한다."""

    @property
    def needs_assignment(self) -> bool:
        return (
            not self.exited
            and self.parked_slot is None
            and self.target_slot is None
        )

    @property
    def is_en_route(self) -> bool:
        return not self.exited and self.parked_slot is None and self.target_slot is not None


@dataclass(slots=True)
class PlateRecord:
    """번호판 하나의 누적 관측. 성향이 아니라 **행동 기록**이다."""

    deviations: int = 0
    steals: int = 0
    victim_count: int = 0
    visits: int = 0


@dataclass(slots=True)
class ObservationLog:
    """번호판별 과거 관측 이력 (D-003).

    복구 전략 `reputation_aware` 가 읽는다. 여기 들어가도 되는 것은 센서로 관측된
    사건의 횟수뿐이다. 성향 파라미터를 역추정해 저장하는 것은 금지다.
    """

    records: dict[PlateId, PlateRecord] = field(default_factory=dict)

    def of(self, plate: PlateId) -> PlateRecord:
        return self.records.setdefault(plate, PlateRecord())

    def trust(self, plate: PlateId) -> float:
        """0~1. 관측된 이탈·강탈이 없을수록 1 에 가깝다.

        방문 이력이 없는 차량은 1.0 — 의심할 근거가 없으면 의심하지 않는다.
        """
        r = self.records.get(plate)
        if r is None:
            return 1.0
        bad = r.deviations + 2 * r.steals
        return 1.0 / (1.0 + bad)


class ControlState:
    """센서 이벤트를 받아 갱신되는 관제의 세계 모델."""

    def __init__(self, lot: LotMap, slot_sensor_mode: str = "anpr") -> None:
        self.lot = lot
        self.slot_sensor_mode = slot_sensor_mode
        self.t = 0.0
        self.slots: dict[SlotId, SlotBelief] = {
            sid: SlotBelief(sid) for sid in lot.slots
        }
        self.vehicles: dict[PlateId, VehicleBelief] = {}
        self.log = ObservationLog()

    # ── 이벤트 반영 ────────────────────────────────────────────────

    def apply(self, events: list[SensorEvent]) -> list[ControlInference]:
        """센서 이벤트를 반영하고, 그로부터 **추론된** 사건들을 돌려준다."""
        out: list[ControlInference] = []
        for e in events:
            self.t = max(self.t, e.t)
            if isinstance(e, VehicleEntered):
                self._on_entered(e)
            elif isinstance(e, SlotOccupancyChanged):
                out.extend(self._on_occupancy(e))
            elif isinstance(e, LaneDetection):
                out.extend(self._on_lane(e))
            elif isinstance(e, VehicleExited):
                self._on_exited(e)
        return out

    def _on_entered(self, e: VehicleEntered) -> None:
        v = self.vehicles.get(e.plate)
        if v is None:
            v = VehicleBelief(e.plate, e.vehicle_class, e.t)
            self.vehicles[e.plate] = v
        else:  # 재방문 — 기록은 이어지고 배정만 초기화된다
            v.vehicle_class = e.vehicle_class
            v.entered_t = e.t
            v.target_slot = None
            v.route = None
            v.route_index = 0
            v.parked_slot = None
            v.exited = False
            v.deviated = False
        v.last_seen_t = e.t
        if self.lot.entry_nodes:
            v.last_node = self.lot.entry_nodes[0]
        self.log.of(e.plate).visits += 1

    def _on_occupancy(self, e: SlotOccupancyChanged) -> list[ControlInference]:
        belief = self.slots.get(e.slot_id)
        if belief is None:
            return []

        if not e.occupied:
            self._vacate(belief, e.t)
            return []

        occupant = e.plate if e.plate is not None else self._infer_occupant(e)
        victim = belief.reserved_for
        out: list[ControlInference] = []

        if victim is not None and occupant is not None and victim != occupant:
            # ★ 강탈 — 관제가 세상을 이해하는 방식의 전부가 이 한 줄에 걸려 있다
            out.append(SlotStolen(t=e.t, slot_id=e.slot_id, taker=occupant, victim=victim))
            self.log.of(occupant).steals += 1
            self.log.of(victim).victim_count += 1
            hurt = self.vehicles.get(victim)
            if hurt is not None:
                hurt.target_slot = None
                hurt.route = None
                hurt.route_index = 0
                hurt.deviated = False
        elif victim is not None and occupant is None:
            # 누가 들어왔는지 못 읽었지만 예약자가 아직 도착 전이라면 강탈일 수 있다.
            # 확정할 수 없으므로 판정하지 않고 예약만 해제한다 — presence 모드의 한계.
            pass

        belief.status = SlotStatus.OCCUPIED
        belief.occupant = occupant
        belief.reserved_for = None
        belief.since = e.t

        if occupant is not None:
            v = self.vehicles.get(occupant)
            if v is not None:
                self._release_other_reservations(occupant, keep=e.slot_id)
                v.parked_slot = e.slot_id
                v.target_slot = None
                v.route = None
                v.route_index = 0
        return out

    def _on_lane(self, e: LaneDetection) -> list[ControlInference]:
        v = self.vehicles.get(e.plate)
        if v is None:
            return []
        if e.node_id != v.last_node:
            v.prev_node = v.last_node
        v.last_node = e.node_id
        v.last_seen_t = e.t

        route = v.route
        if route is None or v.deviated:
            return []

        remaining = route.nodes[v.route_index :]
        if e.node_id in remaining:
            v.route_index += remaining.index(e.node_id)
            return []

        if v.route_index >= len(route.nodes) - 1:
            # 목적지 노드를 이미 지났다 — 주차 조작 중이라 검지기가 더 울린다.
            return []

        v.deviated = True
        self.log.of(e.plate).deviations += 1
        return [RouteDeviation(t=e.t, plate=e.plate, at_node=e.node_id)]

    def _on_exited(self, e: VehicleExited) -> None:
        v = self.vehicles.get(e.plate)
        if v is None:
            return
        v.exited = True
        v.last_seen_t = e.t
        v.target_slot = None
        v.route = None
        self._release_other_reservations(e.plate, keep=None)

    # ── 예약 ──────────────────────────────────────────────────────

    def reserve(self, plate: PlateId, slot_id: SlotId, route: Route) -> None:
        """차량에게 주차면을 배정한다. 물리적으로는 여전히 비어 있는 자리다 —
        바로 이 틈에서 강탈이 일어난다."""
        self._release_other_reservations(plate, keep=slot_id)
        belief = self.slots[slot_id]
        belief.status = SlotStatus.RESERVED
        belief.reserved_for = plate
        belief.since = self.t

        v = self.vehicles[plate]
        v.target_slot = slot_id
        v.route = route
        v.route_index = 0
        v.deviated = False
        v.revision += 1

    def release(self, slot_id: SlotId) -> None:
        belief = self.slots.get(slot_id)
        if belief is not None and belief.status is SlotStatus.RESERVED:
            self._vacate(belief, self.t)

    def _vacate(self, belief: SlotBelief, t: float) -> None:
        if belief.occupant is not None:
            v = self.vehicles.get(belief.occupant)
            if v is not None and v.parked_slot == belief.slot_id:
                v.parked_slot = None
        belief.status = SlotStatus.FREE
        belief.occupant = None
        belief.reserved_for = None
        belief.since = t

    def _release_other_reservations(self, plate: PlateId, keep: SlotId | None) -> None:
        for belief in self.slots.values():
            if belief.reserved_for == plate and belief.slot_id != keep:
                belief.status = SlotStatus.FREE
                belief.reserved_for = None
                belief.since = self.t

    # ── 조회 ──────────────────────────────────────────────────────

    def available_slots(self) -> list[SlotId]:
        return [sid for sid, b in self.slots.items() if b.is_available]

    def awaiting_assignment(self) -> list[VehicleBelief]:
        return [v for v in self.vehicles.values() if v.needs_assignment]

    def guidance_load(self) -> dict[EdgeKey, int]:
        """지금 안내 중인 유도선들의 엣지 점유 수. 혼잡 비용의 재료다."""
        load: dict[EdgeKey, int] = {}
        for v in self.vehicles.values():
            if not v.is_en_route or v.route is None:
                continue
            for edge in v.route.edges[v.route_index :]:
                load[edge] = load.get(edge, 0) + 1
        return load

    def occupancy(self) -> float:
        if not self.slots:
            return 0.0
        taken = sum(1 for b in self.slots.values() if b.status is SlotStatus.OCCUPIED)
        return taken / len(self.slots)

    # ── presence 모드 추론 ────────────────────────────────────────

    def _infer_occupant(self, e: SlotOccupancyChanged) -> PlateId | None:
        """번호판을 못 읽는 센서일 때, 통로 검지기 기록으로 점유자를 추정한다.

        해당 주차면 앞 노드를 가장 최근에 지나갔고 아직 주차하지 않은 차량.
        틀릴 수 있다 — 그 불확실성 자체가 `presence` 모드 실험의 내용이다.
        """
        access = self.lot.slots[e.slot_id].access_node
        best: VehicleBelief | None = None
        for v in self.vehicles.values():
            if v.exited or v.parked_slot is not None or v.last_node != access:
                continue
            if best is None or v.last_seen_t > best.last_seen_t:
                best = v
        return None if best is None else best.plate
