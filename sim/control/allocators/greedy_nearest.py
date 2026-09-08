"""`greedy_nearest` — 도착 즉시 최소비용 주차면을 준다.

가장 단순한 전략이고, 그래서 **기준선**이다. 다른 전략이 얼마나 나은지를
말하려면 "아무 생각 없이 만든 것"과 비교해야 한다.

한계는 설계상 명백하다:
  - 앞차의 배정이 뒷차에게 어떤 영향을 주는지 보지 않는다 (배치 최적화 없음)
  - 한 대씩 최선을 고르므로 여러 대가 같은 방향으로 몰릴 수 있다

혼잡 비용(w₃)이 그 쏠림을 조금 눌러 준다. 배정할 때마다 그 경로의 엣지 점유가
올라가므로, 같은 배치의 뒷 차량은 다른 통로를 보게 된다. 완전한 해법은 아니고
`congestion_aware` / `hungarian_batch` 가 다룰 몫이다.
"""

from __future__ import annotations

from typing import Sequence

from sim.control.allocators.api import (
    AllocationContext,
    AllocationRequest,
    Assignment,
    register,
)


class GreedyNearest:
    """비용이 가장 낮은 빈 주차면을 하나씩 집어 준다."""

    name = "greedy_nearest"

    def allocate(
        self, requests: Sequence[AllocationRequest], ctx: AllocationContext
    ) -> list[Assignment]:
        out: list[Assignment] = []

        # 오래 기다린 차량부터 처리한다. 같은 시각이면 번호판 순 — 시드가 같으면
        # 결과가 같아야 실험이 재현된다.
        for req in sorted(requests, key=lambda r: (r.t, r.plate)):
            best: Assignment | None = None
            for slot_id in ctx.candidates(req):
                got = ctx.evaluate(req, slot_id)
                if got is None:
                    continue
                route, cost = got
                if best is None or cost < best.cost:
                    best = Assignment(req.plate, slot_id, route, cost)

            if best is None:
                continue  # 만차이거나 갈 수 있는 자리가 없다

            out.append(best)
            ctx.take(best.slot_id)
            _add_load(ctx, best)

        return out


def _add_load(ctx: AllocationContext, a: Assignment) -> None:
    """방금 배정한 경로를 혼잡도에 반영한다.

    같은 배치의 뒷 차량이 앞 차량의 경로를 피하도록 만드는 유일한 장치다.
    """
    load = dict(ctx.load)
    for edge in a.route.edges:
        load[edge] = load.get(edge, 0) + 1
    ctx.load = load


register(GreedyNearest())
