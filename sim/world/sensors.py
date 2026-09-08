"""주차장에 설치된 센서들 — 관제가 세상을 아는 유일한 창구.

여기서 만들어지는 이벤트만이 `sim.control` 로 건너간다. 그래서 이 파일을 고칠 때
질문은 하나다:

    **실제 주차장에 이 장비를 설치할 수 있는가?**

아니라면 그 관측은 여기서 만들면 안 된다. 시뮬레이터는 차량의 정확한 위치와
속도를 알고 있지만, 그것을 이벤트에 담는 순간 알고리즘이 현장에서 못 쓰게 된다.

설치한 장비는 셋이다:

  입구/출구 ANPR    번호판을 읽는다. 차가 관측 영역에 처음 나타나면 입장,
                    사라지면 퇴장 — 실제 게이트 카메라가 하는 일 그대로다.
  주차면 점유 센서   눌렸는지 아닌지. `anpr` 모드면 구역 카메라가 번호판까지 읽고,
                    `presence` 모드면 점유 여부만 안다 (D-002).
  통로 루프 검지기   노드마다 묻혀 있다. 누가 지나갔는지만 알고 어디로 갈지는 모른다.

**노이즈는 넣지 않는다.** 완벽 인식을 가정한다 (docs/PLAN.md 제외 항목). 지금
측정하려는 것은 센서 성능이 아니라 복구 전략의 차이이므로, 센서 오차를 섞으면
원인을 분리할 수 없다.
"""

from __future__ import annotations

import math
from typing import Iterable, Protocol, Sequence

from sim.common.geometry import Vec2, angle_diff
from sim.common.ids import NodeId, PlateId, SlotId, VehicleClass
from sim.common.lotmap import LotMap
from sim.common.messages import (
    LaneDetection,
    SensorEvent,
    SlotOccupancyChanged,
    VehicleEntered,
    VehicleExited,
)
from sim.common.vehicle import SelfState, VehicleSpec, body_center

DETECTOR_RADIUS = 2.60
"""루프 검지기의 감지 반경(m). 통로 노드 간격보다 작아야 통과 순서가 뒤섞이지 않는다."""

SETTLED_SPEED = 0.20
"""이 속도 아래로 떨어져야 점유로 판정한다(m/s).

지나가거나 빠져나가는 중인 차를 주차로 오인하지 않기 위한 디바운스다.
실제 초음파/지자기 센서도 같은 이유로 확인 시간을 둔다.
"""

OCCUPANCY_LATERAL = 0.70
"""점유 판정의 횡방향 허용 오차(m).

주차면 폭이 2.5m 이므로 옆칸 중심은 2.5m 떨어져 있다. 이 값을 넉넉하게 두면
**옆칸에서 빠져나오는 차를 이 칸에 들어온 차로 오인**하고, 관제는 그것을 강탈로
판정한다 — 있지도 않은 강탈이 실험 결과에 섞인다.
"""

OCCUPANCY_LONGITUDINAL = 1.20
"""점유 판정의 길이 방향 허용 오차(m).

주차면 길이가 5m 이므로 반듯이 댄 차의 오차는 0.2m 수준이다. 이 값을 넉넉히
두면 주차면 입구에 걸쳐 있는 차 — 이제 막 빠져나가는 차 — 까지 점유로 잡힌다.
"""

HOLD_MARGIN = 1.00
"""점유를 **유지**할 때 쓰는 주차면 여유(m). 시작 판정보다 훨씬 넉넉하다.

이력현상(hysteresis)이 필요한 이유: 좁은 조건 하나로 시작과 해제를 모두 판정하면,
빠져나가려고 1m 전진한 차가 있는데도 센서가 자리를 비웠다고 보고한다. 관제는 그
자리를 다른 차에게 주고, 아직 그 자리에 있는 차가 다시 감지되는 순간 **자기 자리를
자기가 강탈한** 꼴이 된다. 실제로 그 오탐이 났다.

들어오는 판정은 까다롭게, 나가는 판정은 관대하게 — 실제 검지기의 동작이기도 하다.
"""

OCCUPANCY_DEBOUNCE = 0.60
"""같은 판정이 이만큼 이어져야 점유로 발행한다(초).

기하 조건만으로는 경계 사례가 남는다 — 빠져나가던 차가 잠깐 옆칸 안쪽을 스치면
관제가 **있지도 않은 강탈**을 보고한다. 강탈 횟수가 이 연구의 핵심 지표이므로
바닥 노이즈는 0 이어야 한다. 실제 초음파/지자기 센서도 같은 이유로 확인 시간을 둔다.
"""

OCCUPANCY_ALIGN = 0.44
"""주차면 방향과 이 정도(rad, 약 25°) 안으로 정렬돼야 주차로 본다.

통로를 가로질러 나가는 중인 차는 각도가 크게 어긋나 있어 걸러진다.
"""

_CELL = 6.0
"""공간 색인 격자 한 칸의 크기(m)."""


class VehicleView(Protocol):
    """센서가 관측할 수 있는 차량의 겉모습.

    프로토콜로 둔 이유: 센서가 `Simulation` 의 차량 객체를 직접 알면 두 모듈이
    서로를 import 하게 된다. 센서에게 필요한 것은 이 네 가지뿐이다.
    """

    plate: PlateId
    vehicle_class: VehicleClass
    spec: VehicleSpec
    state: SelfState


class SensorSuite:
    """설치된 센서 전부. 매 틱 한 번 `observe()` 를 호출한다."""

    def __init__(
        self,
        lot: LotMap,
        slot_sensor_mode: str = "anpr",
        detector_radius: float = DETECTOR_RADIUS,
    ) -> None:
        if slot_sensor_mode not in ("anpr", "presence"):
            raise ValueError(f"slot_sensor_mode 는 'anpr' 또는 'presence' (받은 값: {slot_sensor_mode!r})")
        self.lot = lot
        self.slot_sensor_mode = slot_sensor_mode
        self.detector_radius = detector_radius

        self._slot_grid = grid_index((sid, s.center) for sid, s in lot.slots.items())
        self._node_grid = grid_index((nid, n.pos) for nid, n in lot.nodes.items())

        self._present: dict[PlateId, VehicleClass] = {}
        self._occupancy: dict[SlotId, PlateId | None] = {}
        self._candidate: dict[SlotId, tuple[PlateId, float]] = {}
        self._last_node: dict[PlateId, NodeId] = {}

    # ── 관측 ──────────────────────────────────────────────────────

    def observe(self, t: float, vehicles: Sequence[VehicleView]) -> list[SensorEvent]:
        """이번 틱의 센서 관측 전부. 순서는 입장 → 점유 → 통로 → 퇴장."""
        events: list[SensorEvent] = []
        events.extend(self._gate_entries(t, vehicles))
        events.extend(self._slot_sensors(t, vehicles))
        events.extend(self._lane_detectors(t, vehicles))
        events.extend(self._gate_exits(t, vehicles))
        return events

    def prime(self, t: float, vehicles: Sequence[VehicleView]) -> list[SensorEvent]:
        """시작 상태를 확인 시간 없이 곧바로 관측으로 만든다.

        이미 주차돼 있는 차들은 방금 들어온 것이 아니라 **오래전부터 그 자리에**
        있었다. 확인 시간(`OCCUPANCY_DEBOUNCE`)을 기다리는 동안 관제가 그 자리를
        새로 도착한 차에게 배정하면, 있지도 않은 강탈이 보고된다 — 실제로 그랬다.

        시뮬레이션 시작 시 한 번만 부른다.
        """
        events: list[SensorEvent] = list(self._gate_entries(t, vehicles))
        settled_at = t - OCCUPANCY_DEBOUNCE - 1.0

        for v in vehicles:
            if v.state.speed > SETTLED_SPEED:
                continue
            sid = self._slot_under(v)
            if sid is None:
                continue
            self._candidate[sid] = (v.plate, settled_at)
            self._occupancy[sid] = v.plate
            events.append(
                SlotOccupancyChanged(
                    t=t,
                    slot_id=sid,
                    occupied=True,
                    plate=v.plate if self.slot_sensor_mode == "anpr" else None,
                )
            )
        return events

    # ── 입구/출구 ANPR ─────────────────────────────────────────────

    def _gate_entries(self, t: float, vehicles: Sequence[VehicleView]) -> list[SensorEvent]:
        out: list[SensorEvent] = []
        for v in vehicles:
            if v.plate not in self._present:
                self._present[v.plate] = v.vehicle_class
                out.append(VehicleEntered(t=t, plate=v.plate, vehicle_class=v.vehicle_class))
        return out

    def _gate_exits(self, t: float, vehicles: Sequence[VehicleView]) -> list[SensorEvent]:
        """관측 영역에서 사라진 차량 = 출구를 통과한 차량."""
        here = {v.plate for v in vehicles}
        gone = [p for p in self._present if p not in here]
        for p in gone:
            del self._present[p]
            self._last_node.pop(p, None)
        return [VehicleExited(t=t, plate=p) for p in sorted(gone)]

    # ── 주차면 점유 센서 ───────────────────────────────────────────

    def _slot_sensors(self, t: float, vehicles: Sequence[VehicleView]) -> list[SensorEvent]:
        settled: dict[SlotId, PlateId] = {}     # 반듯이 선 차 — 점유 **시작** 판정
        present: dict[SlotId, set[PlateId]] = {}  # 영역 안의 차 — 점유 **유지** 판정

        for v in vehicles:
            area = self._slot_area(v)
            if area is not None:
                present.setdefault(area, set()).add(v.plate)
            if v.state.speed <= SETTLED_SPEED:
                sid = self._slot_under(v)
                if sid is not None:
                    settled[sid] = v.plate

        out: list[SensorEvent] = []

        # 점유는 확인 시간을 둔다. 스쳐 지나간 차를 주차로 오인하면 관제가
        # 없던 강탈을 보고한다.
        for sid, plate in settled.items():
            since = self._candidate.get(sid)
            if since is None or since[0] != plate:
                self._candidate[sid] = (plate, t)
                continue
            if t - since[1] < OCCUPANCY_DEBOUNCE or self._occupancy.get(sid) == plate:
                continue
            self._occupancy[sid] = plate
            out.append(
                SlotOccupancyChanged(
                    t=t,
                    slot_id=sid,
                    occupied=True,
                    plate=plate if self.slot_sensor_mode == "anpr" else None,
                )
            )

        for sid in [s for s in self._candidate if s not in settled]:
            del self._candidate[sid]

        # 해제는 그 차가 주차면 영역을 **완전히 벗어났을 때**만.
        for sid, plate in list(self._occupancy.items()):
            if plate is None or plate in present.get(sid, ()):
                continue
            self._occupancy[sid] = None
            out.append(SlotOccupancyChanged(t=t, slot_id=sid, occupied=False, plate=None))
        return out

    def _slot_under(self, v: VehicleView) -> SlotId | None:
        """차체 중심이 어느 주차면 안에 있는가.

        네 꼭짓점이 모두 들어갔는지까지 보지는 않는다. 실제 점유 센서는 자기
        구획 위에 쇳덩이가 있는지만 알지, 반듯하게 댔는지는 모른다.
        """
        center = body_center(v.state.pose, v.spec)
        for sid in cells_around(self._slot_grid, center):
            slot = self.lot.slots[sid]
            if abs(angle_diff(v.state.pose.theta, slot.heading)) > OCCUPANCY_ALIGN:
                continue
            if inside_rect(
                center, slot.center, slot.heading,
                OCCUPANCY_LONGITUDINAL * 2.0, OCCUPANCY_LATERAL * 2.0,
            ):
                return sid
        return None

    def _slot_area(self, v: VehicleView) -> SlotId | None:
        """차체 중심이 어느 주차면 **영역** 안에 있는가. 자세는 보지 않는다.

        점유를 유지할지 판단하는 넉넉한 상자다. 빠져나가는 중이라 비뚤어져 있어도
        아직 그 자리를 쓰고 있는 것은 사실이다.
        """
        center = body_center(v.state.pose, v.spec)
        for sid in cells_around(self._slot_grid, center):
            slot = self.lot.slots[sid]
            if inside_rect(
                center, slot.center, slot.heading,
                slot.length + HOLD_MARGIN, slot.width + HOLD_MARGIN,
            ):
                return sid
        return None

    # ── 통로 루프 검지기 ───────────────────────────────────────────

    def _lane_detectors(self, t: float, vehicles: Sequence[VehicleView]) -> list[SensorEvent]:
        out: list[SensorEvent] = []
        for v in vehicles:
            node = self._node_under(v.state.pose.position)
            if node is None or self._last_node.get(v.plate) == node:
                continue
            self._last_node[v.plate] = node
            out.append(LaneDetection(t=t, node_id=node, plate=v.plate))
        return out

    def _node_under(self, p: Vec2) -> NodeId | None:
        best: NodeId | None = None
        best_d = self.detector_radius
        for nid in cells_around(self._node_grid, p):
            d = self.lot.node_pos(nid).distance_to(p)
            if d <= best_d:
                best, best_d = nid, d
        return best


# ── 공간 색인 (world 안에서 공용) ─────────────────────────────────────────────────────


def grid_index(items: Iterable[tuple[str, Vec2]]) -> dict[tuple[int, int], list]:
    """좌표를 격자 칸에 담아 근처 후보만 훑도록 한다.

    주차면 120개 × 차량 30대를 매 틱 전수 비교하면 시뮬레이션이 실시간을 못 따라간다.
    """
    grid: dict[tuple[int, int], list] = {}
    for key, pos in items:
        grid.setdefault(_cell_of(pos), []).append(key)
    return grid


def _cell_of(p: Vec2) -> tuple[int, int]:
    return (int(math.floor(p.x / _CELL)), int(math.floor(p.y / _CELL)))


def cells_around(grid: dict[tuple[int, int], list], p: Vec2):
    cx, cy = _cell_of(p)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for key in grid.get((cx + dx, cy + dy), ()):
                yield key


def inside_rect(
    p: Vec2, center: Vec2, heading: float, length: float, width: float
) -> bool:
    ct, st = math.cos(-heading), math.sin(-heading)
    dx, dy = p.x - center.x, p.y - center.y
    u = dx * ct - dy * st
    v = dx * st + dy * ct
    return abs(u) <= length / 2.0 and abs(v) <= width / 2.0
