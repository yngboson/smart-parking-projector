"""R3 `chain_shift` — 예약을 연쇄로 이양한다. 전체 재매칭의 저비용 근사.

`global_rematch`(R2)의 문제는 **아무 잘못 없는 차량 여럿의 유도선이 한꺼번에
바뀐다**는 것이다. 바닥의 선이 갑자기 다른 곳을 가리키면 운전자는 시스템을 못 믿게 된다.

그래서 반대로 접근한다 — **최소한의 차량만 건드린다.**

    피해자 V 가 A 의 자리를 받고, A 는 B 의 자리를 받고, B 는 빈 자리로 간다.

이런 사슬 중 전체 비용 증가가 가장 작은 것을 고른다. 이것은 최소비용 증가경로
(min-cost augmenting path)의 축소판이고, 헝가리안이 내부적으로 하는 일이기도 하다.
전체를 다시 풀지 않고 사슬 하나만 찾으므로 훨씬 싸고, 유도선이 바뀌는 차량 수가
사슬 길이로 **상한이 정해진다** — R2 에는 그런 보장이 없다.

깊이를 제한하는 이유: 사슬이 길어질수록 최적에 가까워지지만 휘말리는 차량이 늘어난다.
그 균형점을 실험으로 찾으라고 파라미터로 남겨 둔다.
"""

from __future__ import annotations

import math

from sim.control.recovery.api import (
    BaseRecovery,
    Candidate,
    Reassignment,
    RecoveryContext,
    RecoveryRequest,
    register,
)

MAX_DEPTH = 3
"""사슬의 최대 길이. 이만큼의 차량까지만 휘말린다."""

BEAM = 4
"""각 단계에서 살펴볼 후보 자리 수. 전수 탐색은 필요 없다 — 근사면 충분하다."""


class ChainShift(BaseRecovery):
    name = "chain_shift"

    def recover(self, request: RecoveryRequest, ctx: RecoveryContext) -> list[Reassignment]:
        free = list(ctx.free_slots)
        holders = {
            c.held_slot: c for c in ctx.candidates
            if c.held_slot is not None and not c.is_victim
        }

        out: list[Reassignment] = []
        for cand in sorted(ctx.victims(), key=lambda c: (-c.reroute_count, c.plate)):
            chain = self._best_chain(cand, ctx, free, holders, depth=MAX_DEPTH)
            if not chain:
                continue

            moved = {step.plate for step in chain}
            for step in chain:
                out.append(step)
                holders.pop(step.slot_id, None)
                if step.slot_id in free:
                    free.remove(step.slot_id)

            # 이미 옮긴 차량은 다음 피해자의 사슬에 다시 끌어들이지 않는다.
            # 한 사건에서 같은 사람을 두 번 밀어내면 유도선이 두 번 바뀐다.
            holders = {k: v for k, v in holders.items() if v.plate not in moved}
        return out

    # ── 사슬 탐색 ─────────────────────────────────────────────────

    def _best_chain(
        self,
        cand: Candidate,
        ctx: RecoveryContext,
        free: list[str],
        holders: dict,
        depth: int,
    ) -> list[Reassignment]:
        """이 차량을 앉히는 가장 싼 사슬. 못 찾으면 빈 목록."""
        best: list[Reassignment] = []
        best_cost = math.inf

        # ① 빈 자리로 곧장 간다 — 사슬 길이 1
        direct = ctx.best(cand, free)
        if direct is not None:
            got = ctx.evaluate(cand, direct.slot_id)
            if got is not None:
                best, best_cost = [direct], got[1]

        if depth <= 1:
            return best

        # ② 남의 자리를 받고, 그 사람을 다시 앉힌다
        for sid, holder in self._nearest_held(cand, ctx, holders):
            mine = ctx.evaluate(cand, sid)
            if mine is None:
                continue
            current = ctx.evaluate(holder, sid)
            if current is None:
                continue

            rest = self._best_chain(
                holder, ctx, free,
                {k: v for k, v in holders.items() if k != sid},
                depth - 1,
            )
            if not rest:
                continue

            # 비용 증가분: 내가 새로 드는 비용 + 밀려난 사람의 비용 변화
            moved = sum(self._cost_of(ctx, step) for step in rest)
            total = mine[1] + moved - current[1]
            if total < best_cost:
                best_cost = total
                best = [
                    Reassignment(cand.plate, sid, mine[0], _reason(cand)),
                    *rest,
                ]
        return best

    def _nearest_held(self, cand: Candidate, ctx: RecoveryContext, holders: dict):
        """이 차량에게 싼 순서로 '남이 잡고 있는 자리' 몇 개."""
        scored = []
        for sid, holder in holders.items():
            got = ctx.evaluate(cand, sid)
            if got is not None:
                scored.append((got[1], sid, holder))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [(sid, holder) for _c, sid, holder in scored[:BEAM]]

    @staticmethod
    def _cost_of(ctx: RecoveryContext, step: Reassignment) -> float:
        cand = next(
            (c for c in ctx.candidates if c.plate == step.plate), None
        )
        if cand is None:
            return 0.0
        got = ctx.evaluate(cand, step.slot_id)
        return 0.0 if got is None else got[1]


def _reason(cand: Candidate):
    from sim.common.messages import GuidanceReason

    return GuidanceReason.REROUTE if cand.is_victim else GuidanceReason.RESHUFFLE


register(ChainShift())
