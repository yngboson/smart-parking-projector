"""R4 `reserve_pool` — 일부 자리를 예비로 남겨 두고 사고가 나면 즉시 투입한다.

다른 전략들은 사고가 난 **뒤에** 자리를 찾는다. 이 전략은 **미리** 찾아 둔다.
전체의 k% 를 평시 배정에서 빼두고, 강탈이 발생하면 그 자리를 바로 준다.

**장점**: 복구가 즉각적이다. 피해자를 재탐색시키지 않고, 다른 차량의 유도선도
건드리지 않는다. 연쇄 피해가 원천적으로 없다.

**대가**: 아무 일도 없는 날에도 k% 가 놀고 있다. 만차에서 그 손해가 가장 크고,
하필 만차일 때 강탈이 가장 많이 일어난다 (D-015). 이 상충이 이 전략의 전부이며,
k 를 얼마로 두는 것이 옳은지는 **실험으로만** 답할 수 있다.

예비석은 건물에서 **중간쯤** 되는 자리로 고른다. 제일 좋은 자리를 빼두면 평시
만족도가 크게 떨어지고, 제일 나쁜 자리를 빼두면 피해자에게 벌을 주는 셈이 된다.
"""

from __future__ import annotations

from sim.common.ids import SlotId, SlotType
from sim.common.lotmap import LotMap
from sim.control.recovery.api import (
    BaseRecovery,
    Reassignment,
    RecoveryContext,
    RecoveryRequest,
    register,
)

RESERVE_SHARE = 0.08
"""예비로 남겨둘 비율. 실험에서 훑을 값이다 (k 스윕)."""


class ReservePool(BaseRecovery):
    name = "reserve_pool"

    def __init__(self, share: float = RESERVE_SHARE) -> None:
        self.share = share
        self._cache: dict[str, set[SlotId]] = {}

    # ── 평시 ──────────────────────────────────────────────────────

    def withhold(self, lot: LotMap) -> set[SlotId]:
        """평시 배정에서 빼둘 자리. 도면이 안 바뀌므로 한 번만 고른다."""
        if lot.name not in self._cache:
            self._cache[lot.name] = self._pick(lot)
        return self._cache[lot.name]

    def _pick(self, lot: LotMap) -> set[SlotId]:
        usable = [
            sid for sid, s in lot.slots.items()
            if s.slot_type not in (SlotType.DISABLED, SlotType.EV)
        ]
        usable.sort(key=lot.walk_distance)
        count = int(round(len(usable) * self.share))
        if count <= 0:
            return set()

        # 도보거리 중간 구간에서 고르게 뽑는다 — 좋은 자리도 나쁜 자리도 아니게.
        start = max(0, (len(usable) - count) // 2)
        return set(usable[start : start + count])

    # ── 사고 ──────────────────────────────────────────────────────

    def recover(self, request: RecoveryRequest, ctx: RecoveryContext) -> list[Reassignment]:
        reserve = self.withhold(ctx.lot)
        available = [s for s in ctx.free_slots if s in reserve]
        fallback = [s for s in ctx.free_slots if s not in reserve]

        out: list[Reassignment] = []
        for cand in sorted(ctx.victims(), key=lambda c: (-c.reroute_count, c.plate)):
            # 예비석을 먼저 쓴다. 그러라고 남겨둔 자리다.
            pick = ctx.best(cand, available) or ctx.best(cand, fallback)
            if pick is None:
                continue
            out.append(pick)
            for pool in (available, fallback):
                if pick.slot_id in pool:
                    pool.remove(pick.slot_id)
        return out


register(ReservePool())
