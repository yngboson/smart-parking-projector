"""운전자 — 유도선을 보고 자기 차를 몰아 주차하고 나가는 사람.

`driving.py` 가 "선을 어떻게 따라가는가"(기술)라면, 여기는 **"지금 무엇을 할
차례인가"**(판단)다. 상태 기계 하나가 그 판단 전부다:

    ARRIVING → CRUISING → STAGING → REVERSING → PARKED → LEAVING → GONE
      안내대기   유도선따라  주차면 지나쳐 정차  후진      주차중    출차     퇴장

**`DriverProfile` 은 이 파일에만 존재한다.** 관제는 이 타입의 존재조차 몰라야
하며, 그것을 `tests/test_layer_isolation.py` 가 강제한다 (docs/DECISIONS.md D-001).

운전자가 아는 것은 자기 차의 상태(`SelfState`)와 눈에 보이는 것(`Perception`)뿐이다.
빈 주차면 목록도, 다른 차의 유도선도, 관제의 계획도 인자로 들어오지 않는다.

이 파일은 ``sim.common`` 과 같은 계층(`sim.agents`)만 import 한다 (CLAUDE.md).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from sim.common.geometry import Pose, Vec2, angle_diff
from sim.common.ids import NodeId, SlotId
from sim.common.lotmap import LotMap
from sim.common.maneuver import ParkingManeuver, plan_reverse_parking
from sim.common.messages import GuidanceView, Perception
from sim.common.vehicle import ControlInput, SelfState, VehicleSpec
from sim.agents.driving import DrivingSkill, PathFollower

TURN_RADIUS_MARGIN = 1.15
"""후진 주차에 쓸 회전반경 = 최소 회전반경 × 이 값.

한계 반경으로 붙여 돌면 조향각이 계속 최대치라 조금만 어긋나도 복구가 안 된다.
사람도 여유를 두고 돈다.
"""

STAGING_LEAD_IN = 3.0
"""정차 지점 앞에 두는 직선 유도 구간(m).

이게 없으면 차가 정차 지점에 **위치는** 맞게 서지만 **방향이** 비뚤어진다.
후진 주차는 시작 자세가 전부라 방향이 틀어지면 주차면을 벗어난다.
"""

KEEP_RIGHT = 1.35
"""통로 중앙선이 아니라 오른쪽으로 이만큼 붙어 달린다(m).

**교착을 막는 것은 신호나 통제가 아니라 이 습관이다.** 수직 통로는 양방향이고
폭이 6m 다. 두 대가 각자 오른쪽으로 붙으면 중심 간격이 2.7m 가 되어 서로 스쳐
지나갈 수 있다 — 실제 주차장이 그렇게 굴러간다. 반대로 모두가 중앙선 위를 달리면
마주친 순간 둘 다 멈추고 영원히 풀리지 않는다 (docs/DECISIONS.md D-012).

관제가 그리는 유도선은 통로 중앙에 남는다. 선을 정확히 밟고 가는 것이 아니라
선을 보고 자기 차선을 잡는 것이 사람이 하는 일이기 때문이다.
"""

FOLLOW_GAP = 1.60
"""앞차와 최소한 이만큼은 띄운다(m)."""

CREEP_SPEED = 0.35
"""이미 상대와 겹쳐 버렸을 때 빠져나가는 속도(m/s).

멈춰 있으면 서로를 영원히 막는다. 아주 느리게라도 움직여야 형상이 바뀌고
교착이 풀린다.
"""

STUCK_PATIENCE = 15.0
"""이만큼 꼼짝 못 하면 아주 느리게라도 비집고 나간다(초).

교착의 마지막 안전망이다. 사람은 영원히 기다리지 않으며, 시뮬레이션도 멈춰서는
안 된다 — 멈춘 시뮬레이션은 어떤 복구 전략이 나은지 말해주지 못한다.
"""

COMFORT_DECEL_RATIO = 0.55
"""차간거리를 지키려고 밟는 감속은 최대 제동력의 이 비율까지만."""


class DriverPhase(str, Enum):
    ARRIVING = "arriving"
    """아직 안내를 못 받았다. 입구에서 기다린다."""

    CRUISING = "cruising"
    STAGING = "staging"
    """주차면을 지나쳐 정차했다. 기어를 후진으로 넣는 중."""

    REVERSING = "reversing"
    PARKED = "parked"
    LEAVING = "leaving"
    GONE = "gone"


@dataclass(frozen=True, slots=True)
class DriverProfile:
    """운전자 한 사람. **이 타입은 agents 밖으로 나가지 않는다.**"""

    compliance: float = 1.0
    """안내를 따르는 정도 (0~1). 1 이면 유도선을 그대로 따른다.

    이 값이 이 연구의 독립변수다. 관제는 이 값을 절대 볼 수 없다 (D-001).
    이 값을 실제로 **사용하는** 이탈 판단은 5단계에서 붙인다 — 3단계는 관제가
    정상 동작하는지를 먼저 확인해야 하므로 전원 협조로 둔다.
    """

    impatience: float = 0.5
    """조급함. 대기·우회를 얼마나 싫어하는가."""

    walk_preference: float = 0.5
    """도보거리를 얼마나 중시하는가. 높을수록 건물 앞자리를 탐낸다."""

    skill: DrivingSkill = field(default_factory=DrivingSkill)


@dataclass(slots=True)
class Driver:
    """차량 한 대를 모는 운전자."""

    spec: VehicleSpec
    lot: LotMap
    profile: DriverProfile = field(default_factory=DriverProfile)

    phase: DriverPhase = DriverPhase.ARRIVING
    target_slot: SlotId | None = None

    _follower: PathFollower = field(init=False)
    _maneuver: ParkingManeuver | None = field(default=None, init=False)
    _revision: int = field(default=-1, init=False)
    _stalled_since: float | None = field(default=None, init=False)
    _wants_to_leave: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._follower = PathFollower(self.spec, self.profile.skill)

    # ── 외부에서 걸어오는 신호 ─────────────────────────────────────

    def leave(self) -> None:
        """볼일이 끝났다. 다음 판단에서 출차를 시작한다.

        언제 나갈지는 운전자의 일정이지 관제의 결정이 아니므로 밖에서 알려준다.
        """
        self._wants_to_leave = True

    @property
    def is_done(self) -> bool:
        return self.phase is DriverPhase.GONE

    @property
    def is_parked(self) -> bool:
        return self.phase is DriverPhase.PARKED

    # ── 매 틱의 판단 ──────────────────────────────────────────────

    def decide(self, t: float, state: SelfState, perception: Perception) -> ControlInput:
        self._track_stall(t, state)

        # 앞차든 교행 대기 정지선이든, 운전자에게는 "저기서 멈춰야 한다"는 하나의
        # 사실이다. 더 가까운 쪽에 맞춘다.
        limit = self._speed_limit(
            min(perception.pose_forward_clearance, perception.stop_distance),
            impatient=self._is_stuck(t),
        )

        if self.phase is DriverPhase.ARRIVING:
            if perception.guidance is None:
                return self._halt()
            self._accept(perception.guidance)

        if self.phase is DriverPhase.CRUISING:
            return self._cruise(state, perception.guidance, limit)

        if self.phase is DriverPhase.STAGING:
            return self._shift_to_reverse(state)

        if self.phase is DriverPhase.REVERSING:
            return self._reverse(state)

        if self.phase is DriverPhase.PARKED:
            if self._wants_to_leave and self._begin_exit(state):
                return self._drive(state, limit)
            return self._halt()

        if self.phase is DriverPhase.LEAVING:
            if self._follower.is_finished(state):
                self.phase = DriverPhase.GONE
                return self._halt()
            return self._drive(state, limit)

        return self._halt()

    # ── 각 단계 ───────────────────────────────────────────────────

    def _cruise(
        self, state: SelfState, guidance: GuidanceView | None, limit: float | None
    ) -> ControlInput:
        if guidance is not None and guidance.revision != self._revision:
            # 안내가 갈아끼워졌다 — 자리를 빼앗겼거나 재배치됐다.
            # 운전자는 이유를 모른다. 선이 바뀌었으니 새 선을 따라갈 뿐이다.
            self._accept(guidance)
        elif guidance is None:
            # 선이 사라졌다. 갈 곳을 잃었으니 세운다 — 다음 안내를 기다린다.
            self.phase = DriverPhase.ARRIVING
            self.target_slot = None
            return self._halt()

        if self._follower.is_finished(state):
            self.phase = DriverPhase.STAGING
            return self._halt()

        return self._drive(state, limit)

    def _shift_to_reverse(self, state: SelfState) -> ControlInput:
        """완전히 멈춘 뒤에만 후진으로 넣는다. 물리가 그것을 강제한다."""
        if state.speed > 0.04 or self._maneuver is None:
            return self._halt()
        self._follower.set_path(self._maneuver.reverse_path, gear=-1)
        self.phase = DriverPhase.REVERSING
        return self._follower.control(state)

    def _reverse(self, state: SelfState) -> ControlInput:
        """후진 중에는 앞차 때문에 멈추지 않는다.

        이미 통로를 막고 있고, 뒤차는 기다릴 수밖에 없다. 실제 주차장에서도
        그렇고, 그 정체가 이 시뮬레이션이 재현해야 할 현상이다.
        """
        if self._follower.is_finished(state):
            self.phase = DriverPhase.PARKED
            return self._halt()
        return self._follower.control(state)

    def _begin_exit(self, state: SelfState) -> bool:
        """주차면에서 출구까지의 경로를 스스로 짠다.

        관제에게 묻지 않는다 — 나가는 길은 표지판을 보면 알 수 있고, 운전자가
        관제의 경로 탐색을 호출하면 계층이 무너진다. 그래서 도면의 단순 최단경로
        (`LotMap.shortest_path`) 만 쓴다.
        """
        path = self._exit_path(state)
        if path is None:
            self._wants_to_leave = False
            return False
        self._follower.set_path(path, gear=1)
        self.phase = DriverPhase.LEAVING
        self.target_slot = None
        return True

    # ── 안내 수용 ─────────────────────────────────────────────────

    def _accept(self, guidance: GuidanceView) -> None:
        """새 유도선을 받아 주행 경로와 후진 주차 궤적을 준비한다."""
        slot = self.lot.slots[guidance.target_slot]
        lane = _keep_right(
            _lane_part(guidance.polyline, slot.entry_point, slot.center), KEEP_RIGHT
        )
        approach = _approach_heading(lane, self.lot.node_pos(slot.access_node))

        self._maneuver = plan_reverse_parking(
            slot_center=slot.center,
            slot_heading=slot.heading,
            approach_heading=approach,
            rear_axle_to_center=self.spec.rear_axle_to_center,
            turn_radius=self.spec.min_turn_radius * TURN_RADIUS_MARGIN,
        )

        staging = self._maneuver.staging
        lead_in = staging.position - Vec2.from_angle(staging.theta) * STAGING_LEAD_IN
        drive_path = _trim_before(lane, lead_in) + [lead_in, staging.position]

        self._follower.set_path(drive_path, gear=1)
        self.target_slot = guidance.target_slot
        self._revision = guidance.revision
        self.phase = DriverPhase.CRUISING

    def _exit_path(self, state: SelfState) -> list[Vec2] | None:
        """주차면 → 출구 경로.

        **들어온 궤적을 그대로 되짚어 나간다.** 주차면에서 통로 건너편의 한 점으로
        곧장 향하면, 통로를 사이에 두고 마주 보는 두 줄의 차가 같은 지점을 노리며
        정면으로 만난다 — 통로가 그대로 굳는다. 실제로 그렇게 굳었다.

        후진 주차 궤적(`ParkingManeuver.reverse_path`)을 뒤집으면 그것이 곧
        전진 출차 궤적이다. 자전거 모델은 전·후진이 대칭이므로 들어온 길로는
        반드시 나갈 수 있고, 그 끝(정차 지점)은 이미 통로 방향에 정렬돼 있다.
        """
        if not self.lot.exit_nodes:
            return None
        slot = self.lot.slots.get(self.target_slot) if self.target_slot else None
        access = slot.access_node if slot else self._nearest_node(state.pose.position)
        route = self.lot.shortest_path(access, self.lot.exit_nodes[0])
        if route is None:
            return None

        lane = _keep_right([self.lot.node_pos(n) for n in route], KEEP_RIGHT)
        if self._maneuver is None:
            return [state.pose.position] + lane

        staging = self._maneuver.staging
        ahead = _drop_behind(lane, staging.position, Vec2.from_angle(staging.theta))
        return list(reversed(self._maneuver.reverse_path)) + (ahead or lane)

    def _nearest_node(self, p: Vec2) -> NodeId:
        return min(self.lot.nodes, key=lambda n: self.lot.node_pos(n).distance_to(p))

    # ── 저수준 ────────────────────────────────────────────────────

    def _drive(self, state: SelfState, limit: float | None) -> ControlInput:
        return self._follower.control(state, speed_limit=limit)

    def _halt(self) -> ControlInput:
        return ControlInput(steer=0.0, accel=-self.spec.max_decel, gear=0)

    def _track_stall(self, t: float, state: SelfState) -> None:
        moving_phase = self.phase in (DriverPhase.CRUISING, DriverPhase.LEAVING)
        if moving_phase and state.speed < 0.05:
            if self._stalled_since is None:
                self._stalled_since = t
        else:
            self._stalled_since = None

    def _is_stuck(self, t: float) -> bool:
        return self._stalled_since is not None and t - self._stalled_since >= STUCK_PATIENCE

    def _speed_limit(self, clearance: float, impatient: bool = False) -> float | None:
        """앞차까지의 여유 거리로부터 낼 수 있는 속도를 구한다.

        월드가 강제로 멈춰 세우는 것이 아니라 **운전자가 보고 스스로 줄인다.**
        차간거리는 운전자의 행동이지 시뮬레이터의 기능이 아니다 (docs/HANDOFF.md 이슈 1).
        """
        if not math.isfinite(clearance):
            return None
        if impatient:
            return CREEP_SPEED
        if clearance <= 0.0:
            # 이미 상대와 겹쳐 버렸다. 여기서 완전히 멈추면 상대도 나 때문에 못
            # 움직이고, 둘 다 영원히 굳는다 — 관측된 교착은 전부 이 상태였다.
            # 실제 운전자는 이럴 때 아주 천천히 비집고 빠져나간다.
            return CREEP_SPEED
        gap = clearance - FOLLOW_GAP
        if gap <= 0.0:
            return 0.0
        return math.sqrt(2.0 * self.spec.max_decel * COMFORT_DECEL_RATIO * gap)


# ── 폴리라인 손질 ────────────────────────────────────────────────


def _lane_part(
    polyline: Sequence[Vec2], entry_point: Vec2, slot_center: Vec2
) -> list[Vec2]:
    """유도선에서 통로 구간만 떼어낸다.

    관제가 그리는 선은 주차면 안까지 들어간다 — 운전자에게 어느 자리인지 보여주기
    위해서다. 하지만 전진으로 그 선 끝까지 갈 수는 없다. 마지막 두 점을 떼고
    나머지 통로 구간만 실제 주행 경로로 쓴다.
    """
    pts = list(polyline)
    for anchor in (slot_center, entry_point):
        if pts and pts[-1].distance_to(anchor) < 1e-6:
            pts.pop()
    return pts


def _keep_right(points: Sequence[Vec2], offset: float) -> list[Vec2]:
    """폴리라인을 진행 방향 기준 오른쪽으로 민다.

    꼭짓점에서는 앞뒤 구간의 이등분선 방향으로 민다. 각 구간을 따로 밀면 코너에서
    선이 끊어진다.
    """
    pts = list(points)
    if len(pts) < 2 or offset == 0.0:
        return pts

    out: list[Vec2] = []
    last = len(pts) - 1
    for i, p in enumerate(pts):
        if i == 0:
            d = (pts[1] - pts[0]).normalized()
        elif i == last:
            d = (pts[last] - pts[last - 1]).normalized()
        else:
            a = (pts[i] - pts[i - 1]).normalized()
            b = (pts[i + 1] - pts[i]).normalized()
            d = (a + b).normalized()
            if d.length < 1e-6:      # 되돌아가는 꼭짓점 — 앞 구간 기준으로 민다
                d = a
        out.append(p + Vec2(d.y, -d.x) * offset)
    return out


def _approach_heading(lane: Sequence[Vec2], access_pos: Vec2) -> float:
    """주차면 앞 통로를 어느 방향으로 달려오는가.

    일방통행 통로이므로 방향은 하나뿐이고, 유도선의 마지막 구간이 곧 그 방향이다.
    """
    if len(lane) >= 2:
        d = lane[-1] - lane[-2]
        if d.length > 1e-6:
            return d.angle
    if lane:
        d = access_pos - lane[-1]
        if d.length > 1e-6:
            return d.angle
    return 0.0


def _drop_behind(points: Sequence[Vec2], origin: Vec2, direction: Vec2) -> list[Vec2]:
    """origin 보다 뒤에 있는 앞쪽 점들을 버린다.

    출차 궤적의 끝(정차 지점)은 주차면 앞 통로 노드를 이미 지나쳐 있다. 그 노드로
    되돌아가는 경로가 앞에 남아 있으면 차가 뒤로 갔다 오는 모양이 된다.
    """
    return [p for p in points if (p - origin).dot(direction) > 0.0]


def _trim_before(lane: Sequence[Vec2], lead_in: Vec2) -> list[Vec2]:
    """정차 유도 구간과 겹치거나 그것을 지나친 통로 점들을 잘라낸다.

    자르지 않으면 경로가 뒤로 갔다가 다시 앞으로 오는 모양이 되고, Pure Pursuit 이
    그 접힌 구간에서 방향을 잃는다.
    """
    pts = list(lane)
    if len(pts) < 2:
        return pts

    tail = pts[-1] - pts[-2]
    if tail.length < 1e-6:
        return pts
    direction = tail.normalized()

    while len(pts) >= 2 and (lead_in - pts[-1]).dot(direction) <= 0.0:
        pts.pop()
    return pts


def steering_error(pose: Pose, path_heading: float) -> float:
    """차량 방향과 경로 방향의 차이. 지표 수집과 테스트에서 쓴다."""
    return angle_diff(path_heading, pose.theta)
