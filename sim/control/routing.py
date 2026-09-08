"""레인 그래프 경로 탐색 — A* + 회전 페널티.

관제가 "이 차를 저 주차면까지 어떻게 보낼 것인가"를 푸는 곳이다.

일방통행은 **지켜야 할 규칙이 아니라 그래프상 존재하지 않는 길**이다
(`LotMap.successors` 가 이미 방향성 엣지만 돌려준다). 따라서 여기서 일방통행을
검사할 필요가 없고, 검사하지 않는 편이 안전하다 — 검사가 있으면 언젠가 그 검사를
끄는 코드가 생긴다.

**회전 페널티가 왜 필요한가.** 최단거리만 보면 통로를 지그재그로 가로지르는 경로가
나온다. 거리는 짧지만 운전자가 따라가기 나쁘고, 교차로마다 다른 차와 얽힌다.
좌회전에 우회전보다 큰 페널티를 주는 것은 우측통행에서 좌회전이 마주오는 차선을
가로지르기 때문이다 — 실제 물류 경로 최적화에서 쓰는 것과 같은 이유다.

탐색 상태가 노드가 아니라 **(직전 노드, 현재 노드)** 쌍인 것도 이 때문이다.
회전 비용은 "어디에 있는가"가 아니라 "어느 방향에서 들어왔는가"에 달려 있다.

이 파일은 ``sim.common`` 외에는 아무것도 import 하지 않는다 (CLAUDE.md).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Mapping

from sim.common.geometry import Vec2, angle_diff
from sim.common.ids import NodeId
from sim.common.lotmap import LotMap

EdgeKey = tuple[NodeId, NodeId]


@dataclass(frozen=True, slots=True)
class TurnCost:
    """회전 한 번의 비용. 단위는 **미터 환산**이다.

    거리와 같은 단위로 두면 "좌회전 한 번 = 우회 9m" 처럼 직관적으로 읽히고,
    비용 함수의 다른 항들과 섞어 쓸 때 가중치가 이상해지지 않는다.
    """

    straight: float = 0.35
    """이 각도(rad) 이하는 회전으로 세지 않는다. 격자 도면의 미세한 꺾임 흡수용."""

    left: float = 9.0
    right: float = 3.5
    u_turn: float = 60.0
    """양방향 통로 안에서의 되돌아가기. 물리적으로 가능은 하지만 매우 비싸다."""

    def of(self, delta: float) -> float:
        """진행 방향이 delta(rad)만큼 꺾일 때의 비용. delta > 0 이 좌회전."""
        a = abs(delta)
        if a <= self.straight:
            return 0.0
        if a >= math.pi - 0.35:
            return self.u_turn
        return self.left if delta > 0.0 else self.right


@dataclass(frozen=True, slots=True)
class Route:
    """탐색 결과 하나. 불변이며, 그대로 유도선 폴리라인이 된다."""

    nodes: tuple[NodeId, ...]
    polyline: tuple[Vec2, ...]
    length: float
    """실제 주행 거리(m). 회전 페널티는 포함하지 않는다."""

    turns: int
    cost: float
    """A* 가 최소화한 값 = 거리 + 회전 페널티 + 혼잡 페널티."""

    @property
    def edges(self) -> tuple[EdgeKey, ...]:
        """지나가는 방향성 엣지들. 혼잡도 집계의 단위다."""
        return tuple(zip(self.nodes, self.nodes[1:]))

    @property
    def goal(self) -> NodeId:
        return self.nodes[-1]

    def suffix_from(self, node: NodeId) -> "Route | None":
        """주어진 노드부터 끝까지의 부분 경로. 이미 지나온 구간을 잘라낼 때 쓴다."""
        try:
            i = self.nodes.index(node)
        except ValueError:
            return None
        if i == len(self.nodes) - 1:
            return None
        return Route(
            nodes=self.nodes[i:],
            polyline=self.polyline[i:],
            length=sum(
                self.polyline[j].distance_to(self.polyline[j + 1])
                for j in range(i, len(self.polyline) - 1)
            ),
            turns=self.turns,
            cost=self.cost,
        )


class LaneRouter:
    """도면 하나에 붙어 있는 경로 탐색기.

    혼잡 페널티가 없는 질의는 캐시한다. 도면은 불변이고 같은 (출발, 도착) 조합이
    반복해서 들어오기 때문이다 — 차량 한 대의 후보 주차면 120개를 매번 새로 푸는
    것이 이 시뮬레이터에서 가장 무거운 연산이다.
    """

    def __init__(self, lot: LotMap, turn_cost: TurnCost | None = None) -> None:
        self.lot = lot
        self.turn_cost = turn_cost or TurnCost()
        self._cache: dict[tuple[NodeId | None, NodeId, NodeId], Route | None] = {}

    # ── 조회 ──────────────────────────────────────────────────────

    def route(
        self,
        start: NodeId,
        goal: NodeId,
        *,
        arrive_from: NodeId | None = None,
        edge_penalty: Mapping[EdgeKey, float] | None = None,
    ) -> Route | None:
        """start 에서 goal 까지의 최소비용 경로. 길이 없으면 None.

        :param arrive_from: 차량이 start 노드에 **어느 방향에서** 들어왔는가.
            첫 번째 회전의 비용을 제대로 매기려면 필요하다. 모르면 첫 회전은 공짜.
        :param edge_penalty: 엣지별 추가 비용(m 환산). 혼잡 회피에 쓴다.
        """
        if start == goal:
            pos = self.lot.node_pos(start)
            return Route((start,), (pos,), 0.0, 0, 0.0)

        if edge_penalty:
            return self._search(start, goal, arrive_from, edge_penalty)

        key = (arrive_from, start, goal)
        if key not in self._cache:
            self._cache[key] = self._search(start, goal, arrive_from, None)
        return self._cache[key]

    def distance(self, start: NodeId, goal: NodeId) -> float:
        """주행 거리만. 경로가 없으면 무한대."""
        r = self.route(start, goal)
        return math.inf if r is None else r.length

    # ── A* ────────────────────────────────────────────────────────

    def _search(
        self,
        start: NodeId,
        goal: NodeId,
        arrive_from: NodeId | None,
        edge_penalty: Mapping[EdgeKey, float] | None,
    ) -> Route | None:
        lot = self.lot
        if start not in lot.nodes or goal not in lot.nodes:
            return None

        goal_pos = lot.node_pos(goal)

        def h(node: NodeId) -> float:
            # 직선거리는 항상 실제 비용 이하다 (엣지 비용 ≥ 길이, 페널티 ≥ 0).
            # 따라서 허용적(admissible) 이며 A* 가 최적해를 보장한다.
            return lot.node_pos(node).distance_to(goal_pos)

        State = tuple[NodeId | None, NodeId]  # (직전 노드, 현재 노드)
        origin: State = (arrive_from, start)

        g: dict[State, float] = {origin: 0.0}
        dist: dict[State, float] = {origin: 0.0}
        turns: dict[State, int] = {origin: 0}
        came: dict[State, State] = {}

        heap: list[tuple[float, int, State]] = [(h(start), 0, origin)]
        tie = 0
        closed: set[State] = set()

        while heap:
            _f, _t, cur = heapq.heappop(heap)
            if cur in closed:
                continue
            closed.add(cur)

            prev, node = cur
            if node == goal:
                return self._reconstruct(came, cur, dist[cur], turns[cur], g[cur])

            for nxt, seg in lot.successors(node):
                step = seg
                turned = 0
                if prev is not None:
                    delta = self._heading_change(prev, node, nxt)
                    step += self.turn_cost.of(delta)
                    # 회전 수는 기하학적 사실이므로 페널티 가중치와 무관하게 센다.
                    # cost.py 가 이 값에 자기 가중치를 곱하므로, 여기서 가중치에
                    # 따라 개수가 달라지면 두 번 반영된다.
                    turned = 1 if abs(delta) > self.turn_cost.straight else 0
                if edge_penalty:
                    step += edge_penalty.get((node, nxt), 0.0)

                nxt_state: State = (node, nxt)
                cand = g[cur] + step
                if cand < g.get(nxt_state, math.inf):
                    g[nxt_state] = cand
                    dist[nxt_state] = dist[cur] + seg
                    turns[nxt_state] = turns[cur] + turned
                    came[nxt_state] = cur
                    tie += 1
                    heapq.heappush(heap, (cand + h(nxt), tie, nxt_state))

        return None

    def _heading_change(self, prev: NodeId, node: NodeId, nxt: NodeId) -> float:
        lot = self.lot
        a = (lot.node_pos(node) - lot.node_pos(prev)).angle
        b = (lot.node_pos(nxt) - lot.node_pos(node)).angle
        return angle_diff(b, a)

    def _reconstruct(
        self,
        came: dict[tuple[NodeId | None, NodeId], tuple[NodeId | None, NodeId]],
        end: tuple[NodeId | None, NodeId],
        length: float,
        turns: int,
        cost: float,
    ) -> Route:
        nodes: list[NodeId] = []
        cur = end
        while True:
            nodes.append(cur[1])
            if cur not in came:
                break
            cur = came[cur]
        nodes.reverse()
        polyline = tuple(self.lot.node_pos(n) for n in nodes)
        return Route(tuple(nodes), polyline, length, turns, cost)
