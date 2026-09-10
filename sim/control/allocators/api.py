"""할당 전략 플러그인 계약.

전략을 추가할 때 **기존 코드를 고치지 않는다.** 이 디렉터리에 모듈을 하나 더 넣고
`Allocator` 를 구현한 뒤 `register()` 하면 시나리오 YAML 에서 이름으로 고를 수 있다
(CLAUDE.md '전략을 추가하는 방법').

**왜 한 대씩이 아니라 목록으로 받는가.** `greedy_nearest` 는 한 대씩 처리해도
되지만 `hungarian_batch` 는 여러 대를 **함께** 놓고 최적 매칭해야 한다. 인터페이스를
한 대짜리로 만들면 그 전략을 나중에 끼워 넣을 수 없다. 그래서 처음부터 배치로 둔다.

전략은 상태를 갖지 않는 편이 좋다. 같은 시드로 재현되어야 실험이 성립한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence, runtime_checkable

from sim.common.ids import NodeId, PlateId, SlotId, VehicleClass
from sim.common.lotmap import LotMap
from sim.common.messages import GuidanceReason
from sim.control.cost import CostWeights, slot_cost, slot_is_eligible
from sim.control.routing import EdgeKey, LaneRouter, Route


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    """자리를 달라는 요청 하나.

    관제가 아는 것만 담는다 — 번호판, 차종 등급, 마지막으로 목격된 통로 노드.
    차량이 지금 어디를 보고 있는지, 무슨 생각인지는 들어오지 않는다.
    """

    plate: PlateId
    vehicle_class: VehicleClass
    from_node: NodeId
    """마지막으로 관측된 위치. 재할당이라면 입구가 아니라 통로 한복판이다."""

    arrived_from: NodeId | None = None
    """그 노드에 어느 방향으로 들어왔는가. 첫 회전 비용 계산용."""

    t: float = 0.0
    reason: GuidanceReason = GuidanceReason.INITIAL

    held_slot: SlotId | None = None
    """지금 잠정 배정받아 들고 있는 자리. 처음 받는 차량이면 None.

    `zone_late_binding` 이 "구역만 정한 상태"와 "자리를 확정할 때"를 구분하는 데 쓴다.
    """


@dataclass(frozen=True, slots=True)
class Assignment:
    """배정 결과 하나."""

    plate: PlateId
    slot_id: SlotId
    route: Route
    cost: float

    provisional: bool = False
    """아직 확정이 아니다 — 차량이 그 구역에 들어오면 다시 물어봐 달라.

    `zone_late_binding` 을 위한 것이다. 입구에서 최종 자리를 확정하지 않고 구역만
    정해 두면, 예약과 도착 사이의 틈이 짧아져 강탈에 원천적으로 강해진다
    (docs/PLAN.md 4절). 이 표시가 없으면 그 전략을 프로토콜 안에서 표현할 수 없다.

    관제 본체는 이 표시가 붙은 차량이 목표 구역의 통로에 들어선 것을 보면
    (통로 검지기로 알 수 있다) 그 차량을 다시 배정 대상에 올린다.
    """


class AllocationContext:
    """전략이 쓸 수 있는 도구 모음.

    전략이 `ControlState` 를 직접 만지지 못하게 한 겹 감쌌다. 전략은 "무엇이
    비어 있고 어디로 가면 얼마인가"만 알면 되고, 예약을 확정하는 것은 관제 본체의
    몫이다. 이렇게 두면 전략이 세계 모델을 조용히 오염시키는 사고가 안 난다.
    """

    def __init__(
        self,
        lot: LotMap,
        router: LaneRouter,
        available: Sequence[SlotId],
        load: Mapping[EdgeKey, int],
        weights: CostWeights | None = None,
        trust: Callable[[PlateId], float] | None = None,
        bias: Callable[[PlateId, SlotId], float] | None = None,
    ) -> None:
        self.lot = lot
        self.router = router
        self.available = list(available)
        self.load = load
        self.weights = weights or CostWeights()
        self._trust = trust or (lambda _p: 1.0)
        self._bias = bias or (lambda _p, _s: 0.0)
        """복구 전략이 평시 배정에 얹는 보정(m 환산).

        `reserve_pool` 과 `reputation_aware` 는 사고가 나기 **전부터** 다르게
        행동해야 하는 전략이다. 사후 대응만 갈아끼우면 그 둘을 표현할 수 없다.
        """

    def trust(self, plate: PlateId) -> float:
        """번호판별 신뢰도(0~1). 관측된 이탈·강탈 이력의 집계다 (D-003)."""
        return self._trust(plate)

    def candidates(self, req: AllocationRequest) -> list[SlotId]:
        """이 차량에게 배정 가능한 주차면들."""
        return [
            sid
            for sid in self.available
            if slot_is_eligible(self.lot.slots[sid], req.vehicle_class)
        ]

    def route_to(self, req: AllocationRequest, slot_id: SlotId) -> Route | None:
        """요청 지점에서 주차면 앞 통로 노드까지의 경로."""
        return self.router.route(
            req.from_node,
            self.lot.slots[slot_id].access_node,
            arrive_from=req.arrived_from,
        )

    def evaluate(self, req: AllocationRequest, slot_id: SlotId) -> tuple[Route, float] | None:
        """(경로, 비용). 갈 수 없으면 None."""
        route = self.route_to(req, slot_id)
        if route is None:
            return None
        cost = slot_cost(
            self.lot, slot_id, req.vehicle_class, route, self.load, self.weights
        )
        return route, cost + self._bias(req.plate, slot_id)

    def take(self, slot_id: SlotId) -> None:
        """한 배치 안에서 같은 자리를 두 대에게 주지 않도록 후보에서 뺀다."""
        if slot_id in self.available:
            self.available.remove(slot_id)


@runtime_checkable
class Allocator(Protocol):
    """할당 전략."""

    name: str

    def allocate(
        self, requests: Sequence[AllocationRequest], ctx: AllocationContext
    ) -> list[Assignment]:
        """요청들에 자리를 배정한다. 배정하지 못한 요청은 결과에서 빠진다."""
        ...


# ── 레지스트리 ────────────────────────────────────────────────────

_REGISTRY: dict[str, Allocator] = {}


def register(allocator: Allocator) -> Allocator:
    _REGISTRY[allocator.name] = allocator
    return allocator


def get(name: str) -> Allocator:
    if name not in _REGISTRY:
        _load_builtin()
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(없음)"
        raise KeyError(f"알 수 없는 할당 전략 {name!r}. 등록된 전략: {known}")
    return _REGISTRY[name]


def available_names() -> list[str]:
    _load_builtin()
    return sorted(_REGISTRY)


def _load_builtin() -> None:
    from sim.control.allocators import (  # noqa: F401
        congestion_aware,
        greedy_nearest,
        hungarian_batch,
        spread_only,
        walk_only,
        zone_late_binding,
    )
