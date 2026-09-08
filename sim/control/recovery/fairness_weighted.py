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
"""

from __future__ import annotations

from sim.common.ids import PlateId, SlotId
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
"""재탐색 한 번당 깎아 주는 비용(m 환산). 우회 30m 를 면제해 주는 셈이다."""

MAX_PRIORITY = 4
"""보정 상한. 없으면 한 사람이 영원히 모든 경쟁을 이긴다."""


class FairnessWeighted(BaseRecovery):
    name = "fairness_weighted"

    def __init__(self, step: float = PRIORITY_STEP) -> None:
        self.step = step

    def bias(self, plate: PlateId, slot_id: SlotId, ctx: BiasContext) -> float:
        return -self._discount(ctx.reroute_count(plate))

    def recover(self, request: RecoveryRequest, ctx: RecoveryContext) -> list[Reassignment]:
        free = list(ctx.free_slots)

        # 많이 밀려난 사람부터 고른다. 이 순서가 이 전략의 전부다.
        order = sorted(ctx.victims(), key=lambda c: (-c.reroute_count, c.plate))

        out: list[Reassignment] = []
        for cand in order:
            pick = self._cheapest(cand, ctx, free)
            if pick is None:
                continue
            out.append(pick)
            free.remove(pick.slot_id)
        return out

    def _cheapest(
        self, cand: Candidate, ctx: RecoveryContext, free: list[SlotId]
    ) -> Reassignment | None:
        discount = self._discount(cand.reroute_count)
        best: Reassignment | None = None
        cheapest = float("inf")

        for sid in free:
            got = ctx.evaluate(cand, sid)
            if got is None:
                continue
            adjusted = got[1] - discount
            if adjusted < cheapest:
                cheapest = adjusted
                best = Reassignment(cand.plate, sid, got[0])
        return best

    def _discount(self, reroutes: int) -> float:
        return self.step * min(max(0, reroutes), MAX_PRIORITY)


register(FairnessWeighted())
