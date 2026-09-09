"""격자형 주차장 도면 생성기.

좌표를 손으로 찍지 않는다. 파라미터로 규모를 바꿀 수 있게 해두고, 결과는
``layouts/*.json`` 으로 저장해 코드와 분리한다.

기본 도면 (mid_grid_120) 의 단면 — y 는 북쪽이 양수, 아래가 남쪽:

    y 83.25‥91.50   Row A   ← 건물 출입구에 가장 가까운 인기 자리
    y 69.25‥83.25   H0 통로 (일방통행 동쪽)
    y 61.00‥69.25   Row B
    y 52.75‥61.00   Row C
    y 38.75‥52.75   H1 통로 (일방통행 서쪽)
    y 30.50‥38.75   Row D
    y 22.25‥30.50   Row E
    y  8.25‥22.25   H2 통로 (일방통행 동쪽)
    y  0.00‥ 8.25   Row F
    y -6.00‥ 0.00   진출입 램프

수평 통로를 일방통행으로 엇갈리게 둔 것은 의도적이다. 통로 안에서 U턴이
불가능해야 "자리를 빼앗겨 되돌아가는 비용"이 실제로 발생하고, 복구 전략들의
차이가 드러난다.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

from sim.common.geometry import Vec2
from sim.common.ids import EdgeId, NodeId, SlotId, SlotType
from sim.common.lotmap import Aisle, Island, LaneEdge, LaneNode, LotMap, Slot

EAST = 0.0
WEST = math.pi
NORTH = math.pi / 2
SOUTH = -math.pi / 2


@dataclass(frozen=True)
class GridSpec:
    """격자 주차장의 치수. 전부 미터.

    ## 값 하나만 바꾸면 됩니다 — `aisle_width`

    통로 폭을 바꾸면 아래가 **전부 따라옵니다.** 따로 손볼 것이 없습니다.

    | 따라오는 것 | 어디에서 계산되나 |
    |---|---|
    | 주행·후진 속도, 예견 거리 | `agents.driving.DrivingSkill.for_lot` |
    | 우측통행 간격 | `agents.driver.KEEP_RIGHT_RATIO` |
    | 운전자의 횡방향 시야 | `agents.perception.VisionModel.for_lot` |
    | 후진 주차 접근 구간 길이 | `agents.driver.STAGING_APPROACH_RATIO` |
    | 교행 판정 폭 | `world.traffic.AisleTraffic` |
    | 이탈 판단 거리 | `agents.driver.TEMPTATION_LEAD_SECONDS` |

    주차면 크기(`slot_width` / `slot_length`)는 별개의 손잡이입니다. 차종이 실제로
    들어가는지는 `control.cost.slot_is_eligible` 이 검사하므로, 너무 줄이면
    큰 차가 배정받지 못합니다.

    ```bash
    # 치수를 바꾼 뒤에는 도면을 다시 만들어야 합니다
    python -m sim.world.lot_builder
    ```
    """

    name: str = "mid_grid_120"

    slot_width: float = 4.2
    slot_length: float = 8.25
    """주차면 한 칸 (m).

    **현실 규격이 아니라 사용자가 지정한 값이다** (D-019). 국내 법정 최소는
    2.5 × 5.0m, 확장형이 2.6 × 5.2m 이고, 여기 값은 그보다 훨씬 크다 —
    버스 전용 베이에 가깝다.

    현실성을 되찾으려면 2.8 × 5.5 로 돌리면 된다. 그 값이면 가장 큰 차종
    (밴 5.2 × 1.95m)도 앞뒤 0.15m · 좌우 0.42m 가 남는다.

    ⚠️ 이 값을 바꾸면 `layouts/mid_grid_120.json` 을 다시 만들어야 한다:
        python -m sim.world.lot_builder
    """

    aisle_width: float = 14.0
    """통로 폭 (m).

    법정 최소(직각주차 양방향 6.0m)의 두 배가 넘는다. 왕복 2차선 도로 폭이다.
    이것도 사용자 지정값이며 (D-019), 현실적인 값은 7.0m 다.

    통로가 넓어지면 우측통행 간격(`agents.driver.KEEP_RIGHT_RATIO`)과 운전자의
    횡방향 시야(`agents.perception.VisionModel.for_lot`)가 함께 따라간다.
    고정값으로 두면 통로만 넓어지고 차는 여전히 가운데로 몰린다.
    """

    island_width: float = 2.0

    slots_per_block: int = 10
    """블록(수직 통로 사이 구간) 하나에 들어가는 주차면 수."""

    blocks: int = 2
    """블록 개수. blocks+1 개의 수직 통로가 생긴다."""

    bands: int = 3
    """수평 통로 개수. 각 통로가 위아래 두 줄을 담당하므로 주차 행은 bands*2 개."""

    ramp_depth: float = 6.0
    """주차장 남쪽 진출입 램프의 깊이."""

    gate_offset: float = 4.0
    """건물 출입구가 주차장 북쪽 경계에서 얼마나 떨어져 있는가."""

    @property
    def total_slots(self) -> int:
        return self.slots_per_block * self.blocks * self.bands * 2


_ROW_LABELS = "ABCDEFGHIJKL"


def build_grid_lot(spec: GridSpec = GridSpec()) -> LotMap:
    """GridSpec 으로부터 완전한 LotMap 을 만든다."""

    # ── x 좌표 배치: [수직통로][섬][주차 블록][섬][수직통로]... ──────────────
    vertical_x: list[float] = []
    column_x: list[float] = []

    cursor = 0.0
    for b in range(spec.blocks):
        vertical_x.append(cursor + spec.aisle_width / 2.0)
        cursor += spec.aisle_width
        if b == 0:
            cursor += spec.island_width  # 블록 서쪽 끝 조경섬
        for i in range(spec.slots_per_block):
            column_x.append(cursor + spec.slot_width * (i + 0.5))
        cursor += spec.slot_width * spec.slots_per_block
        if b == spec.blocks - 1:
            cursor += spec.island_width  # 마지막 블록 동쪽 끝 조경섬
    vertical_x.append(cursor + spec.aisle_width / 2.0)
    cursor += spec.aisle_width
    lot_width = cursor

    # ── y 좌표 배치: 아래에서 위로 [행][통로][행][행][통로][행]... ──────────
    # 한 밴드 = 아래쪽 행 + 통로 + 위쪽 행
    band_height = spec.slot_length * 2 + spec.aisle_width
    lot_height = band_height * spec.bands

    aisle_y: list[float] = []
    row_specs: list[tuple[str, float, float]] = []  # (라벨, 중심 y, 진행방향 heading)

    for band in range(spec.bands):
        base = band * band_height
        y_aisle = base + spec.slot_length + spec.aisle_width / 2.0
        aisle_y.append(y_aisle)
        # 통로 아래 행: 차량 앞머리는 북쪽(통로 쪽)을 본다
        row_specs.append(("", base + spec.slot_length / 2.0, NORTH))
        # 통로 위 행: 앞머리는 남쪽(통로 쪽)
        row_specs.append(("", base + spec.slot_length * 1.5 + spec.aisle_width, SOUTH))

    # 라벨은 북쪽(출입구에 가까운 쪽)부터 A, B, C ... 순서로 붙인다.
    order = sorted(range(len(row_specs)), key=lambda i: -row_specs[i][1])
    labelled: dict[int, str] = {idx: _ROW_LABELS[k] for k, idx in enumerate(order)}
    row_specs = [(labelled[i], y, h) for i, (_, y, h) in enumerate(row_specs)]

    # ── 통로 노드 ───────────────────────────────────────────────────────
    x_positions = sorted(vertical_x + column_x)
    x_index = {round(x, 6): i for i, x in enumerate(x_positions)}

    nodes: dict[NodeId, LaneNode] = {}
    edges: list[LaneEdge] = []
    aisles: list[Aisle] = []

    def nid(band: int, i: int) -> NodeId:
        return NodeId(f"H{band}-{i:02d}")

    for band, y in enumerate(aisle_y):
        # 북쪽 통로부터 H0 이 되도록 번호를 뒤집는다 (라벨 A 와 방향을 맞춤)
        h = spec.bands - 1 - band
        for i, x in enumerate(x_positions):
            nodes[nid(h, i)] = LaneNode(nid(h, i), Vec2(x, y), f"H{h}")

        eastbound = h % 2 == 0
        for i in range(len(x_positions) - 1):
            a, b = nid(h, i), nid(h, i + 1)
            src, dst = (a, b) if eastbound else (b, a)
            length = abs(x_positions[i + 1] - x_positions[i])
            edges.append(LaneEdge(EdgeId(f"{src}>{dst}"), src, dst, length))

        aisles.append(
            Aisle(
                id=f"H{h}",
                axis="h",
                start=Vec2(0.0, y),
                end=Vec2(lot_width, y),
                width=spec.aisle_width,
                one_way=True,
                heading=EAST if eastbound else WEST,
            )
        )

    # 수직 통로: 수평 통로들을 양방향으로 잇는다
    band_order = sorted(range(spec.bands), key=lambda h: -h)  # 남 → 북
    for vx in vertical_x:
        i = x_index[round(vx, 6)]
        for a, b in zip(band_order, band_order[1:]):
            na, nb = nid(a, i), nid(b, i)
            length = abs(nodes[na].pos.y - nodes[nb].pos.y)
            edges.append(LaneEdge(EdgeId(f"{na}>{nb}"), na, nb, length))
            edges.append(LaneEdge(EdgeId(f"{nb}>{na}"), nb, na, length))
        aisles.append(
            Aisle(
                id=f"V{i:02d}",
                axis="v",
                start=Vec2(vx, nodes[nid(band_order[0], i)].pos.y),
                end=Vec2(vx, nodes[nid(band_order[-1], i)].pos.y),
                width=spec.aisle_width,
                one_way=False,
                heading=None,
            )
        )

    # ── 주차면 ─────────────────────────────────────────────────────────
    slots: dict[SlotId, Slot] = {}
    gate = Vec2(lot_width / 2.0, lot_height + spec.gate_offset)

    for row_i, (label, cy, heading) in enumerate(row_specs):
        band = row_i // 2
        h = spec.bands - 1 - band
        for idx, cx in enumerate(column_x, start=1):
            sid = SlotId(f"{label}-{idx:02d}")
            slots[sid] = Slot(
                id=sid,
                center=Vec2(cx, cy),
                heading=heading,
                length=spec.slot_length,
                width=spec.slot_width,
                slot_type=SlotType.STANDARD,
                access_node=nid(h, x_index[round(cx, 6)]),
                row=label,
                index=idx,
            )

    _assign_special_slots(slots, gate)

    # ── 조경섬: 각 주차 행의 동/서 양 끝 ────────────────────────────────
    islands: list[Island] = []
    west_x = column_x[0] - spec.slot_width / 2.0 - spec.island_width / 2.0
    east_x = column_x[-1] + spec.slot_width / 2.0 + spec.island_width / 2.0
    for row_i, (label, cy, _h) in enumerate(row_specs):
        for side, x in (("W", west_x), ("E", east_x)):
            islands.append(
                Island(
                    id=f"IS-{label}{side}",
                    center=Vec2(x, cy),
                    length=spec.slot_length,
                    width=spec.island_width,
                    kind="planting",
                )
            )

    # ── 진출입 램프 ────────────────────────────────────────────────────
    south = spec.bands - 1  # 가장 남쪽 수평 통로 번호
    entry_i = x_index[round(vertical_x[0], 6)]
    exit_i = x_index[round(vertical_x[-1], 6)]

    entry = NodeId("ENTRY")
    exit_ = NodeId("EXIT")
    ramp_y = -spec.ramp_depth / 2.0
    nodes[entry] = LaneNode(entry, Vec2(vertical_x[0], ramp_y), "RAMP")
    nodes[exit_] = LaneNode(exit_, Vec2(vertical_x[-1], ramp_y), "RAMP")

    n_in, n_out = nid(south, entry_i), nid(south, exit_i)
    edges.append(
        LaneEdge(
            EdgeId(f"{entry}>{n_in}"),
            entry,
            n_in,
            abs(nodes[n_in].pos.y - ramp_y),
        )
    )
    edges.append(
        LaneEdge(
            EdgeId(f"{n_out}>{exit_}"),
            n_out,
            exit_,
            abs(nodes[n_out].pos.y - ramp_y),
        )
    )
    aisles.append(
        Aisle("RAMP-IN", "v", Vec2(vertical_x[0], -spec.ramp_depth),
              nodes[n_in].pos, spec.aisle_width, True, NORTH)
    )
    aisles.append(
        Aisle("RAMP-OUT", "v", nodes[n_out].pos,
              Vec2(vertical_x[-1], -spec.ramp_depth), spec.aisle_width, True, SOUTH)
    )

    return LotMap(
        name=spec.name,
        slots=slots,
        nodes=nodes,
        edges=edges,
        aisles=aisles,
        islands=islands,
        entry_nodes=[entry],
        exit_nodes=[exit_],
        pedestrian_gates=[gate],
        bounds=(0.0, -spec.ramp_depth, lot_width, lot_height),
    )


def _assign_special_slots(slots: dict[SlotId, Slot], gate: Vec2) -> None:
    """건물 출입구에서 가까운 순으로 장애인 4면, 그 다음 EV 4면을 지정한다.

    실제 주차장이 그렇듯 인기 자리를 특수 목적으로 묶어두면, 일반 차량이 노릴 수 있는
    '좋은 자리'가 줄어들어 강탈 압력이 현실적으로 높아진다.
    """
    ranked = sorted(slots.values(), key=lambda s: s.center.distance_to(gate))
    for rank, slot in enumerate(ranked[:8]):
        kind = SlotType.DISABLED if rank < 4 else SlotType.EV
        slots[slot.id] = Slot(
            id=slot.id,
            center=slot.center,
            heading=slot.heading,
            length=slot.length,
            width=slot.width,
            slot_type=kind,
            access_node=slot.access_node,
            row=slot.row,
            index=slot.index,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="격자 주차장 도면 생성")
    ap.add_argument("--out", default="layouts/mid_grid_120.json")
    ap.add_argument("--name", default="mid_grid_120")
    ap.add_argument("--slots-per-block", type=int, default=10)
    ap.add_argument("--blocks", type=int, default=2)
    ap.add_argument("--bands", type=int, default=3)
    args = ap.parse_args()

    spec = GridSpec(
        name=args.name,
        slots_per_block=args.slots_per_block,
        blocks=args.blocks,
        bands=args.bands,
    )
    lot = build_grid_lot(spec)
    lot.save(args.out)

    w, h = lot.size
    print(f"{lot.name}: 주차면 {len(lot.slots)}면, 크기 {w:.1f} x {h:.1f} m")
    print(f"  통로 노드 {len(lot.nodes)}개 / 방향성 엣지 {len(lot.edges)}개")
    print(f"  구역 {', '.join(lot.rows)}")
    print(f"  저장: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
