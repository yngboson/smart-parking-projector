"""관제 본체 — 센서 이벤트를 받아 유도선을 발급한다.

한 틱의 흐름은 항상 같다:

    센서 이벤트  →  세계 모델 갱신  →  (강탈·이탈 추론)
                 →  자리를 기다리는 차량 모으기
                 →  할당 전략 호출  →  예약 확정  →  유도선 명령

**전략은 여기 없다.** 어느 자리를 줄지는 `allocators/`, 빼앗겼을 때 어떻게 복구할지는
`recovery/` 가 정한다. 이 파일은 그 결정을 세계 모델과 프로젝터에 반영하는 배관이다.
그래야 전략을 갈아끼워도 배관이 그대로다.

이 파일은 ``sim.common`` 과 ``sim.control`` 안쪽만 import 한다 (CLAUDE.md).
"""

from __future__ import annotations

from typing import Sequence

from sim.common.geometry import Vec2, offset_polyline
from sim.common.ids import NodeId, PlateId, SlotId
from sim.common.lotmap import LotMap
from sim.common.messages import (
    ClearGuidance,
    ControlInference,
    GuidanceCommand,
    GuidanceReason,
    ProjectorCommand,
    RouteDeviation,
    SensorEvent,
    SlotOccupancyChanged,
    SlotStolen,
    VehicleExited,
)
from sim.control.allocators import api as alloc_api
from sim.control.cost import CostWeights
from sim.control.recovery import api as recovery_api
from sim.control.routing import LaneRouter, Route, TurnCost
from sim.control.state import ControlState, VehicleBelief

RETRY_INTERVAL = 2.0
"""**만차라서** 자리를 못 받은 차량을 다시 시도하기까지의 간격(초).

만차일 때 매 틱 120면을 재평가하는 것은 낭비다. 실제 관제도 이벤트가 있을 때
다시 계산하지 매 프레임 전수조사하지 않는다.
"""

RETRY_SOON = 0.4
"""**전략이 미룬** 차량을 다시 시도하기까지의 간격(초).

만차와 구별해야 한다. 자리가 있는데 전략이 판단을 미룬 것이라면 곧 다시 물어야
하고, 자리가 없어서 못 준 것이라면 서두를 이유가 없다. 둘을 같은 간격으로 묶으면
전략이 의도한 타이밍이 만차 대기에 묻힌다.
"""


class ProjectorControl:
    """기본 관제 시스템 — `ControlSystem` 프로토콜 구현."""

    def __init__(
        self,
        lot: LotMap,
        allocator: str | alloc_api.Allocator = "greedy_nearest",
        recovery: str | recovery_api.RecoveryStrategy = "global_rematch",
        *,
        weights: CostWeights | None = None,
        turn_cost: TurnCost | None = None,
        slot_sensor_mode: str = "anpr",
    ) -> None:
        self.lot = lot
        self.state = ControlState(lot, slot_sensor_mode=slot_sensor_mode)
        self.router = LaneRouter(lot, turn_cost)
        self.weights = weights or CostWeights()
        self.allocator = (
            alloc_api.get(allocator) if isinstance(allocator, str) else allocator
        )
        self.recovery = (
            recovery_api.get(recovery) if isinstance(recovery, str) else recovery
        )
        """자리를 빼앗겼을 때 어떻게 구제할 것인가. **기본값을 바꾸지 마라** (D-009).

        6종 비교가 이 연구의 결론이므로, 기본 전략은 비교의 기준점이다.
        """

        self._withheld = self.recovery.withhold(lot)
        """복구 전략이 평시 배정에서 빼둔 자리 (`reserve_pool`)."""

        self._bias_ctx = recovery_api.BiasContext(
            lot=lot,
            trust=self.state.log.trust,
            reroute_count=self._reroute_count,
        )

        self.inferences: list[ControlInference] = []
        """직전 틱에 추론한 사건들. 지표 수집과 뷰어 이벤트 로그가 읽는다."""

        self._pending_reason: dict[PlateId, GuidanceReason] = {}
        self._retry_at: dict[PlateId, float] = {}

        self._provisional: dict[PlateId, SlotId] = {}
        """아직 확정되지 않은 배정 (`zone_late_binding`). 차량이 목표 구역의
        통로에 들어서면 다시 물어본다."""

    # ── ControlSystem ─────────────────────────────────────────────

    def tick(self, t: float) -> None:
        """관제의 시계를 맞춘다 (D-025). 센서 이벤트가 없는 틱에도 시간은 간다."""
        self.state.t = max(self.state.t, t)

    def on_events(self, events: Sequence[SensorEvent]) -> list[ProjectorCommand]:
        inferences = self.state.apply(list(events))
        self.inferences = inferences

        commands: list[ProjectorCommand] = list(self._clears(events))
        self._note_reasons(inferences)
        commands.extend(self._recover(inferences))
        commands.extend(self._assign())
        return commands

    # ── 복구 ──────────────────────────────────────────────────────

    def _recover(self, inferences: Sequence[ControlInference]) -> list[ProjectorCommand]:
        """강탈이 일어났다. 복구 전략에게 넘긴다.

        전략이 손대지 못한 피해 차량은 그대로 두면 된다 — 다음 줄의 평시 배정이
        받아준다. 복구가 실패해도 차가 갈 곳을 잃지는 않는다.
        """
        thefts = [i for i in inferences if isinstance(i, SlotStolen)]
        if not thefts:
            return []

        victims = tuple(dict.fromkeys(i.victim for i in thefts))
        ctx = self._recovery_context(set(victims))
        request = recovery_api.RecoveryRequest(
            t=self.state.t,
            victims=victims,
            stolen_slots=tuple(i.slot_id for i in thefts),
        )

        out: list[ProjectorCommand] = []
        taken: set[SlotId] = set()
        for move in self.recovery.recover(request, ctx):
            if move.slot_id in taken or move.plate not in self.state.vehicles:
                continue        # 전략이 같은 자리를 두 번 준 경우 — 조용히 무시한다
            taken.add(move.slot_id)
            out.append(self._commit_recovery(move))
        return out

    def _recovery_context(self, victims: set[PlateId]) -> recovery_api.RecoveryContext:
        entry = self.lot.entry_nodes[0]
        candidates = [
            recovery_api.Candidate(
                plate=v.plate,
                vehicle_class=v.vehicle_class,
                from_node=v.last_node or entry,
                arrived_from=v.prev_node,
                held_slot=v.target_slot,
                reroute_count=v.reroute_count,
                is_victim=v.plate in victims,
            )
            for v in self.state.vehicles.values()
            if not v.exited and v.parked_slot is None
        ]
        return recovery_api.RecoveryContext(
            lot=self.lot,
            router=self.router,
            candidates=candidates,
            # 예비석도 여기서는 쓸 수 있다 — 그러라고 남겨둔 자리다.
            free_slots=self.state.available_slots(),
            load=self.state.guidance_load(),
            weights=self.weights,
            trust=self.state.log.trust,
        )

    def _commit_recovery(self, move: recovery_api.Reassignment) -> GuidanceCommand:
        v = self.state.vehicles[move.plate]
        if move.reason is GuidanceReason.REROUTE:
            v.reroute_count += 1
        elif move.reason is GuidanceReason.RESHUFFLE:
            v.reshuffle_count += 1

        self.state.reserve(move.plate, move.slot_id, move.route)
        self._pending_reason.pop(move.plate, None)
        self._retry_at.pop(move.plate, None)

        return GuidanceCommand(
            plate=move.plate,
            target_slot=move.slot_id,
            polyline=guidance_polyline(self.lot, move.route, move.slot_id),
            reason=move.reason,
            revision=v.revision,
        )

    def _reroute_count(self, plate: PlateId) -> int:
        v = self.state.vehicles.get(plate)
        return 0 if v is None else v.reroute_count

    # ── 유도선 소거 ────────────────────────────────────────────────

    def _clears(self, events: Sequence[SensorEvent]) -> list[ProjectorCommand]:
        """더 이상 그릴 이유가 없어진 유도선을 지운다.

        주차를 마쳤거나 주차장을 떠났을 때다. 바닥에 남은 선의 총량을 줄이는 것이
        이 시스템의 가독성을 지키는 핵심이다 (docs/DECISIONS.md D-006).
        """
        out: list[ProjectorCommand] = []
        for e in events:
            if isinstance(e, SlotOccupancyChanged) and e.occupied and e.plate is not None:
                out.append(ClearGuidance(plate=e.plate))
                self._forget(e.plate)
            elif isinstance(e, VehicleExited):
                out.append(ClearGuidance(plate=e.plate))
                self._forget(e.plate)
        return out

    def _note_reasons(self, inferences: Sequence[ControlInference]) -> None:
        """다음 안내가 왜 나가는지를 기억해 둔다.

        `reroute_count` 는 **자기 자리를 빼앗긴 경우에만** 올라간다. 남의 사고를
        흡수하느라 목적지가 바뀐 것은 RESHUFFLE 로 따로 센다 — 두 숫자를 섞으면
        복구 전략 비교가 무의미해진다.
        """
        for inf in inferences:
            if isinstance(inf, SlotStolen):
                self._pending_reason[inf.victim] = GuidanceReason.REROUTE
                self._retry_at.pop(inf.victim, None)
            elif isinstance(inf, RouteDeviation):
                # 조기 경보일 뿐 아직 아무 일도 일어나지 않았다. 선제 대응은
                # 복구 전략(6단계)이 결정할 몫이라 여기서는 기록만 한다.
                pass

    # ── 배정 ──────────────────────────────────────────────────────

    def _assign(self) -> list[ProjectorCommand]:
        waiting = [v for v in self.state.awaiting_assignment() if self._is_due(v)]
        waiting += [v for v in self._arrived_in_zone() if self._is_due(v)]
        if not waiting:
            return []

        # 예비석은 평시 배정에서 뺀다. 사고가 났을 때 즉시 투입하려고 남긴 자리다.
        available = [s for s in self.state.available_slots() if s not in self._withheld]
        if not available:
            self._defer(waiting, RETRY_INTERVAL)     # 만차 — 급할 것 없다
            return []

        requests = [self._request(v) for v in waiting]
        ctx = alloc_api.AllocationContext(
            lot=self.lot,
            router=self.router,
            available=available,
            load=self.state.guidance_load(),
            weights=self.weights,
            trust=self.state.log.trust,
            bias=lambda plate, sid: self.recovery.bias(plate, sid, self._bias_ctx),
        )

        assignments = self.allocator.allocate(requests, ctx)
        placed = {a.plate for a in assignments}
        # 자리는 있는데 전략이 안 준 것이다 — 곧 다시 묻는다.
        self._defer([v for v in waiting if v.plate not in placed], RETRY_SOON)

        return [self._commit(a) for a in assignments]

    def _arrived_in_zone(self) -> list[VehicleBelief]:
        """잠정 배정을 받은 차량 중 목표 구역의 통로에 들어선 것들.

        통로 검지기가 "이 번호판이 이 노드를 지났다"를 알려주므로, 그 노드가 목표
        주차면의 통로와 같은지만 보면 된다 — 실제 하드웨어로도 할 수 있는 판단이다.

        **한 번 올라오면 잠정 표시를 뗀다.** 자리를 새로 못 받더라도(구역이 다 찼다)
        매 틱 다시 묻지 않기 위해서다. 늦은 확정의 기회는 한 번이다.
        """
        out: list[VehicleBelief] = []
        for plate, slot_id in list(self._provisional.items()):
            v = self.state.vehicles.get(plate)
            if v is None or v.exited or v.parked_slot is not None:
                del self._provisional[plate]
                continue
            if v.target_slot != slot_id:
                del self._provisional[plate]     # 그새 재할당됐다
                continue
            if v.last_node is None:
                continue
            if self._same_aisle(v.last_node, self.lot.slots[slot_id].access_node):
                del self._provisional[plate]
                out.append(v)
        return out

    def _same_aisle(self, a: NodeId, b: NodeId) -> bool:
        nodes = self.lot.nodes
        if a not in nodes or b not in nodes:
            return False
        return nodes[a].aisle == nodes[b].aisle

    def _request(self, v: VehicleBelief) -> alloc_api.AllocationRequest:
        reason = self._pending_reason.get(v.plate, GuidanceReason.INITIAL)
        node = v.last_node or self.lot.entry_nodes[0]
        return alloc_api.AllocationRequest(
            plate=v.plate,
            vehicle_class=v.vehicle_class,
            from_node=node,
            arrived_from=v.prev_node,
            t=self.state.t,
            reason=reason,
            held_slot=v.target_slot,
        )

    def _commit(self, a: alloc_api.Assignment) -> GuidanceCommand:
        v = self.state.vehicles[a.plate]
        reason = self._pending_reason.pop(a.plate, GuidanceReason.INITIAL)
        if reason is GuidanceReason.REROUTE:
            v.reroute_count += 1
        elif reason is GuidanceReason.RESHUFFLE:
            v.reshuffle_count += 1

        self.state.reserve(a.plate, a.slot_id, a.route)
        self._retry_at.pop(a.plate, None)
        if a.provisional:
            self._provisional[a.plate] = a.slot_id
        else:
            self._provisional.pop(a.plate, None)

        return GuidanceCommand(
            plate=a.plate,
            target_slot=a.slot_id,
            polyline=guidance_polyline(self.lot, a.route, a.slot_id),
            reason=reason,
            revision=v.revision,
        )

    def _forget(self, plate: PlateId) -> None:
        self._retry_at.pop(plate, None)
        self._pending_reason.pop(plate, None)
        self._provisional.pop(plate, None)

    def _is_due(self, v: VehicleBelief) -> bool:
        return self.state.t >= self._retry_at.get(v.plate, 0.0)

    def _defer(self, vehicles: Sequence[VehicleBelief], delay: float) -> None:
        for v in vehicles:
            self._retry_at[v.plate] = self.state.t + delay

    # ── 조회 ──────────────────────────────────────────────────────

    def target_of(self, plate: PlateId) -> SlotId | None:
        v = self.state.vehicles.get(plate)
        return None if v is None else v.target_slot


def guidance_polyline(lot: LotMap, route: Route, slot_id: SlotId) -> tuple[Vec2, ...]:
    """통로 경로에 주차면 진입 구간을 붙여 바닥에 그릴 선을 만든다.

    **선은 실제로 달릴 차선 위에 그린다.** 경로 탐색은 통로 중심선의 노드를 잇지만,
    차는 우측통행으로 그 옆을 달린다 (D-012). 중심선에 그리면 바닥의 선과 차가
    가는 길이 통로 폭의 4분의 1만큼 어긋나고, 넓은 통로에서는 그 차이가 3m 를
    넘는다 — 안내를 따르는 차가 선 밖으로 달리는 것처럼 보인다.

    이 시스템의 약속은 "선을 따라가면 자리에 닿는다"이다. 그 선이 실제 차선이
    아니면 약속이 깨진다.

    선이 통로에서 뚝 끊기면 어느 자리인지 알 수 없으므로 주차면 안까지 이어 붙인다.
    실제로 어떻게 꺾어 들어갈지는 운전자가 정하는 일이다 (agents 계층).
    """
    slot = lot.slots[slot_id]
    lane = offset_polyline(route.polyline, lot.travel_lane_offset)
    return tuple(lane) + (slot.entry_point, slot.center)
