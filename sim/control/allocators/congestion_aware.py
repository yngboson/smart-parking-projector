"""`congestion_aware` — 통로 혼잡을 **비선형으로** 보고 흐름을 분산한다.

기본 비용 함수에도 혼잡 항(w₃)이 있다. 하지만 그것은 겹친 엣지의 점유 **합**,
즉 선형이다. 선형 벌점은 쏠림을 못 막는다 — 한 통로에 이미 다섯 대가 몰려 있어도
여섯 번째 차가 내는 추가 비용은 첫 번째 차와 똑같기 때문이다. 그동안 그 통로의
실제 통행 시간은 선형이 아니라 급격히 나빠진다.

그래서 이 전략은 **제곱 항**을 얹는다.

    추가비용 = w₃ · Σ_e (점유_e)²

점유 1인 엣지 다섯 개(합 5)보다 점유 5인 엣지 하나(합 5)가 훨씬 비싸진다. 앞의
것은 다섯 통로에 흩어진 상태이고 뒤의 것은 한 통로에 쌓인 상태다 — 같은 값으로
볼 이유가 없다.

**배치 안에서도 누적한다.** `greedy_nearest` 와 마찬가지로 한 대를 배정할 때마다
그 경로의 점유를 올린다. 제곱 항 덕분에 그 효과가 훨씬 세게 나타나, 같은 배치의
뒷 차량이 앞 차량의 통로를 확실히 피한다.

**대가**: 걷는 거리와 주행 거리를 희생해 통로를 비운다. 한산할 때는 순수한 손해이고
(피할 혼잡이 없다), 붐빌 때 이득이 난다. 그 교차점이 이 전략의 실험 결과다.
"""

from __future__ import annotations

from typing import Sequence

from sim.control.allocators.api import (
    AllocationContext,
    AllocationRequest,
    Assignment,
    register,
)


class CongestionAware:
    name = "congestion_aware"

    def allocate(
        self, requests: Sequence[AllocationRequest], ctx: AllocationContext
    ) -> list[Assignment]:
        out: list[Assignment] = []

        for req in sorted(requests, key=lambda r: (r.t, r.plate)):
            best: Assignment | None = None
            for slot_id in ctx.candidates(req):
                got = ctx.evaluate(req, slot_id)
                if got is None:
                    continue
                route, cost = got
                cost += _pileup_penalty(ctx, route)
                if best is None or cost < best.cost:
                    best = Assignment(req.plate, slot_id, route, cost)

            if best is None:
                continue

            out.append(best)
            ctx.take(best.slot_id)
            _add_load(ctx, best.route)

        return out


def _pileup_penalty(ctx: AllocationContext, route) -> float:
    """쌓임에 대한 비선형 벌점. 기본 비용의 선형 혼잡 항 위에 얹는다."""
    return ctx.weights.congestion * sum(
        ctx.load.get(edge, 0) ** 2 for edge in route.edges
    )


def _add_load(ctx: AllocationContext, route) -> None:
    load = dict(ctx.load)
    for edge in route.edges:
        load[edge] = load.get(edge, 0) + 1
    ctx.load = load


register(CongestionAware())
