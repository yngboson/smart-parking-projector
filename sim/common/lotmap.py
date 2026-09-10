"""주차장 도면 — 정적이고 불변인 데이터.

세 계층이 모두 이 도면을 읽는다. 다만 읽는 목적이 다르다:
    world   — 물리 충돌 판정과 센서 배치
    control — 경로 탐색과 주차면 비용 계산
    agents  — 운전자가 육안으로 볼 수 있는 범위 판정

도면은 코드가 아니라 ``layouts/*.json`` 데이터로 관리한다. 규모를 바꿀 때
코드를 고치지 않기 위해서다.

렌더링에 필요한 정보(통로 폴리곤, 구역 라벨, 연석/조경섬)도 함께 담는다.
뷰어가 도면을 다시 추측하지 않도록 하기 위해서다.
"""

from __future__ import annotations

import heapq
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from sim.common.geometry import Vec2, offset_polyline, rect_corners

TRAVEL_LANE_RATIO = 0.225
"""통로 폭 대비 주행 차선의 중심선 이격 비율. 폭 6m 에서 1.35m."""
from sim.common.ids import EdgeId, NodeId, SlotId, SlotType


@dataclass(frozen=True, slots=True)
class Slot:
    """주차면 하나."""

    id: SlotId
    center: Vec2
    heading: float
    """주차를 마친 차량의 앞머리가 향하는 방향(rad). 후진 주차이므로 통로 쪽을 본다."""

    length: float
    width: float
    slot_type: SlotType
    access_node: NodeId
    """이 주차면 바로 앞 통로 위의 노드. 경로 탐색의 종착점이다."""

    row: str
    """구역 라벨 (A~F). 바닥에 크게 도색하고, 구역 단위 안내에도 쓴다."""

    index: int
    """행 안에서의 순번 (1부터)."""

    def corners(self) -> list[Vec2]:
        """주차선 사각형의 네 꼭짓점."""
        return rect_corners(self.center, self.heading, self.length, self.width)

    @property
    def entry_point(self) -> Vec2:
        """통로에서 이 주차면으로 진입을 시작하는 지점 (주차면 입구 중앙)."""
        return self.center + Vec2.from_angle(self.heading) * (self.length / 2.0)


@dataclass(frozen=True, slots=True)
class LaneNode:
    """통로 위의 한 점. 통로 검지기가 여기에 묻혀 있다고 가정한다."""

    id: NodeId
    pos: Vec2
    aisle: str


@dataclass(frozen=True, slots=True)
class LaneEdge:
    """통로 구간. 일방통행이면 반대 방향 엣지가 아예 존재하지 않는다.

    즉 일방통행 위반은 알고리즘이 지켜야 할 규칙이 아니라 그래프상 불가능한 일이다.
    """

    id: EdgeId
    src: NodeId
    dst: NodeId
    length: float


@dataclass(frozen=True, slots=True)
class Aisle:
    """통로 한 줄. 아스팔트 도색과 진행 방향 화살표를 그리기 위한 렌더링용 정보."""

    id: str
    axis: str
    """h = 동서 방향, v = 남북 방향."""

    start: Vec2
    end: Vec2
    width: float
    one_way: bool
    heading: float | None
    """일방통행일 때 진행 방향(rad). 양방향이면 None."""


@dataclass(frozen=True, slots=True)
class Island:
    """연석으로 둘러싼 조경섬 / 안전지대. 시각적 요소이자 통행 불가 영역."""

    id: str
    center: Vec2
    length: float
    width: float
    kind: str = "planting"


@dataclass(frozen=True)
class LotMap:
    """주차장 전체 도면."""

    name: str
    slots: Mapping[SlotId, Slot]
    nodes: Mapping[NodeId, LaneNode]
    edges: Sequence[LaneEdge]
    aisles: Sequence[Aisle]
    islands: Sequence[Island]
    entry_nodes: Sequence[NodeId]
    exit_nodes: Sequence[NodeId]
    pedestrian_gates: Sequence[Vec2]
    """건물 출입구. 여기서 가까운 주차면이 인기 자리이며, 강탈이 집중되는 곳이다."""

    bounds: tuple[float, float, float, float]
    """(min_x, min_y, max_x, max_y) — 아스팔트 포장 영역."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "_succ", self._build_successors())
        object.__setattr__(self, "_walk", self._build_walk_distances())

    # ── 그래프 조회 ────────────────────────────────────────────────

    def _build_successors(self) -> dict[NodeId, tuple[tuple[NodeId, float], ...]]:
        acc: dict[NodeId, list[tuple[NodeId, float]]] = {n: [] for n in self.nodes}
        for e in self.edges:
            acc[e.src].append((e.dst, e.length))
        return {k: tuple(v) for k, v in acc.items()}

    def successors(self, node: NodeId) -> tuple[tuple[NodeId, float], ...]:
        """(다음 노드, 이동 거리) 목록. 일방통행은 여기서 이미 반영되어 있다."""
        return getattr(self, "_succ")[node]

    def node_pos(self, node: NodeId) -> Vec2:
        return self.nodes[node].pos

    @property
    def travel_lane_offset(self) -> float:
        """통로 중심선에서 주행 차선까지의 거리(m). 진행 방향 오른쪽.

        **도로의 성질이므로 도면이 답한다.** 관제는 이 값으로 바닥에 유도선을 그리고,
        운전자는 안내가 없을 때 이 값으로 자기 차선을 잡는다. 둘이 다르면 바닥의
        선과 차가 가는 길이 어긋난다.

        폭에 비례시키는 이유: 넓은 통로에서 중앙선만 따라 달리면 마주 오는 차와
        정면으로 만난다 (docs/DECISIONS.md D-012). 폭 6m 에서 1.35m —
        교착을 없앤 값이 그것이었다.
        """
        widths = [a.width for a in self.aisles]
        return (min(widths) if widths else 6.0) * TRAVEL_LANE_RATIO

    def shortest_path(self, src: NodeId, dst: NodeId) -> list[NodeId] | None:
        """거리만 보는 최단 경로. 길이 없으면 None.

        도면 자체가 가진 성질이므로 여기 둔다. 관제의 경로 탐색
        (`sim.control.routing`) 은 이것과 다른 물건이다 — 저쪽은 회전 페널티와
        혼잡 비용을 얹어 "어느 길로 안내할까"를 정한다.

        이것은 **길이 있는가**를 묻는 용도다. 운전자가 출구를 찾아 나가는 것처럼
        정책이 개입하지 않는 이동, 그리고 도면 연결성 검사에 쓴다.
        """
        if src not in self.nodes or dst not in self.nodes:
            return None
        if src == dst:
            return [src]

        dist: dict[NodeId, float] = {src: 0.0}
        came: dict[NodeId, NodeId] = {}
        heap: list[tuple[float, int, NodeId]] = [(0.0, 0, src)]
        tie = 0
        seen: set[NodeId] = set()

        while heap:
            d, _t, node = heapq.heappop(heap)
            if node in seen:
                continue
            seen.add(node)
            if node == dst:
                path = [node]
                while path[-1] != src:
                    path.append(came[path[-1]])
                path.reverse()
                return path
            for nxt, length in self.successors(node):
                nd = d + length
                if nd < dist.get(nxt, float("inf")):
                    dist[nxt] = nd
                    came[nxt] = node
                    tie += 1
                    heapq.heappush(heap, (nd, tie, nxt))
        return None

    # ── 도보 거리 ──────────────────────────────────────────────────

    def _build_walk_distances(self) -> dict[SlotId, float]:
        if not self.pedestrian_gates:
            return {sid: 0.0 for sid in self.slots}
        return {
            sid: min(s.center.distance_to(g) for g in self.pedestrian_gates)
            for sid, s in self.slots.items()
        }

    def walk_distance(self, slot: SlotId) -> float:
        """주차면에서 가장 가까운 건물 출입구까지의 직선 도보 거리."""
        return getattr(self, "_walk")[slot]

    def road_capacity(self, vehicle_length: float = 4.7, headway: float = 2.0) -> int:
        """통로가 물리적으로 담을 수 있는 **주행 차량** 수.

        통로 총 길이를 (차량 길이 + 안전 차간)으로 나눈다. 양방향 통로는 두 줄이
        오가므로 두 배로 센다.

        **왜 도면에서 계산하는가.** "주차장이 포화됐다"가 상수를 재는 말이 되면 안
        되기 때문이다. 지금까지 입구를 막던 값은 `max_guided` = 20 이었고, 그것은
        **유도선 색 구분의 한계**에서 나온 숫자다 (D-006). 무안내 모드에는 유도선이
        없으므로 그 제약이 걸릴 이유가 없는데도 똑같이 걸리고 있었다 — 그러면
        "무안내가 처리량이 낮다"가 물리적 혼잡 때문인지 색 팔레트 때문인지
        구분되지 않는다 (docs/ALLOCATION_MODEL.md 7절, D-030).

        통로 폭을 바꾸면 이 값도 따라 움직인다 (D-019 의 손잡이 하나 원칙).
        """
        spacing = vehicle_length + headway
        total = 0.0
        for a in self.aisles:
            lanes = 1 if a.one_way else 2
            total += a.start.distance_to(a.end) * lanes
        return max(1, int(total / spacing))

    # ── 편의 ──────────────────────────────────────────────────────

    def slots_in_row(self, row: str) -> list[Slot]:
        return sorted(
            (s for s in self.slots.values() if s.row == row), key=lambda s: s.index
        )

    @property
    def rows(self) -> list[str]:
        return sorted({s.row for s in self.slots.values()})

    @property
    def size(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bounds
        return (x1 - x0, y1 - y0)

    # ── 직렬화 ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "bounds": list(self.bounds),
            "slots": [
                {
                    "id": s.id,
                    "center": [s.center.x, s.center.y],
                    "heading": s.heading,
                    "length": s.length,
                    "width": s.width,
                    "type": s.slot_type.value,
                    "access_node": s.access_node,
                    "row": s.row,
                    "index": s.index,
                }
                for s in self.slots.values()
            ],
            "nodes": [
                {"id": n.id, "pos": [n.pos.x, n.pos.y], "aisle": n.aisle}
                for n in self.nodes.values()
            ],
            "edges": [
                {"id": e.id, "src": e.src, "dst": e.dst, "length": e.length}
                for e in self.edges
            ],
            "aisles": [
                {
                    "id": a.id,
                    "axis": a.axis,
                    "start": [a.start.x, a.start.y],
                    "end": [a.end.x, a.end.y],
                    "width": a.width,
                    "one_way": a.one_way,
                    "heading": a.heading,
                }
                for a in self.aisles
            ],
            "islands": [
                {
                    "id": i.id,
                    "center": [i.center.x, i.center.y],
                    "length": i.length,
                    "width": i.width,
                    "kind": i.kind,
                }
                for i in self.islands
            ],
            "entry_nodes": list(self.entry_nodes),
            "exit_nodes": list(self.exit_nodes),
            "pedestrian_gates": [[g.x, g.y] for g in self.pedestrian_gates],
        }

    @staticmethod
    def from_dict(d: dict) -> "LotMap":
        slots = {
            SlotId(s["id"]): Slot(
                id=SlotId(s["id"]),
                center=Vec2(*s["center"]),
                heading=s["heading"],
                length=s["length"],
                width=s["width"],
                slot_type=SlotType(s["type"]),
                access_node=NodeId(s["access_node"]),
                row=s["row"],
                index=s["index"],
            )
            for s in d["slots"]
        }
        nodes = {
            NodeId(n["id"]): LaneNode(NodeId(n["id"]), Vec2(*n["pos"]), n["aisle"])
            for n in d["nodes"]
        }
        edges = [
            LaneEdge(EdgeId(e["id"]), NodeId(e["src"]), NodeId(e["dst"]), e["length"])
            for e in d["edges"]
        ]
        aisles = [
            Aisle(
                a["id"],
                a["axis"],
                Vec2(*a["start"]),
                Vec2(*a["end"]),
                a["width"],
                a["one_way"],
                a["heading"],
            )
            for a in d.get("aisles", [])
        ]
        islands = [
            Island(i["id"], Vec2(*i["center"]), i["length"], i["width"], i["kind"])
            for i in d.get("islands", [])
        ]
        return LotMap(
            name=d["name"],
            slots=slots,
            nodes=nodes,
            edges=edges,
            aisles=aisles,
            islands=islands,
            entry_nodes=[NodeId(x) for x in d["entry_nodes"]],
            exit_nodes=[NodeId(x) for x in d["exit_nodes"]],
            pedestrian_gates=[Vec2(*g) for g in d["pedestrian_gates"]],
            bounds=tuple(d["bounds"]),  # type: ignore[arg-type]
        )

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )

    @staticmethod
    def load(path: str | Path) -> "LotMap":
        return LotMap.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
