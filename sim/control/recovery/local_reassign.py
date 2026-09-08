"""R1 `local_reassign` — 피해 차량에게만 최근접 빈 자리를 준다.

가장 단순한 대응이고, 그래서 기준선이다. 다른 전략이 얼마나 나은지 말하려면
"아무 고민 없이 만든 것"과 비교해야 한다.

**한계는 설계상 명백하다.** 피해자만 본다. 그 피해자가 받은 자리가 원래 다른 차에게
더 어울렸는지, 이 배정이 통로를 어떻게 막는지는 고려하지 않는다. 강탈이 몰릴 때
"피해자가 또 피해자를 만드는" 연쇄가 일어나기 쉽다 — 그 연쇄가 실제로 얼마나
일어나는지가 이 전략의 측정값이다.
"""

from __future__ import annotations

from sim.control.recovery.api import (
    BaseRecovery,
    Reassignment,
    RecoveryContext,
    RecoveryRequest,
    register,
)


class LocalReassign(BaseRecovery):
    name = "local_reassign"

    def recover(self, request: RecoveryRequest, ctx: RecoveryContext) -> list[Reassignment]:
        free = list(ctx.free_slots)
        out: list[Reassignment] = []

        # 오래 기다린 피해자부터. 같은 조건이면 번호판 순 — 재현성을 위해서다.
        for cand in sorted(ctx.victims(), key=lambda c: (-c.reroute_count, c.plate)):
            pick = ctx.best(cand, free)
            if pick is None:
                continue
            out.append(pick)
            free.remove(pick.slot_id)
        return out


register(LocalReassign())
