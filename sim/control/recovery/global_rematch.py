"""R2 `global_rematch` — 미주차 차량 전체를 다시 매칭한다. **기본 전략** (D-009).

한 사람이 자리를 빼앗겼을 때, 그 사람만 옮기는 것이 최선이라는 보장은 없다.
피해자에게 가장 가까운 자리가 다른 차량에게는 훨씬 더 좋은 자리일 수 있고, 그렇다면
**모두가 조금씩 양보하는 편**이 전체 우회 거리를 줄인다.

그래서 사건이 날 때마다 "아직 주차하지 못한 차량 전체 × 아직 아무도 앉지 않은 자리
전체"의 비용 행렬을 다시 세우고 헝가리안 매칭으로 최적 배분을 구한다.

**대가**: 자기 잘못이 없는 차량의 유도선까지 바뀐다. 바닥의 선이 갑자기 다른 곳을
가리키는 경험이 운전자에게 어떤지는 이 시뮬레이터가 답할 수 없다 — 그래서
`RESHUFFLE` 로 따로 세어 "몇 대가 휘말렸는가"를 기록한다. 그 숫자가 이 전략의 비용이다.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import linear_sum_assignment

from sim.common.messages import GuidanceReason
from sim.control.recovery.api import (
    BaseRecovery,
    Reassignment,
    RecoveryContext,
    RecoveryRequest,
    register,
)

UNREACHABLE = 1.0e6
"""갈 수 없는 조합의 비용. 헝가리안은 빈칸을 다루지 못하므로 아주 비싸게 매긴다."""


class GlobalRematch(BaseRecovery):
    name = "global_rematch"

    def recover(self, request: RecoveryRequest, ctx: RecoveryContext) -> list[Reassignment]:
        movers = sorted(ctx.candidates, key=lambda c: c.plate)
        pool = ctx.pool()
        if not movers or not pool:
            return []

        cost = np.full((len(movers), len(pool)), UNREACHABLE)
        routes: dict[tuple[int, int], object] = {}

        for i, cand in enumerate(movers):
            for j, sid in enumerate(pool):
                got = ctx.evaluate(cand, sid)
                if got is None:
                    continue
                routes[(i, j)] = got[0]
                cost[i, j] = got[1]

        rows, cols = linear_sum_assignment(cost)

        out: list[Reassignment] = []
        for i, j in zip(rows, cols):
            if cost[i, j] >= UNREACHABLE:
                continue
            cand, sid = movers[i], pool[j]
            if cand.held_slot == sid:
                continue        # 바뀌지 않은 차량의 유도선은 건드리지 않는다
            out.append(
                Reassignment(
                    plate=cand.plate,
                    slot_id=sid,
                    route=routes[(i, j)],       # type: ignore[arg-type]
                    reason=(
                        GuidanceReason.REROUTE if cand.is_victim
                        else GuidanceReason.RESHUFFLE
                    ),
                )
            )
        return out


register(GlobalRematch())
