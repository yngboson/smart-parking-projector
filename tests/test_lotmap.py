"""주차장 도면과 통로 그래프의 무결성 검사.

여기서 잡아야 하는 것은 "경로가 존재하지 않는 주차면" 같은 도면 자체의 결함이다.
이런 결함은 나중에 할당 알고리즘 버그로 오인되기 쉬우므로 도면 단계에서 막는다.
"""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path

import pytest

from sim.common.geometry import Vec2
from sim.common.ids import NodeId, SlotType
from sim.common.lotmap import LotMap
from sim.world.lot_builder import GridSpec, build_grid_lot

REPO = Path(__file__).resolve().parents[1]
LAYOUT = REPO / "layouts" / "mid_grid_120.json"


@pytest.fixture(scope="module")
def lot() -> LotMap:
    return build_grid_lot(GridSpec())


def _reachable_from(lot: LotMap, start: NodeId) -> set[NodeId]:
    seen = {start}
    q = deque([start])
    while q:
        n = q.popleft()
        for nxt, _ in lot.successors(n):
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return seen


def _reaching(lot: LotMap, target: NodeId) -> set[NodeId]:
    """target 에 도달할 수 있는 노드 집합 (역방향 탐색)."""
    rev: dict[NodeId, list[NodeId]] = {n: [] for n in lot.nodes}
    for e in lot.edges:
        rev[e.dst].append(e.src)
    seen = {target}
    q = deque([target])
    while q:
        n = q.popleft()
        for prev in rev[n]:
            if prev not in seen:
                seen.add(prev)
                q.append(prev)
    return seen


def test_slot_count_and_labels(lot: LotMap) -> None:
    assert len(lot.slots) == 120
    assert lot.rows == ["A", "B", "C", "D", "E", "F"]
    for row in lot.rows:
        assert len(lot.slots_in_row(row)) == 20


def test_every_slot_is_reachable_from_the_entrance(lot: LotMap) -> None:
    """입구에서 모든 주차면 앞까지 갈 수 있어야 한다.

    일방통행을 엇갈리게 두면 도달 불가능한 구역이 생기기 쉽다.
    """
    reachable = _reachable_from(lot, lot.entry_nodes[0])
    unreachable = sorted(
        s.id for s in lot.slots.values() if s.access_node not in reachable
    )
    assert not unreachable, f"입구에서 도달할 수 없는 주차면: {unreachable}"


def test_every_slot_can_reach_the_exit(lot: LotMap) -> None:
    """주차 후 출차할 수 있어야 한다. 도착만 되고 나갈 수 없으면 도면 결함이다."""
    reaching = _reaching(lot, lot.exit_nodes[0])
    stuck = sorted(s.id for s in lot.slots.values() if s.access_node not in reaching)
    assert not stuck, f"출구로 나갈 수 없는 주차면: {stuck}"


def test_horizontal_aisles_are_one_way_and_alternate(lot: LotMap) -> None:
    """수평 통로는 일방통행이고 방향이 번갈아야 한다.

    이래야 통로 안에서 U턴이 불가능해지고, 자리를 빼앗겼을 때 되돌아가는 비용이
    실제로 발생한다 (복구 전략을 비교하는 의미가 여기서 생긴다).
    """
    horizontal = [a for a in lot.aisles if a.axis == "h"]
    assert len(horizontal) == 3
    headings = [a.heading for a in sorted(horizontal, key=lambda a: a.id)]
    assert all(a.one_way for a in horizontal)
    for a, b in zip(headings, headings[1:]):
        assert a is not None and b is not None
        assert abs(abs(a - b) - math.pi) < 1e-9, "인접한 수평 통로의 방향이 같습니다"


def test_no_reverse_edge_exists_on_one_way_aisles(lot: LotMap) -> None:
    """일방통행은 규칙이 아니라 그래프상 불가능해야 한다."""
    pairs = {(e.src, e.dst) for e in lot.edges}
    h_nodes = {n.id: n for n in lot.nodes.values() if n.aisle.startswith("H")}
    for src, dst in pairs:
        if src in h_nodes and dst in h_nodes:
            same_aisle = h_nodes[src].aisle == h_nodes[dst].aisle
            if same_aisle:
                assert (dst, src) not in pairs, (
                    f"일방통행 통로 {h_nodes[src].aisle} 에 역방향 엣지가 있습니다: {src}<->{dst}"
                )


def test_slot_access_node_is_adjacent_to_the_slot(lot: LotMap) -> None:
    """주차면의 진입 지점과 배정된 통로 노드가 실제로 붙어 있어야 한다."""
    for slot in lot.slots.values():
        node = lot.node_pos(slot.access_node)
        assert abs(node.x - slot.center.x) < 1e-6, f"{slot.id}: 통로 노드가 x축으로 어긋남"
        gap = abs(node.y - slot.entry_point.y)
        assert gap <= 3.1, f"{slot.id}: 통로 노드가 {gap:.1f}m 떨어져 있음"


def test_parked_cars_face_the_aisle(lot: LotMap) -> None:
    """후진 주차이므로 주차를 마친 차량의 앞머리는 통로를 향해야 한다."""
    for slot in lot.slots.values():
        node = lot.node_pos(slot.access_node)
        to_aisle = (node - slot.center).normalized()
        facing = Vec2.from_angle(slot.heading)
        assert to_aisle.dot(facing) > 0.99, f"{slot.id}: 차량이 통로 반대쪽을 봄"


def test_popular_slots_are_near_the_pedestrian_gate(lot: LotMap) -> None:
    """A 구역이 건물 출입구에 가장 가까워야 한다 (강탈이 집중될 구역)."""
    by_row = {
        row: min(lot.walk_distance(s.id) for s in lot.slots_in_row(row))
        for row in lot.rows
    }
    ordered = [by_row[r] for r in lot.rows]
    assert ordered == sorted(ordered), f"구역 라벨과 도보 거리 순서가 어긋남: {by_row}"


def test_special_slots_are_the_closest_ones(lot: LotMap) -> None:
    special = [s for s in lot.slots.values() if s.slot_type is not SlotType.STANDARD]
    assert len(special) == 8
    assert sum(1 for s in special if s.slot_type is SlotType.DISABLED) == 4
    assert sum(1 for s in special if s.slot_type is SlotType.EV) == 4

    worst_special = max(lot.walk_distance(s.id) for s in special)
    best_standard = min(
        lot.walk_distance(s.id)
        for s in lot.slots.values()
        if s.slot_type is SlotType.STANDARD
    )
    assert worst_special <= best_standard + 1e-9


def test_slots_do_not_overlap_the_aisles(lot: LotMap) -> None:
    """주차면이 통로를 침범하면 차량이 통로에 걸쳐 서게 된다."""
    for aisle in lot.aisles:
        if aisle.axis != "h":
            continue
        y0 = aisle.start.y - aisle.width / 2.0
        y1 = aisle.start.y + aisle.width / 2.0
        for slot in lot.slots.values():
            top = slot.center.y + slot.length / 2.0
            bottom = slot.center.y - slot.length / 2.0
            assert bottom >= y1 - 1e-9 or top <= y0 + 1e-9, (
                f"{slot.id} 가 통로 {aisle.id} 를 침범합니다"
            )


def test_scaling_the_spec_changes_the_lot_size() -> None:
    """도면은 데이터다 — 코드를 고치지 않고 규모를 바꿀 수 있어야 한다."""
    small = build_grid_lot(GridSpec(name="small", slots_per_block=5, blocks=1, bands=2))
    assert len(small.slots) == 5 * 1 * 2 * 2 == 20
    assert small.rows == ["A", "B", "C", "D"]

    large = build_grid_lot(GridSpec(name="large", slots_per_block=15, blocks=3, bands=4))
    assert len(large.slots) == 15 * 3 * 4 * 2 == 360


def test_roundtrip_through_json(tmp_path: Path, lot: LotMap) -> None:
    p = tmp_path / "lot.json"
    lot.save(p)
    back = LotMap.load(p)

    assert back.name == lot.name
    assert len(back.slots) == len(lot.slots)
    assert len(back.edges) == len(lot.edges)
    assert len(back.islands) == len(lot.islands)
    assert back.bounds == lot.bounds
    sid = next(iter(lot.slots))
    assert back.slots[sid] == lot.slots[sid]
    assert back.walk_distance(sid) == pytest.approx(lot.walk_distance(sid))


def test_committed_layout_matches_the_generator(lot: LotMap) -> None:
    """layouts/*.json 이 생성기와 어긋난 채 방치되지 않도록 한다."""
    assert LAYOUT.exists(), f"{LAYOUT} 가 없습니다. python -m sim.world.lot_builder 를 실행하세요."
    committed = LotMap.load(LAYOUT)
    assert committed.to_dict() == lot.to_dict(), (
        "커밋된 도면이 생성기 출력과 다릅니다. "
        "python -m sim.world.lot_builder 로 다시 생성하세요."
    )
