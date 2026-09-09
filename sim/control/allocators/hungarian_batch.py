"""`hungarian_batch` — 한 배치를 **함께** 놓고 최적 매칭한다.

`greedy_nearest` 는 한 대씩 최선을 고른다. 그래서 먼저 온 차가 두 번째 차에게
훨씬 더 좋았을 자리를 가져가고, 두 번째 차는 훨씬 먼 데로 간다. 두 대를 함께 놓고
풀면 **총합이 더 낮은 배분**이 나온다.

    갑: A자리 10, B자리 12        greedy:    갑→A(10), 을→B(30)   합 40
    을: A자리  9, B자리 30        헝가리안:  갑→B(12), 을→A( 9)   합 21

**시간창으로 도착을 모으는 방식은 버렸다. 이 주차장에서는 성립하지 않는다.**

docs/PLAN.md 4절은 "짧은 시간창(예 3초) 내 도착 차량을 묶는다"고 적었고 그대로
만들어 봤다. 배치는 **한 번도** 둘 이상이 되지 않았다 (측정: 도착률 0.25/초에서
219회, 0.8/초에서 179회, 초기점유 90%에서 357회 — 전부 크기 1).

이유는 순환이었다.

    자리를 기다리는 차는 입구에 서 있다
      → 입구에 선 차는 뒷차의 진입을 막는다 (`SimConfig.entry_clearance`)
      → 그래서 같은 창 안에 두 번째 차가 **도착할 수가 없다**
      → 창은 배치를 못 만들고 지연만 남긴다

측정된 대가도 컸다. 창을 3초로 두었을 때 400초 동안 주차 완료가 79대에서 47대로
떨어졌다.

그래서 **붙잡지 않는다.** 관제가 한 번에 여러 대를 올려 줄 때만 배치가 되고,
그때는 이 전략이 `greedy_nearest` 보다 나은 배분을 낸다. 한 대짜리 배치에서는
둘이 같은 답을 낸다 — 그것이 이 주차장의 사실이고, 숨기지 않는 편이 낫다.

**언제 배치가 생기는가**: 만차라서 여러 대가 자리를 기다리다가 한꺼번에 풀릴 때,
그리고 `zone_late_binding` 처럼 재문의가 섞일 때다.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from sim.control.allocators.api import (
    AllocationContext,
    AllocationRequest,
    Assignment,
    register,
)

UNREACHABLE = 1.0e6
"""갈 수 없는 조합의 비용. 헝가리안은 빈칸을 다루지 못하므로 아주 비싸게 매긴다."""


class HungarianBatch:
    name = "hungarian_batch"

    def allocate(
        self, requests: Sequence[AllocationRequest], ctx: AllocationContext
    ) -> list[Assignment]:
        # 오래 기다린 차량부터. 같은 시각이면 번호판 순 — 시드가 같으면 결과도 같아야 한다.
        movers = sorted(requests, key=lambda r: (r.t, r.plate))
        if not movers:
            return []

        # 후보 자리는 요청들의 합집합. 차종 제약이 다르므로 칸을 채울 때 다시 본다.
        eligible = [set(ctx.candidates(r)) for r in movers]
        pool: list = []
        seen: set = set()
        for cands in eligible:
            for sid in sorted(cands):
                if sid not in seen:
                    seen.add(sid)
                    pool.append(sid)
        if not pool:
            return []

        cost = np.full((len(movers), len(pool)), UNREACHABLE)
        routes: dict[tuple[int, int], object] = {}

        for i, req in enumerate(movers):
            for j, sid in enumerate(pool):
                if sid not in eligible[i]:
                    continue
                got = ctx.evaluate(req, sid)
                if got is None:
                    continue
                routes[(i, j)] = got[0]
                cost[i, j] = got[1]

        rows, cols = linear_sum_assignment(cost)

        out: list[Assignment] = []
        for i, j in zip(rows, cols):
            if cost[i, j] >= UNREACHABLE:
                continue        # 이 차량이 갈 수 있는 자리가 없었다
            out.append(
                Assignment(
                    plate=movers[i].plate,
                    slot_id=pool[j],
                    route=routes[(i, j)],   # type: ignore[arg-type]
                    cost=float(cost[i, j]),
                )
            )
            ctx.take(pool[j])
        return out


register(HungarianBatch())
