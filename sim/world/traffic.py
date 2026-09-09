"""통로 합류 통제 — 주차면에서 나오는 차의 양보 (docs/DECISIONS.md D-012).

**교착의 정체를 찾기까지 세 번 틀렸다. 그 기록을 남긴다.**

1. *양방향 수직 통로의 정면 교행일 것이다* — 아니었다. 통로 폭이 6m 라 두 대가
   각자 오른쪽으로 붙으면 그냥 스쳐 간다. 모두가 중앙선 위를 달린 것이 문제였고,
   `agents.driver.KEEP_RIGHT` 로 해결됐다.

2. *옆자리 주차 차량이 진로를 막는 것이다* — 절반만 맞았다. 주차면에서 나오려는
   차가 **옆칸에 세워진 차**를 자기 진로의 장애물로 읽고 스스로 갇혔다.
   통로가 도로이고 주차면은 도로 밖이므로, 주차면 안의 차는 통로 장애물로 세지
   않는다 (`Simulation._clearance`). 이걸 고치자 저밀도 정체가 사라졌다.

3. **진짜 교착은 합류점의 상호 봉쇄였다.** 주차면에서 나오던 차와 통로를 달리던
   차가 **동시에** 합류점에 진입해 서로의 앞을 막는다. 둘 다 상대가 비켜야 갈 수
   있고, 상대도 같은 처지다. 한 번 얽히면 그 통로 전체가 굳는다.

그래서 규칙은 **간격 수용(gap acceptance)** 이다:

    통로에서 곧 도착할 차가 있으면, 주차면 밖으로 코를 내밀지 않는다.

"곧 도착할"이 핵심이다. 처음 구현은 합류점 근처에 **멈춰 있는** 차에게까지
양보하게 만들었고, 그래서 통로가 한 번 막히면 아무도 못 나와 정체가 스스로를
키웠다 — 정지 비율이 5% 에서 64% 로 뛰었다. 멈춰 있는 차는 나를 칠 수 없다.

**왜 world 에 있는가.** 합류 우선순위는 통로의 성질이지 관제 알고리즘의 판단이
아니다. 관제(`sim.control`)에 넣으면 복구 전략마다 교통 규칙이 달라져
"복구 전략의 차이"와 "교통 규칙의 차이"가 섞이고, 6종 비교(D-009)가 무의미해진다.
모든 전략이 같은 도로 조건 위에서 겨뤄야 한다.

**월드가 차를 세우지는 않는다.** `Perception.stop_distance` 로 "여기서 멈춰야
한다"를 알려줄 뿐이고, 실제로 서는 것은 운전자다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from sim.common.geometry import Vec2, angle_diff
from sim.common.ids import PlateId, SlotId
from sim.common.lotmap import LotMap, Slot
from sim.common.vehicle import body_center
from sim.world.sensors import VehicleView, cells_around, grid_index, inside_rect

REQUIRED_GAP = 3.5
"""통로 차량이 합류점에 도착하기까지 이만큼은 남아 있어야 나간다(초)."""

MERGE_WINDOW = 16.0
"""합류점에서 이 거리 밖의 차는 신경 쓰지 않는다(m)."""

BLOCKED_RADIUS = 4.5
"""합류점이 물리적으로 점유된 것으로 보는 거리(m). 상대가 멈춰 있어도 못 나간다."""

MOVING_SPEED = 0.40
"""이보다 느린 차는 나를 칠 수 없다. 정체된 차에게 양보하면 정체가 영원해진다."""

SLOT_MARGIN = 0.40
"""주차면 안에 있다고 볼 때의 여유(m)."""

ALIGNED_WITH_SLOT = 1.05
"""주차면과 이 각도(rad, 약 60°) 안으로 정렬돼야 '그 자리를 쓰는 중'으로 본다.

주차면이 커지면 그 사각형이 통로 가까이까지 뻗는다. 자세를 보지 않으면 **통로를
가로질러 지나가는 차**까지 '주차면 안'으로 잡히고, 그러면 그 차가 다른 차의
장애물 계산에서 빠져 버린다 — 뒤차가 그대로 밀고 들어간다.

들어가거나 나오는 차는 주차면과 나란하고, 지나가는 차는 직각이다.
"""

FACING_AISLE = 0.50
"""통로 쪽을 향하고 있다고 볼 최소 내적값 (약 60°)."""

PATIENCE = 12.0
"""이만큼 기다렸으면 비집고 나간다(초).

안전망이다. 없으면 붐비는 통로에 접한 차량이 영원히 못 나온다. 실제 운전자도
언젠가는 나가고, 그때 통로가 잠깐 막히는 것 또한 현실이다.
"""


@dataclass(slots=True)
class _Waiting:
    since: float
    released: bool = False


class AisleTraffic:
    """주차면 → 통로 합류의 양보 규칙."""

    def __init__(self, lot: LotMap) -> None:
        self.lot = lot
        self._slot_grid = grid_index((sid, s.center) for sid, s in lot.slots.items())

        self.in_slot: dict[PlateId, SlotId] = {}
        """지금 주차면 안에 있는 차량들. 이들은 통로의 장애물이 아니다."""

        self.forced_merges = 0
        """참다못해 나간 횟수. 통로가 얼마나 막혀 있었는지를 보여주는 지표다."""

        self._waiting: dict[PlateId, _Waiting] = {}
        self._stop: dict[PlateId, float] = {}
        self._cache: dict[PlateId, tuple[int, SlotId | None]] = {}
        """번호판 → (자세 버전, 주차면). 세워둔 차는 다시 조회하지 않는다."""

    # ── 매 틱 ─────────────────────────────────────────────────────

    def update(self, t: float, vehicles: Sequence[VehicleView]) -> None:
        self._stop = {}
        self.in_slot = {}
        live = set()
        for v in vehicles:
            live.add(v.plate)
            hit = self._cache.get(v.plate)
            if hit is not None and hit[0] == v.version:
                sid = hit[1]
            else:
                sid = self._slot_of(v)
                self._cache[v.plate] = (v.version, sid)
            if sid is not None:
                self.in_slot[v.plate] = sid
        for gone in [p for p in self._cache if p not in live]:
            del self._cache[gone]

        on_aisle = [v for v in vehicles if v.plate not in self.in_slot]
        for v in vehicles:
            sid = self.in_slot.get(v.plate)
            if sid is None:
                continue
            slot = self.lot.slots[sid]
            if self._is_heading_out(v, slot):
                self._resolve(t, v, slot, on_aisle)
            else:
                self._waiting.pop(v.plate, None)

        for plate in [p for p in self._waiting if p not in self.in_slot]:
            del self._waiting[plate]

    def stop_distance(self, vehicle: VehicleView) -> float:
        """이 차가 멈춰야 하는 지점까지의 거리. 나가도 되면 무한대."""
        return self._stop.get(vehicle.plate, math.inf)

    # ── 판단 ──────────────────────────────────────────────────────

    def _resolve(
        self, t: float, v: VehicleView, slot: Slot, on_aisle: Sequence[VehicleView]
    ) -> None:
        merge = self.lot.node_pos(slot.access_node)
        if not self._conflict(merge, on_aisle):
            self._waiting.pop(v.plate, None)
            return

        wait = self._waiting.setdefault(v.plate, _Waiting(since=t))
        if wait.released:
            return
        if t - wait.since >= PATIENCE:
            wait.released = True
            self.forced_merges += 1
            return

        # 주차면 입구에서 멈춘다 — 통로로 코를 내밀지 않는 자리다.
        self._stop[v.plate] = _bumper_gap(v, slot.entry_point)

    def _conflict(self, merge: Vec2, on_aisle: Sequence[VehicleView]) -> bool:
        """지금 나가면 통로 차량과 부딪히는가."""
        for other in on_aisle:
            d = merge.distance_to(other.state.pose.position)
            if d > MERGE_WINDOW:
                continue
            if d <= BLOCKED_RADIUS:
                return True  # 합류점이 물리적으로 막혀 있다

            speed = other.state.speed
            if speed < MOVING_SPEED:
                continue  # 멈춰 있는 차는 나를 칠 수 없다
            approaching = (merge - other.state.pose.position).normalized()
            if approaching.dot(other.state.pose.forward) < 0.5:
                continue  # 이미 지나갔거나 다른 데로 간다
            if d / speed < REQUIRED_GAP:
                return True
        return False

    def _is_heading_out(self, v: VehicleView, slot: Slot) -> bool:
        """통로 쪽으로 나가려는 자세인가. 후진 주차 중인 차는 해당되지 않는다."""
        if v.state.gear < 0:
            return False
        to_aisle = (self.lot.node_pos(slot.access_node) - slot.center).normalized()
        return v.state.pose.forward.dot(to_aisle) >= FACING_AISLE

    def _slot_of(self, v: VehicleView) -> SlotId | None:
        center = v.center
        for sid in cells_around(self._slot_grid, center):
            slot = self.lot.slots[sid]
            if abs(angle_diff(v.state.pose.theta, slot.heading)) > ALIGNED_WITH_SLOT:
                continue
            if inside_rect(
                center, slot.center, slot.heading,
                slot.length + SLOT_MARGIN, slot.width + SLOT_MARGIN,
            ):
                return sid
        return None


def _bumper_gap(v: VehicleView, target: Vec2) -> float:
    """앞범퍼에서 그 지점까지 남은 거리."""
    d = target - v.state.pose.position
    along = d.x * math.cos(v.state.pose.theta) + d.y * math.sin(v.state.pose.theta)
    return max(0.0, along - (v.spec.length - v.spec.rear_overhang))
