"""`walk_only` — 건물에서 가장 가까운 자리를 **먼저 온 사람이** 가져간다.

주차장에서 사람이 실제로 원하는 것 하나만 보는 전략입니다. 주행 거리도, 회전도,
통로 혼잡도 보지 않습니다. 빈 자리 중 **건물에 제일 가까운 것**을 줍니다.

    비용 = 건물까지의 도보 거리

**일부러 극단으로 만들었습니다.** 이 전략이 좋아서가 아니라, 다른 전략들이 어디쯤
있는지 **좌표를 주기 위해서**입니다 (docs/ALLOCATION_MODEL.md 5절). 지금까지 만든
넷은 같은 비용 함수의 가중치만 다른 형제라서 서로 몰려 있었고, 그래서 "이 전략이
분산형이다/집중형이다"라고 말할 기준이 없었습니다.

**이 전략이 만드는 혼잡**: 모두를 A 구역 한쪽으로 몰아넣습니다. 좋은 자리가 먼저
소진되고, 그 구역 통로만 붐빕니다. 나머지 주차장은 비어 있는데도요. 집중 혼잡의
**상한**이 여기서 나옵니다.

**동시에 강탈이 가장 적을 수 있습니다.** 운전자가 이탈하는 이유는 "배정받은 자리보다
눈앞의 자리가 더 좋아서"인데(`agents.driver._consider_defection`), 이미 제일 좋은
자리를 받았다면 이탈할 이유가 없습니다. 혼잡과 순응이 정반대로 움직이는지가
이 전략이 답하는 질문입니다.
"""

from __future__ import annotations

from typing import Sequence

from sim.control.allocators.api import (
    AllocationContext,
    AllocationRequest,
    Assignment,
    register,
)


class WalkOnly:
    name = "walk_only"

    def allocate(
        self, requests: Sequence[AllocationRequest], ctx: AllocationContext
    ) -> list[Assignment]:
        out: list[Assignment] = []

        # 먼저 온 사람부터. 같은 시각이면 번호판 순 — 시드가 같으면 결과도 같아야 한다.
        for req in sorted(requests, key=lambda r: (r.t, r.plate)):
            best: Assignment | None = None

            # 도보가 가까운 순으로 보다가 **갈 수 있는 첫 자리**에서 멈춘다.
            # 전부 평가할 이유가 없다 — 비용이 도보 거리 하나뿐이므로 순서가 곧 답이다.
            for slot_id in sorted(ctx.candidates(req), key=ctx.lot.walk_distance):
                route = ctx.route_to(req, slot_id)
                if route is None:
                    continue        # 일방통행 때문에 갈 수 없는 자리
                best = Assignment(
                    req.plate, slot_id, route, ctx.lot.walk_distance(slot_id)
                )
                break

            if best is None:
                continue
            out.append(best)
            ctx.take(best.slot_id)

        return out


register(WalkOnly())
