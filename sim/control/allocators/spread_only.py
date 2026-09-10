"""`spread_only` — 주차장 전체에 **고르게 흩뜨린다.**

`walk_only` 의 정반대입니다. 도보 거리도 주행 거리도 보지 않고, **이미 차가 몰려 있는
곳을 피하는 것**만 봅니다.

    비용 = 그 구역에 이미 잡혀 있는 자리 수  (+ 같은 값이면 통로 부하)

**일부러 극단으로 만들었습니다.** 분산의 **상한**을 보기 위해서입니다
(docs/ALLOCATION_MODEL.md 5절). 실제로 쓸 전략이 아니라 좌표축의 반대쪽 끝입니다.

**어떻게 '몰림'을 재는가.** 주차면 행(A~F)을 구역으로 보고, 각 구역에서 이미 쓰이고
있는 자리 수를 셉니다. 관제가 아는 것만으로 셀 수 있습니다 — 자기가 그린 예약과
센서가 알려준 점유뿐입니다. 차량 위치를 들여다보지 않습니다 (CLAUDE.md 3번 규칙).

**이 전략이 만드는 혼잡**: 통로 밀도는 낮아지지만 **도보 거리가 폭발합니다.** 그리고
도보 거리가 늘면 운전자가 이탈할 이유가 생깁니다 — 눈앞의 빈 자리가 배정받은 자리보다
훨씬 가까워 보이기 때문입니다. 분산이 강탈을 **늘리는지**가 이 전략이 답하는
질문이고, 집중–분산 트레이드오프(ALLOCATION_MODEL 4.1절)의 반대쪽 증거입니다.
"""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from sim.control.allocators.api import (
    AllocationContext,
    AllocationRequest,
    Assignment,
    register,
)

LANE_TIE_BREAK = 0.01
"""구역 점유가 같을 때 통로 부하로 가르는 가중치.

아주 작게 둔다 — 이 전략의 논지는 '구역을 고르게' 하나이고, 통로 부하는 동점 처리용
이상이 되면 안 된다. 커지면 `congestion_aware` 와 섞여 극단 대조군이 아니게 된다.
"""


class SpreadOnly:
    name = "spread_only"

    def allocate(
        self, requests: Sequence[AllocationRequest], ctx: AllocationContext
    ) -> list[Assignment]:
        used = self._zone_load(ctx)
        out: list[Assignment] = []

        for req in sorted(requests, key=lambda r: (r.t, r.plate)):
            best: Assignment | None = None

            for slot_id in ctx.candidates(req):
                route = ctx.route_to(req, slot_id)
                if route is None:
                    continue
                zone = ctx.lot.slots[slot_id].row
                cost = used[zone] + LANE_TIE_BREAK * sum(
                    ctx.load.get(e, 0) for e in route.edges
                )
                if best is None or cost < best.cost:
                    best = Assignment(req.plate, slot_id, route, cost)

            if best is None:
                continue

            out.append(best)
            ctx.take(best.slot_id)
            used[ctx.lot.slots[best.slot_id].row] += 1

        return out

    def _zone_load(self, ctx: AllocationContext) -> Counter:
        """구역별로 이미 쓰이고 있는 자리 수.

        `ctx.available` 은 지금 **비어 있고 예약도 없는** 자리다. 구역 전체에서 그것을
        빼면 쓰이는 자리 수가 나온다 — 점유든 예약이든 구분할 필요가 없다.
        """
        free = Counter(ctx.lot.slots[sid].row for sid in ctx.available)
        total = Counter(s.row for s in ctx.lot.slots.values())
        return Counter({row: total[row] - free[row] for row in total})


register(SpreadOnly())
