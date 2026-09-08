"""복구 전략 플러그인 계약 — **이 연구가 답해야 할 질문이 사는 곳**.

    자리를 빼앗긴 사람을 어떻게 구제하는 것이 가장 매끄러운가?

이건 미리 정답을 가정할 수 있는 문제가 아니다. 그래서 6종을 구현해 같은 시드로
비교한다 (docs/DECISIONS.md D-009). 기본값 `global_rematch` 를 임의로 바꾸지 마라 —
비교 대상이다.

전략마다 성격이 다르다:

    빠르지만 이기적 ←─────────────────────────────→ 느리지만 공평
    local_reassign   chain_shift   global_rematch   fairness_weighted
                     reserve_pool(즉답, 평시 손해)  reputation_aware(학습)

**전략이 건드릴 수 있는 것은 셋뿐이다.**

1. `recover()` — 강탈이 벌어진 뒤 누구에게 어느 자리를 다시 줄 것인가
2. `withhold()` — 평시 배정에서 빼둘 자리 (예비 풀)
3. `bias()` — 평시 배정 비용에 얹을 보정 (신뢰도·공평성)

2·3 이 필요한 이유: `reserve_pool` 과 `reputation_aware` 는 **사고가 나기 전부터**
다르게 행동해야 하는 전략이다. 사후 대응만 갈아끼우면 그 둘을 표현할 수 없고,
비교가 반쪽이 된다. 기본 구현은 아무것도 하지 않으므로 나머지 전략은 신경 쓸 필요가 없다.

이 파일은 ``sim.common`` 과 ``sim.control`` 안쪽만 import 한다 (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, Sequence, runtime_checkable

from sim.common.ids import NodeId, PlateId, SlotId, VehicleClass
from sim.common.lotmap import LotMap
from sim.common.messages import GuidanceReason
from sim.control.cost import CostWeights, slot_cost, slot_is_eligible
from sim.control.routing import EdgeKey, LaneRouter, Route


@dataclass(frozen=True, slots=True)
class Candidate:
    """아직 주차하지 못한 차량 하나 — 복구가 움직일 수 있는 대상.

    관제가 아는 것만 담는다. 이 차가 지금 무슨 생각인지, 실제로 어디를 향해 가는지는
    들어 있지 않다 — 안내를 무시하고 딴 데로 가는 중일 수도 있다.
    """

    plate: PlateId
    vehicle_class: VehicleClass
    from_node: NodeId
    """마지막으로 관측된 통로 노드. 재배정 경로는 여기서 시작한다."""

    arrived_from: NodeId | None = None
    held_slot: SlotId | None = None
    """지금 예약하고 있는 자리. 피해 차량은 이미 잃었으므로 None."""

    reroute_count: int = 0
    """지금까지 자리를 빼앗긴 횟수. `fairness_weighted` 가 읽는다."""

    is_victim: bool = False
    """이번 사건의 피해자인가."""


@dataclass(frozen=True, slots=True)
class RecoveryRequest:
    """복구가 필요해진 순간."""

    t: float
    victims: tuple[PlateId, ...]
    stolen_slots: tuple[SlotId, ...] = ()


@dataclass(frozen=True, slots=True)
class Reassignment:
    """전략이 내린 결정 하나."""

    plate: PlateId
    slot_id: SlotId
    route: Route
    reason: GuidanceReason = GuidanceReason.REROUTE
    """`REROUTE` 는 피해 당사자, `RESHUFFLE` 은 남의 사고를 흡수한 차량.

    **둘을 섞으면 안 된다.** 섞으면 "피해자 수"와 "영향받은 차량 수"가 구분되지
    않고, 그 둘의 차이가 바로 전략들의 성격 차이다.
    """


class RecoveryContext:
    """전략이 쓸 수 있는 도구 모음.

    `ControlState` 를 직접 넘기지 않는 이유는 할당 전략과 같다 — 전략이 세계 모델을
    조용히 오염시키는 사고를 막고, 무엇을 볼 수 있는지 한눈에 보이게 하려는 것이다.
    """

    def __init__(
        self,
        lot: LotMap,
        router: LaneRouter,
        candidates: Sequence[Candidate],
        free_slots: Sequence[SlotId],
        load: Mapping[EdgeKey, int],
        weights: CostWeights | None = None,
        trust: Callable[[PlateId], float] | None = None,
    ) -> None:
        self.lot = lot
        self.router = router
        self.candidates = list(candidates)
        self.free_slots = list(free_slots)
        self.load = load
        self.weights = weights or CostWeights()
        self._trust = trust or (lambda _p: 1.0)

    # ── 조회 ──────────────────────────────────────────────────────

    def trust(self, plate: PlateId) -> float:
        """번호판별 신뢰도 (0~1). **관측된 이탈·강탈 이력의 집계일 뿐이다** (D-003).

        운전자의 성향을 읽는 것이 아니다. 그 구분이 이 연구의 정당성을 지탱한다.
        """
        return self._trust(plate)

    def victims(self) -> list[Candidate]:
        return [c for c in self.candidates if c.is_victim]

    def held_slots(self) -> list[SlotId]:
        """미주차 차량들이 붙잡고 있는 자리들. 연쇄 재배치의 재료다."""
        return [c.held_slot for c in self.candidates if c.held_slot is not None]

    def pool(self) -> list[SlotId]:
        """전체 재매칭이 다룰 수 있는 자리 = 빈 자리 + 아직 아무도 앉지 않은 예약석."""
        return sorted({*self.free_slots, *self.held_slots()})

    def evaluate(self, cand: Candidate, slot_id: SlotId) -> tuple[Route, float] | None:
        """(경로, 비용). 못 가거나 못 쓰는 자리면 None."""
        slot = self.lot.slots[slot_id]
        if not slot_is_eligible(slot, cand.vehicle_class):
            return None
        route = self.router.route(
            cand.from_node, slot.access_node, arrive_from=cand.arrived_from
        )
        if route is None:
            return None
        cost = slot_cost(
            self.lot, slot_id, cand.vehicle_class, route, self.load, self.weights
        )
        return route, cost

    def best(self, cand: Candidate, slots: Sequence[SlotId]) -> Reassignment | None:
        """주어진 후보들 중 이 차량에게 가장 싼 자리."""
        found: Reassignment | None = None
        cheapest = float("inf")
        for sid in slots:
            got = self.evaluate(cand, sid)
            if got is None or got[1] >= cheapest:
                continue
            cheapest = got[1]
            found = Reassignment(
                cand.plate, sid, got[0],
                GuidanceReason.REROUTE if cand.is_victim else GuidanceReason.RESHUFFLE,
            )
        return found


@runtime_checkable
class RecoveryStrategy(Protocol):
    """복구 전략 하나."""

    name: str

    def recover(
        self, request: RecoveryRequest, ctx: RecoveryContext
    ) -> list[Reassignment]:
        """강탈이 벌어졌다. 누구에게 어느 자리를 다시 줄 것인가."""
        ...

    def withhold(self, lot: LotMap) -> set[SlotId]:
        """평시 배정에서 빼둘 자리. 기본은 없음."""
        ...

    def bias(self, plate: PlateId, slot_id: SlotId, ctx: "BiasContext") -> float:
        """평시 배정 비용에 얹을 보정(m 환산). 기본은 0."""
        ...


@dataclass(frozen=True, slots=True)
class BiasContext:
    """평시 배정 보정에 필요한 것들."""

    lot: LotMap
    trust: Callable[[PlateId], float]
    reroute_count: Callable[[PlateId], int]


class BaseRecovery:
    """전략들이 물려받는 기본 동작 — 평시에는 아무것도 하지 않는다.

    6종 중 넷은 사고가 난 뒤에만 움직인다. 그 넷이 `withhold` / `bias` 를
    신경 쓰지 않아도 되게 기본 구현을 여기 둔다.
    """

    name = "base"

    def recover(
        self, request: RecoveryRequest, ctx: RecoveryContext
    ) -> list[Reassignment]:
        raise NotImplementedError

    def withhold(self, lot: LotMap) -> set[SlotId]:
        return set()

    def bias(self, plate: PlateId, slot_id: SlotId, ctx: BiasContext) -> float:
        return 0.0


# ── 레지스트리 ────────────────────────────────────────────────────

_REGISTRY: dict[str, RecoveryStrategy] = {}


def register(strategy: RecoveryStrategy) -> RecoveryStrategy:
    _REGISTRY[strategy.name] = strategy
    return strategy


def get(name: str) -> RecoveryStrategy:
    if name not in _REGISTRY:
        _load_builtin()
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(없음)"
        raise KeyError(f"알 수 없는 복구 전략 {name!r}. 등록된 전략: {known}")
    return _REGISTRY[name]


def available_names() -> list[str]:
    _load_builtin()
    return sorted(_REGISTRY)


def _load_builtin() -> None:
    from sim.control.recovery import (  # noqa: F401
        chain_shift,
        fairness_weighted,
        global_rematch,
        local_reassign,
        reputation_aware,
        reserve_pool,
    )
