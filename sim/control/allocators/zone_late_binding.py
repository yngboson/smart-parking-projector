"""`zone_late_binding` — 입구에서는 **구역**만 정하고, 자리는 구역에 들어와서 확정한다.

강탈이 가능한 이유는 **예약과 도착 사이에 시간이 있기 때문**이다. 입구에서 자리를
확정하면 그 차가 통로를 가로질러 오는 30초 동안 그 자리는 비어 있는 채로 남의 눈에
띈다. 그 틈이 이 연구가 다루는 사고의 무대다.

그래서 이 전략은 틈을 좁힌다.

1. 입구에서는 **구역**(주차면 행 A~F)만 고른다. 자리는 잠정으로 하나 잡아 두되
   `provisional` 로 표시한다 — 유도선은 그려야 하므로 끝점은 필요하다.
2. 차량이 그 구역의 통로에 들어서면(통로 검지기가 알려준다) 관제가 다시 물어보고,
   그때 **지금 비어 있는 자리 중 최선**으로 확정한다.

확정 시점에 차는 이미 그 자리 앞에 있다. 남이 가로챌 시간이 거의 없다.

**구역을 고르는 기준이 다르다.** `greedy_nearest` 는 가장 싼 자리 **하나**를 본다.
이쪽은 구역의 **공급**을 본다 — 도착했을 때 고를 자리가 남아 있어야 늦은 확정이
의미가 있기 때문이다. 빈 자리가 하나뿐인 구역은 사실상 조기 확정과 같다.

**대가**: 최선의 자리 하나를 좇지 않으므로 평균 주행·도보 거리가 늘어난다. 강탈이
드문 조건(`light`)에서는 순수한 손해다. 그 손익이 뒤집히는 지점이 이 전략의
실험 결과다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from sim.common.ids import SlotId
from sim.control.allocators.api import (
    AllocationContext,
    AllocationRequest,
    Assignment,
    register,
)

SAMPLE = 3
"""구역 비용을 매길 때 보는 최저가 자리 수. 하나만 보면 `greedy_nearest` 와 같아진다."""

ROOM_BONUS = 4.0
"""구역에 빈 자리가 하나 더 있을 때 깎아 주는 비용(m 환산)."""

ROOM_CAP = 6
"""여유를 쳐주는 상한. 이보다 많이 비어 있어도 더 깎지 않는다."""


class ZoneLateBinding:
    name = "zone_late_binding"

    def allocate(
        self, requests: Sequence[AllocationRequest], ctx: AllocationContext
    ) -> list[Assignment]:
        out: list[Assignment] = []

        for req in sorted(requests, key=lambda r: (r.t, r.plate)):
            priced = _price(ctx, req)
            if not priced:
                continue

            if req.held_slot is None:
                got = _pick_zone(ctx, priced)
                provisional = True
            else:
                got = _pick_within(ctx, priced, req.held_slot)
                provisional = False

            if got is None:
                continue

            slot_id, route, cost = got
            out.append(Assignment(req.plate, slot_id, route, cost, provisional))
            ctx.take(slot_id)
            _add_load(ctx, route)

        return out


def _price(ctx: AllocationContext, req: AllocationRequest):
    """이 차량이 갈 수 있는 자리들의 (자리, 경로, 비용)."""
    priced = []
    for slot_id in ctx.candidates(req):
        got = ctx.evaluate(req, slot_id)
        if got is not None:
            priced.append((slot_id, got[0], got[1]))
    return priced


def _pick_zone(ctx: AllocationContext, priced):
    """구역을 고르고, 그 안에서 잠정으로 한 자리를 잡는다."""
    by_zone: dict[str, list] = defaultdict(list)
    for row in priced:
        by_zone[ctx.lot.slots[row[0]].row].append(row)

    best_zone, best_score = None, None
    for zone, rows in sorted(by_zone.items()):
        rows.sort(key=lambda r: r[2])
        cheapest = [r[2] for r in rows[:SAMPLE]]
        score = sum(cheapest) / len(cheapest) - ROOM_BONUS * min(len(rows), ROOM_CAP)
        if best_score is None or score < best_score:
            best_zone, best_score = zone, score

    return by_zone[best_zone][0] if best_zone is not None else None


def _pick_within(ctx: AllocationContext, priced, held: SlotId):
    """구역에 들어왔다 — 그 구역 안에서 지금 최선인 자리로 확정한다.

    구역이 그새 다 차 버렸으면 어쩔 수 없이 밖에서 고른다. 늦은 확정의 위험이
    바로 이것이고, 그래서 구역을 고를 때 여유분을 쳐준다.
    """
    zone = ctx.lot.slots[held].row
    inside = [r for r in priced if ctx.lot.slots[r[0]].row == zone]
    pool = inside or priced
    return min(pool, key=lambda r: r[2]) if pool else None


def _add_load(ctx: AllocationContext, route) -> None:
    load = dict(ctx.load)
    for edge in route.edges:
        load[edge] = load.get(edge, 0) + 1
    ctx.load = load


register(ZoneLateBinding())
