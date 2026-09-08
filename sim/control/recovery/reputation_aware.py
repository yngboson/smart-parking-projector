"""R5 `reputation_aware` — 관측된 행동 이력으로 배정을 조정한다.

**이 전략이 D-001(계층 분리)의 정당성을 보여주는 사례다.**

관제는 운전자의 내부 성향을 볼 수 없다 — 그런 값이 있다는 사실조차 모른다.
하지만 **센서에 남은 과거 행동**은
집계할 수 있다 — 이 번호판이 예전에 안내를 벗어난 적이 있는가, 남의 자리를 차지한
적이 있는가 (D-003). 성향을 훔쳐보는 것과 관측을 학습하는 것은 다르다. 실제 시스템도
후자는 할 수 있다.

**전략의 논지**: 이탈은 배신이 아니라 **저울의 불일치**다 (D-014). 안내받은 자리가
자기 기준으로 나쁘면 사람은 딴 데로 간다. 그렇다면 이탈 이력이 있는 번호판에게는
애초에 **본인이 만족할 자리**를 주면 된다. 벌을 주는 것이 아니라 달래는 것이다.

    신뢰도가 낮을수록 → 도보거리가 짧은 자리를 더 싸게 평가한다

**대가는 명백하고, 그 자체가 실험 결과다.** 규칙을 어긴 사람이 더 좋은 자리를 받는다.
전체 효율은 나아질 수 있어도 공평하지 않다. `fairness_weighted`(R6)가 정확히 반대
방향이므로 두 전략을 나란히 놓으면 "효율 대 공평"의 상충을 숫자로 보여줄 수 있다.
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

APPEASE = 45.0
"""달래기 보정의 최대 크기(m 환산).

`cost.CostWeights.walk` 가 도보 1m 를 1.8 로 치므로, 45 는 도보 25m 어치다 —
구역 두 줄쯤 앞당겨 주는 셈이다. 이보다 크면 상습 이탈자가 언제나 최고의 자리를
독차지하게 되어, 이 전략이 '학습'이 아니라 '항복'이 된다.
"""


class ReputationAware(BaseRecovery):
    name = "reputation_aware"

    def __init__(self, appease: float = APPEASE) -> None:
        self.appease = appease
        self._rank: dict[str, dict[SlotId, float]] = {}

    # ── 평시 배정 보정 ────────────────────────────────────────────

    def bias(self, plate: PlateId, slot_id: SlotId, ctx: BiasContext) -> float:
        trust = ctx.trust(plate)
        if trust >= 1.0:
            return 0.0
        # rank 0 = 건물에 가장 가까운 자리. 좋은 자리일수록 크게 깎아 준다.
        rank = self._walk_rank(ctx.lot).get(slot_id, 1.0)
        return -self.appease * (1.0 - trust) * (1.0 - rank)

    def _walk_rank(self, lot: LotMap) -> dict[SlotId, float]:
        if lot.name not in self._rank:
            self._rank[lot.name] = walk_rank(lot)
        return self._rank[lot.name]

    # ── 사고 ──────────────────────────────────────────────────────

    def recover(self, request: RecoveryRequest, ctx: RecoveryContext) -> list[Reassignment]:
        free = list(ctx.free_slots)
        rank = self._walk_rank(ctx.lot)

        # 신뢰도가 높은 피해자부터 고른다. 여러 번 당한 사람이 또 밀리지 않게,
        # 재탐색 횟수도 함께 본다.
        order = sorted(
            ctx.victims(),
            key=lambda c: (-ctx.trust(c.plate), -c.reroute_count, c.plate),
        )

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
        trust = ctx.trust(cand.plate)
        best: Reassignment | None = None
        cheapest = float("inf")

        for sid in free:
            got = ctx.evaluate(cand, sid)
            if got is None:
                continue
            adjusted = got[1] - self.appease * (1.0 - trust) * (1.0 - rank.get(sid, 1.0))
            if adjusted < cheapest:
                cheapest = adjusted
                best = Reassignment(cand.plate, sid, got[0])
        return best


register(ReputationAware())
