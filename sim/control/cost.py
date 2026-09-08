"""주차면 비용 함수 — "이 차에게 이 자리가 얼마나 좋은가".

    비용 = w₁·주행거리 + w₂·회전수 + w₃·통로혼잡도 + w₄·도보거리 + w₅·차종제약

모든 항을 **미터 환산**으로 맞춰 둔다. 그래야 가중치를 조정할 때 "회전 한 번을
우회 9m 로 친다" 처럼 말로 설명할 수 있고, 발표에서 방어할 수 있는 숫자가 된다.

**도보거리가 왜 중요한가.** 이 항이 없으면 알고리즘은 입구에서 제일 가까운 자리를
채우고, 그 자리들은 대개 건물에서 멀다. 그러면 운전자가 안내를 무시할 이유가
생긴다 — 이 연구가 다루는 '강탈'의 동기가 바로 여기서 나온다. 반대로 이 항을
너무 키우면 모두를 A 구역으로 보내 통로가 막힌다. 균형점을 찾는 것이 실험의 일부다.

혼잡도는 **관제가 지금 안내 중인 유도선들의 엣지 점유 수**다. 실제 차량 위치가
아니라 자기가 그린 선을 세는 것이므로 계층 위반이 아니다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from sim.common.ids import SlotId, SlotType, VehicleClass
from sim.common.lotmap import LotMap, Slot
from sim.control.routing import EdgeKey, Route


@dataclass(frozen=True, slots=True)
class CostWeights:
    """비용 함수의 가중치. 시나리오 YAML 로 덮어쓸 수 있게 값 객체로 둔다."""

    drive: float = 1.0
    """주행 거리 1m 당 비용. 기준 단위."""

    turn: float = 6.0
    """회전 한 번의 추가 비용(m 환산). routing.TurnCost 와는 별개다 —
    저쪽은 '경로를 어떻게 그릴까', 이쪽은 '어느 자리를 줄까'의 문제다."""

    congestion: float = 3.0
    """내 경로가 다른 유도선과 겹치는 엣지 하나당 비용(m 환산)."""

    walk: float = 1.8
    """주차면에서 건물까지 도보 1m 당 비용. 걷는 1m 는 타고 가는 1m 보다 싫다."""

    tight_fit: float = 30.0
    """주차면 폭 여유가 부족할 때의 벌점."""

    min_side_clearance: float = 0.30
    """양옆으로 이만큼은 남아야 편하게 주차할 수 있다고 본다(m)."""


def slot_is_eligible(slot: Slot, vehicle_class: VehicleClass) -> bool:
    """이 차량에게 배정해도 되는 주차면인가 — 비용 이전의 가부 판단.

    장애인·EV 주차면은 일반 차량에게 배정하지 않는다. 실제 주차장이 그렇고,
    그 결과 '좋은 자리'가 줄어들어 강탈 압력이 현실적으로 높아진다.
    """
    if slot.slot_type in (SlotType.DISABLED, SlotType.EV):
        return False
    if slot.slot_type is SlotType.COMPACT and vehicle_class is not VehicleClass.COMPACT:
        return False

    length, width = vehicle_class.footprint
    return width <= slot.width + 0.15 and length <= slot.length + 0.40


def fit_penalty(slot: Slot, vehicle_class: VehicleClass, w: CostWeights) -> float:
    """차종 제약 항(w₅). 들어가긴 하지만 빠듯한 자리에 매기는 벌점."""
    _, width = vehicle_class.footprint
    slack = (slot.width - width) / 2.0
    if slack >= w.min_side_clearance:
        return 0.0
    return w.tight_fit * (1.0 - slack / w.min_side_clearance)


def congestion_of(route: Route, load: Mapping[EdgeKey, int]) -> int:
    """경로가 다른 유도선과 겹치는 정도. 겹친 엣지들의 점유 대수 **합**.

    합이면 경로가 길수록 혼잡도가 커지므로 주행거리(w₁)를 한 번 더 세는 셈이라는
    비판이 가능하다. 실제로 "가장 붐비는 엣지 하나"(병목)로 바꿔서 재봤다.
    결과는 더 나빴다 — 배정은 구역별로 잘 흩어졌지만(A~F 고르게) 통로를 가로지르는
    장거리 이동이 늘어 정지 비율이 5% 에서 82% 로 뛰었다.

    일방통행 격자에서는 **멀리 흩어지는 것 자체가 비싸다.** 합을 쓰면 그 사실이
    비용에 자연히 들어온다. 흩어짐과 흐름의 균형을 제대로 다루는 것은
    `congestion_aware` / `hungarian_batch` 전략의 몫이다 (docs/PLAN.md 4).
    """
    return sum(load.get(e, 0) for e in route.edges)


def slot_cost(
    lot: LotMap,
    slot_id: SlotId,
    vehicle_class: VehicleClass,
    route: Route,
    load: Mapping[EdgeKey, int],
    weights: CostWeights | None = None,
) -> float:
    """주차면 하나의 총 비용. 낮을수록 좋다."""
    w = weights or CostWeights()
    slot = lot.slots[slot_id]
    return (
        w.drive * route.length
        + w.turn * route.turns
        + w.congestion * congestion_of(route, load)
        + w.walk * lot.walk_distance(slot_id)
        + fit_penalty(slot, vehicle_class, w)
    )


def walk_rank(lot: LotMap) -> dict[SlotId, float]:
    """도보거리 기준 0~1 순위. 0 이 가장 좋은 자리.

    '인기 자리'가 어디인지를 관제도, 실험 분석도 같은 기준으로 말할 수 있게 한다.
    """
    ordered = sorted(lot.slots, key=lot.walk_distance)
    n = max(1, len(ordered) - 1)
    return {sid: i / n for i, sid in enumerate(ordered)}


def infeasible() -> float:
    return math.inf
