"""경로 탐색 — 일방통행 준수와 회전 페널티.

유도선이 "실제로 주행 가능한 선"이 아니면 이 프로젝트 전체가 무의미하다.
그래서 여기서 검사하는 것은 알고리즘의 최적성이 아니라 **경로의 실현 가능성**이다.
"""

from __future__ import annotations

import math

import pytest

from sim.common.geometry import Vec2
from sim.common.ids import EdgeId, NodeId
from sim.common.lotmap import LaneEdge, LaneNode, LotMap
from sim.control.routing import LaneRouter, TurnCost
from sim.world.lot_builder import GridSpec, build_grid_lot


@pytest.fixture(scope="module")
def lot() -> LotMap:
    return build_grid_lot(GridSpec())


@pytest.fixture(scope="module")
def router(lot: LotMap) -> LaneRouter:
    return LaneRouter(lot)


def _edge_set(lot: LotMap) -> set[tuple[NodeId, NodeId]]:
    return {(e.src, e.dst) for e in lot.edges}


def test_every_step_of_a_route_is_a_real_edge(lot: LotMap, router: LaneRouter) -> None:
    """경로가 존재하지 않는 방향으로 건너뛰면 일방통행을 위반한 것이다."""
    edges = _edge_set(lot)
    entry = lot.entry_nodes[0]

    checked = 0
    for slot in list(lot.slots.values())[::7]:
        route = router.route(entry, slot.access_node)
        assert route is not None, f"{slot.id} 로 가는 길이 없습니다"
        for a, b in route.edges:
            assert (a, b) in edges, f"{a}→{b} 는 존재하지 않는 엣지입니다 (일방통행 위반)"
        checked += 1
    assert checked > 5


def test_route_reaches_every_slot_and_can_still_leave(lot: LotMap, router: LaneRouter) -> None:
    """모든 주차면이 입구에서 닿고, 거기서 출구까지 나갈 수 있어야 한다.

    통로 방향을 바꾸면 가장 먼저 깨지는 성질이다.
    """
    entry, exit_ = lot.entry_nodes[0], lot.exit_nodes[0]
    for sid, slot in lot.slots.items():
        assert router.route(entry, slot.access_node) is not None, f"{sid} 에 갈 수 없습니다"
        assert router.route(slot.access_node, exit_) is not None, f"{sid} 에서 나갈 수 없습니다"


def test_route_length_matches_the_polyline(lot: LotMap, router: LaneRouter) -> None:
    route = router.route(lot.entry_nodes[0], lot.exit_nodes[0])
    assert route is not None
    walked = sum(
        route.polyline[i].distance_to(route.polyline[i + 1])
        for i in range(len(route.polyline) - 1)
    )
    assert route.length == pytest.approx(walked, abs=1e-6)


def _open_grid(n: int = 4, step: float = 10.0) -> LotMap:
    """모든 방향으로 열린 격자. 같은 길이의 경로가 여러 개 존재한다.

    실제 도면은 일방통행이 많아 대안 경로가 거의 없다. 회전 페널티가 정말로
    작동하는지 보려면 **선택지가 있는** 그래프에서 시험해야 한다.
    """
    nodes, edges = {}, []
    for i in range(n):
        for j in range(n):
            nid = NodeId(f"{i}-{j}")
            nodes[nid] = LaneNode(nid, Vec2(i * step, j * step), "grid")
    for i in range(n):
        for j in range(n):
            for di, dj in ((1, 0), (0, 1)):
                a, b = NodeId(f"{i}-{j}"), NodeId(f"{i+di}-{j+dj}")
                if b in nodes:
                    edges.append(LaneEdge(EdgeId(f"{a}>{b}"), a, b, step))
                    edges.append(LaneEdge(EdgeId(f"{b}>{a}"), b, a, step))
    return LotMap(
        name="grid", slots={}, nodes=nodes, edges=edges, aisles=[], islands=[],
        entry_nodes=[NodeId("0-0")], exit_nodes=[NodeId(f"{n-1}-{n-1}")],
        pedestrian_gates=[], bounds=(0.0, 0.0, n * step, n * step),
    )


def test_turn_penalty_makes_the_router_prefer_a_straighter_path() -> None:
    """길이가 같은 경로가 여럿이면 회전이 적은 쪽을 골라야 한다.

    이게 안 되면 유도선이 통로를 지그재그로 가로지른다 — 거리는 같아도
    운전자가 따라가기 나쁘고 교차로마다 다른 차와 얽힌다.
    """
    grid = _open_grid()
    start, goal = NodeId("0-0"), NodeId("3-3")

    free = LaneRouter(grid, TurnCost(left=0.0, right=0.0, u_turn=0.0))
    strict = LaneRouter(grid, TurnCost(left=40.0, right=40.0, u_turn=200.0))

    a = free.route(start, goal)
    b = strict.route(start, goal)
    assert a is not None and b is not None

    assert b.length == pytest.approx(a.length), "최단거리는 그대로여야 한다"
    assert b.turns == 1, f"L 자 한 번이면 충분한데 {b.turns} 번 꺾었습니다"
    assert b.turns <= a.turns


def test_turn_count_does_not_depend_on_the_penalty_weights() -> None:
    """회전 수는 기하학적 사실이다. 가중치를 0 으로 둬도 개수는 그대로여야 한다.

    여기가 어긋나면 `cost.py` 의 회전 항이 조용히 사라진다.
    """
    grid = _open_grid()
    start, goal = NodeId("0-0"), NodeId("3-3")

    strict = LaneRouter(grid, TurnCost(left=40.0, right=40.0, u_turn=200.0))
    route = strict.route(start, goal)
    assert route is not None

    replay = LaneRouter(grid, TurnCost(left=0.0, right=0.0, u_turn=0.0))
    same = replay.route(start, goal)
    assert same is not None
    assert _geometric_turns(grid, route.nodes) == route.turns
    assert _geometric_turns(grid, same.nodes) == same.turns


def _geometric_turns(lot: LotMap, nodes: tuple[NodeId, ...]) -> int:
    from sim.common.geometry import angle_diff

    count = 0
    for i in range(len(nodes) - 2):
        a = (lot.node_pos(nodes[i + 1]) - lot.node_pos(nodes[i])).angle
        b = (lot.node_pos(nodes[i + 2]) - lot.node_pos(nodes[i + 1])).angle
        if abs(angle_diff(b, a)) > 0.35:
            count += 1
    return count


def test_left_turns_cost_more_than_right_turns() -> None:
    tc = TurnCost()
    assert tc.of(math.pi / 2) > tc.of(-math.pi / 2) > 0.0
    assert tc.of(0.0) == 0.0
    assert tc.of(0.2) == 0.0, "미세한 꺾임은 회전으로 세지 않는다"
    assert tc.of(math.pi) == tc.u_turn


def test_arriving_direction_changes_the_first_turn_cost(lot: LotMap, router: LaneRouter) -> None:
    """같은 지점이라도 어느 방향에서 들어왔는지에 따라 비용이 달라야 한다.

    이게 성립하지 않으면 재할당 경로가 '지금 차가 향한 방향'을 무시하게 된다.
    """
    node = lot.slots_in_row("C")[4].access_node
    goal = lot.exit_nodes[0]
    prev = next(
        (a for a, _l in _incoming(lot, node)), None
    )
    assert prev is not None

    blind = router.route(node, goal)
    aware = router.route(node, goal, arrive_from=prev)
    assert blind is not None and aware is not None
    assert aware.cost >= blind.cost, "진입 방향을 알면 첫 회전 비용이 추가된다"


def _incoming(lot: LotMap, node: NodeId):
    for e in lot.edges:
        if e.dst == node:
            yield e.src, e.length


def test_unreachable_goal_returns_none(lot: LotMap, router: LaneRouter) -> None:
    """입구 노드로는 아무도 되돌아갈 수 없다 — 램프는 들어오는 방향뿐이다."""
    assert router.route(lot.exit_nodes[0], lot.entry_nodes[0]) is None


def test_same_node_route_is_empty_but_valid(lot: LotMap, router: LaneRouter) -> None:
    n = lot.entry_nodes[0]
    route = router.route(n, n)
    assert route is not None
    assert route.length == 0.0 and route.turns == 0 and route.nodes == (n,)


def test_suffix_drops_the_part_already_driven(lot: LotMap, router: LaneRouter) -> None:
    route = router.route(lot.entry_nodes[0], lot.slots_in_row("A")[5].access_node)
    assert route is not None and len(route.nodes) > 3

    mid = route.nodes[2]
    tail = route.suffix_from(mid)
    assert tail is not None
    assert tail.nodes[0] == mid
    assert tail.goal == route.goal
    assert tail.length < route.length


def test_cached_and_uncached_queries_agree(lot: LotMap, router: LaneRouter) -> None:
    """혼잡 페널티가 0 이면 캐시된 결과와 같아야 한다 — 캐시가 정답을 바꾸면 안 된다."""
    entry = lot.entry_nodes[0]
    goal = lot.slots_in_row("D")[2].access_node
    cached = router.route(entry, goal)
    fresh = router.route(entry, goal, edge_penalty={("없는노드", "없는노드"): 5.0})
    assert cached is not None and fresh is not None
    assert cached.nodes == fresh.nodes


def test_congestion_penalty_pushes_the_route_elsewhere(lot: LotMap, router: LaneRouter) -> None:
    """혼잡한 엣지에 벌점을 주면 다른 길로 돌아가야 한다."""
    entry = lot.entry_nodes[0]
    goal = lot.slots_in_row("A")[9].access_node
    base = router.route(entry, goal)
    assert base is not None

    penalty = {edge: 500.0 for edge in base.edges[:3]}
    detour = router.route(entry, goal, edge_penalty=penalty)
    assert detour is not None
    assert detour.nodes != base.nodes, "벌점을 줬는데도 같은 길로 갑니다"
