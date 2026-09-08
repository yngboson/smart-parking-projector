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

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from sim.common.geometry import Vec2, rect_corners
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
