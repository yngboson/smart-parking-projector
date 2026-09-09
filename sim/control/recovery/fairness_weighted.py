"""R6 `fairness_weighted` — 여러 번 밀려난 사람에게 우선권을 준다.

다른 전략들은 **평균**을 줄인다. 이 전략은 **최악**을 줄인다.

평균만 보면 놓치는 것이 있다. 같은 사람이 세 번 연속 자리를 빼앗기고 세 번 다
주차장을 가로질러 다시 가는 일이 실제로 일어난다 — 전체 평균은 멀쩡한데 그 사람의
경험은 최악이다. 발표에서 "평균 주차시간이 줄었습니다"라고만 말하면 그 사람의
이야기는 사라진다. 그래서 p95 를 따로 본다.

    이미 밀려난 횟수가 많을수록 → 배정 비용을 깎아 준다 (= 경쟁에서 이긴다)

**대가**: 평균은 나빠진다. 자원을 '가장 잘 쓸 사람'이 아니라 '가장 손해 본 사람'에게
주기 때문이다. 그 손해가 얼마인지, p95 개선이 그만한 값어치인지가 이 전략의 측정값이다.

`reputation_aware`(R5)와 정확히 반대 방향이다. R5 는 규칙을 어긴 사람을 달래고,
R6 는 규칙을 지키다 손해 본 사람을 보상한다. 둘을 나란히 놓는 것이 이 비교의 백미다.

**'우선권' 을 어떻게 표현하는가 — 한 번 틀렸다.**

처음에는 배정 비용에서 상수를 깎았다(`비용 − 30m × 재탐색횟수`). **아무 일도
일어나지 않았다.** 그 상수는 그 차량의 **모든 후보 자리에 똑같이** 적용되므로
최소값이 바뀌지 않는다. 게다가 기본 할당 전략은 차량을 한 대씩 처리해서 차량끼리
비용을 견주지도 않는다. 10개 시드 전부에서 `local_reassign` 과 **바이트 단위로
같은 결과**가 나왔다 (`runs/repeat-offenders`).

그래서 깎는 양을 **자리에 따라** 다르게 한다 — 좋은 자리(건물에 가까운 자리)일수록
크게 깎는다. 그러면 여러 번 밀려난 사람이 더 좋은 자리 쪽으로 끌린다. 이것이 이
관제가 표현할 수 있는 '우선권'이다.

    깎는 양 = step × min(재탐색 횟수, 상한) × (1 − 자리 순위)
                                              ↑ 0 = 건물에 가장 가까운 자리
"""

from __future__ import annotations

from sim.common.ids import PlateId, SlotId
from sim.common.lotmap import LotMap
from sim.control.cost import walk_rank
from sim.control.recovery.api import (
    BaseRecovery,
    BiasContext,
    Candidate,
    Reassignment,
    RecoveryContext,
    RecoveryRequest,
    register,
)

PRIORITY_STEP = 30.0
"""재탐색 한 번당, **가장 좋은 자리에** 깎아 주는 비용(m 환산).

가장 나쁜 자리에는 0 을 깎는다. 그 사이는 도보거리 순위로 비례한다 —
`cost.CostWeights.walk` 가 도보 1m 를 1.8 로 치므로, 30 은 도보 17m 어치다.
"""

MAX_PRIORITY = 4
"""보정 상한. 없으면 한 사람이 영원히 모든 경쟁을 이긴다."""


class FairnessWeighted(BaseRecovery):
    name = "fairness_weighted"

    def __init__(self, step: float = PRIORITY_STEP) -> None:
        self.step = step
        self._rank: dict[str, dict[SlotId, float]] = {}

    def bias(self, plate: PlateId, slot_id: SlotId, ctx: BiasContext) -> float:
        rank = self._walk_rank(ctx.lot).get(slot_id, 1.0)
        return -self._discount(ctx.reroute_count(plate)) * (1.0 - rank)

    def _walk_rank(self, lot: LotMap) -> dict[SlotId, float]:
        if lot.name not in self._rank:
            self._rank[lot.name] = walk_rank(lot)
        return self._rank[lot.name]

    def recover(self, request: RecoveryRequest, ctx: RecoveryContext) -> list[Reassignment]:
        free = list(ctx.free_slots)

        # 많이 밀려난 사람부터 고른다. 이 순서가 이 전략의 전부다.
        order = sorted(ctx.victims(), key=lambda c: (-c.reroute_count, c.plate))

        rank = self._walk_rank(ctx.lot)
        out: list[Reassignment] = []
        for cand in order:
            pick = self._cheapest(cand, ctx, free, rank)
            if pick is None:
                continue
            out.append(pick)
            free.remove(pick.slot_id)
        return out

    def _cheapest(
        self, cand: Candidate, ctx: RecoveryContext, free: list[SlotId], rank
    ) -> Reassignment | None:
        discount = self._discount(cand.reroute_count)
        best: Reassignment | None = None
        cheapest = float("inf")

        for sid in free:
            got = ctx.evaluate(cand, sid)
            if got is None:
                continue
            adjusted = got[1] - discount * (1.0 - rank.get(sid, 1.0))
            if adjusted < cheapest:
                cheapest = adjusted
                best = Reassignment(cand.plate, sid, got[0])
        return best

    def _discount(self, reroutes: int) -> float:
        return self.step * min(max(0, reroutes), MAX_PRIORITY)


register(FairnessWeighted())
