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
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from sim.common.geometry import Pose, Vec2, angle_diff, offset_polyline
from sim.common.ids import NodeId, SlotId
from sim.common.lotmap import LotMap, Slot
from sim.common.maneuver import ParkingManeuver, plan_reverse_parking
from sim.common.messages import GuidanceView, Perception
from sim.common.vehicle import ControlInput, SelfState, VehicleSpec
from sim.agents.driving import DrivingSkill, PathFollower

TURN_RADIUS_MARGIN = 1.15
"""후진 주차 회전반경의 하한 = 최소 회전반경 × 이 값.

한계 반경으로 붙여 돌면 조향각이 계속 최대치라 조금만 어긋나도 복구가 안 된다.
사람도 여유를 두고 돈다.

실제 반경은 이보다 클 수 있다 — `Driver._parking_radius` 가 통로 폭에 맞춰 키운다.
"""

EXIT_CLEARANCE = 1.30
"""주차면에서 곧장 빠져나오는 직선 구간(m).

`common.maneuver.plan_reverse_parking` 의 기본값과 같아야 한다 — 회전반경을
역산할 때 쓰기 때문이다.
"""

AISLE_PROBE = 80.0
"""무안내 탐색에서 '지금 달리는 통로'를 대신할 직선 구간의 길이(m).

눈에 들어온 자리는 언제나 지금 달리는 통로에 접해 있으므로(`_is_on_this_aisle`),
진행 방향의 직선 하나면 진입 계산에 충분하다.
"""

MIN_STAGING_LEAD_IN = 3.0
"""정차 지점 앞에 두는 직선 유도 구간의 최소 길이(m).

이게 없으면 차가 정차 지점에 **위치는** 맞게 서지만 **방향이** 비뚤어진다.
후진 주차는 시작 자세가 전부라 방향이 틀어지면 주차면을 벗어난다.
"""

STAGING_APPROACH_RATIO = 3.0
"""차선에서 정차 지점까지 옆으로 벌어진 거리 × 이 값 = 접근 구간 길이.

**통로가 넓어지면 이 구간도 길어져야 한다.** 주행 차선은 통로 중앙 근처에 있고
후진 주차 정차 지점은 주차면 쪽 끝에 있는데, 통로가 넓을수록 그 둘이 멀어진다.
14m 통로에서는 9.5m 나 벌어진다 — 그걸 3m 안에 붙으려면 차가 통로를 가로지르는
급격한 사선을 그리게 되고, 반대편 차선까지 막아 통로가 굳는다.

비율 3 이면 사선 기울기가 약 18° 로 완만해진다. 좁은 통로(7m)에서는 벌어진 거리가
1.2m 뿐이라 결과가 최소값과 거의 같다 — 기존 동작을 바꾸지 않는다.
"""

KEEP_RIGHT_RATIO = 0.225
"""통로 중앙선이 아니라 오른쪽으로 (통로 폭 × 이 비율)만큼 붙어 달린다.

폭 6m 통로에서 1.35m — 교착을 없앤 값이 그것이었고, 통로를 넓히면 간격도 함께
넓어져야 한다. 고정값으로 두면 통로만 넓어지고 차는 여전히 가운데로 몰린다.

**교착을 막는 것은 신호나 통제가 아니라 이 습관이다.** 수직 통로는 양방향이고
폭이 6m 다. 두 대가 각자 오른쪽으로 붙으면 중심 간격이 2.7m 가 되어 서로 스쳐
지나갈 수 있다 — 실제 주차장이 그렇게 굴러간다. 반대로 모두가 중앙선 위를 달리면
마주친 순간 둘 다 멈추고 영원히 풀리지 않는다 (docs/DECISIONS.md D-012).

관제가 그리는 유도선은 통로 중앙에 남는다. 선을 정확히 밟고 가는 것이 아니라
선을 보고 자기 차선을 잡는 것이 사람이 하는 일이기 때문이다.
"""

EXIT_RUN_OUT = 30.0
"""출구 노드를 지나 이만큼 더 이어 두는 가상의 경로(m).

**경로가 출구에서 끝나면 운전자는 거기서 정지한다.** 경로 추종기는 끝점에서 멈추도록
감속하므로, 출구 6m 전부터 속도를 줄이고 차단기 앞에서 완전히 선다. 한 대씩 서면
그 뒤로 통로 전체가 밀린다 — 실제로 그렇게 병목이 생겼다.

실제 운전자는 출구를 그냥 통과해 나간다. 그래서 경로를 주차장 바깥까지 연장해
두고, 출구를 지나는 순간 퇴장 처리한다. 감속할 '끝'이 없으니 서지 않는다.
"""

EXIT_MARGIN = 0.5
"""출구 정지선을 이만큼 지나면 떠난 것으로 본다(m).

**거리로 판정하면 안 된다.** 차는 우측통행으로 통로 중앙에서 비껴 달리므로 출구
노드 바로 옆을 스쳐 지나갈 뿐, 그 지점에 가까이 가지 않는다. 반경으로 재면
판정이 영영 안 되고 차가 주차장 밖까지 계속 달린다 — 실제로 그랬다.

지나갔는지는 **진행 방향으로 투영**해서 본다. 옆으로 얼마나 비껴 있든 상관없다.
"""

MIN_TEMPTATION_GAIN = 12.0
"""이만큼은 덜 달려야 이탈을 고민한다(m).

작게 잡으면 모두가 눈앞의 첫 빈자리로 뛰어들어 '유도선을 무시한다'가 아니라
'유도선이 무의미하다'가 된다. 12m 는 통로 한 구간쯤 — 사람이 "저기가 더 가깝네"
라고 느낄 만한 거리다.
"""

MIN_TEMPTATION_AHEAD = 9.0
"""이 거리보다 가까운 자리는 노리지 않는다(m) — 최소값.

후진 주차는 주차면을 지나쳐 정차한 뒤 들어가는 것이라, 코앞의 자리는 이미 늦었다.
**빨리 달릴수록 더 멀리서 판단해야 한다.** 실제 값은 순항 속도에서 계산한다
(`TEMPTATION_LEAD_SECONDS`).
"""

TEMPTATION_LEAD_SECONDS = 2.5
"""이만큼 앞을 내다보고 이탈을 결정한다(초).

거리로 고정하면 통로가 넓어져 속도가 빨라졌을 때 판단이 늦는다 — 자리를 발견한
순간 이미 지나쳐 있다.
"""

WALK_WEIGHT_RANGE = (1.0, 5.0)
"""도보 1m 를 주행 몇 m 로 치는가. `walk_preference` 가 이 범위를 훑는다.

**이 값이 강탈의 진짜 동기다.** 관제의 비용 함수도 주행거리와 도보거리를 저울질하지만
(`sim/control/cost.py`), 그 저울과 운전자의 저울은 다르다. 관제는 "전체가 조금씩
덜 달리는" 배분을 하고, 어떤 운전자는 "나는 조금 더 달려도 건물 앞에 대겠다"고
생각한다. 그 **저울의 불일치**가 사람이 안내를 무시하는 이유다.

거리만 보면 이탈이 거의 일어나지 않는다 — 관제가 이미 가까운 자리를 주기 때문이다.
실제로 그렇게 만들어 보니 비협조 운전자 31명 중 4명만 이탈했다.
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

    SEEKING = "seeking"
    """안내 없이 스스로 통로를 돌며 빈자리를 찾는다 (무안내 베이스라인, D-010)."""

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
    """안내를 따르는 정도 (0~1). **유혹 한 번을 참아낼 확률**이다.

    더 가까운 빈자리가 눈에 들어올 때마다 한 번씩 판정한다. 1.0 이면 절대 이탈하지
    않고, 0.0 이면 조건이 맞는 첫 자리에서 바로 이탈한다. 같은 자리를 두 번
    고민하지는 않는다 — 사람이 그렇듯 한 번 지나치면 끝이다.

    이 값이 이 연구의 독립변수이고, **관제는 이 값을 절대 볼 수 없다** (D-001).
    관제는 자리를 빼앗긴 뒤에야 센서로 알게 된다. 그 지연이 측정 대상이다.
    """

    impatience: float = 0.5
    """조급함. 대기·우회를 얼마나 싫어하는가."""

    walk_preference: float = 0.5
    """도보거리를 얼마나 중시하는가 (0~1). 높을수록 건물 앞자리를 탐낸다.

    `WALK_WEIGHT_RANGE` 를 훑어 "도보 1m = 주행 몇 m" 로 환산된다.
    """

    @property
    def walk_weight(self) -> float:
        lo, hi = WALK_WEIGHT_RANGE
        return lo + (hi - lo) * max(0.0, min(1.0, self.walk_preference))

    skill: DrivingSkill = field(default_factory=DrivingSkill)


@dataclass(slots=True)
class Driver:
    """차량 한 대를 모는 운전자."""

    spec: VehicleSpec
    lot: LotMap
    profile: DriverProfile = field(default_factory=DriverProfile)

    self_directed: bool = False
    """안내가 없을 때 스스로 찾아다닐 것인가.

    **무안내 베이스라인(D-010)의 스위치다.** 평소에는 꺼 둔다 — 유도선 시스템이
    도는 중이라면 안내를 못 받은 차는 잠깐 기다리는 것이 맞고, 제멋대로 돌아다니면
    관제의 성능을 측정할 수 없다.
    """

    phase: DriverPhase = DriverPhase.ARRIVING
    target_slot: SlotId | None = None

    _follower: PathFollower = field(init=False)
    _maneuver: ParkingManeuver | None = field(default=None, init=False)
    rng: random.Random = field(default_factory=random.Random)
    """이탈 판정용 난수. 월드가 차량마다 시드를 심어 재현성을 지킨다."""

    _revision: int = field(default=-1, init=False)
    _stalled_since: float | None = field(default=None, init=False)
    _keep_right: float = field(default=0.0, init=False)
    _min_ahead: float = field(default=MIN_TEMPTATION_AHEAD, init=False)
    _exit_gate: tuple[Vec2, Vec2] | None = field(default=None, init=False)
    """출구 정지선 (위치, 나가는 방향)."""
    _lane: list[Vec2] = field(default_factory=list, init=False)
    """지금 달리는 통로 구간. 이탈할 때 새 정차 지점만 갈아 끼우면 된다."""

    _approach: float = field(default=0.0, init=False)
    _target_walk: float = field(default=0.0, init=False)
    _considered: set[SlotId] = field(default_factory=set, init=False)
    _defected: bool = field(default=False, init=False)
    _wants_to_leave: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._follower = PathFollower(self.spec, self.profile.skill)
        self._keep_right = self.lot.travel_lane_offset
        self._min_ahead = max(
            MIN_TEMPTATION_AHEAD,
            self.profile.skill.cruise_speed * TEMPTATION_LEAD_SECONDS,
        )

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

    @property
    def is_idle(self) -> bool:
        """세워두고 아무 생각 없는 상태인가.

        주차를 마쳤고 아직 나갈 때가 아니면 이 운전자는 어떤 입력을 받아도 같은
        답(정지)을 낸다. 만차 주차장에서는 차량의 8할이 여기 해당하므로, 이걸
        건너뛰는 것만으로 시뮬레이션이 눈에 띄게 빨라진다.
        """
        return self.phase is DriverPhase.PARKED and not self._wants_to_leave

    @property
    def watches_for_slots(self) -> bool:
        """빈자리를 두리번거리는가.

        월드가 시야 계산을 할지 말지 물어보는 창구다. 협조적인 운전자는 유도선만
        보고 가므로 계산할 이유가 없다 — 그리고 이렇게 물어보면 월드가
        `compliance` 를 직접 읽지 않아도 된다.
        """
        if self.phase in (DriverPhase.STAGING, DriverPhase.SEEKING):
            return True     # 후진 직전의 마지막 확인, 그리고 무안내 탐색
        if self.self_directed and self.phase is DriverPhase.ARRIVING:
            return True     # 첫 틱부터 눈을 뜨고 들어온다
        return self.profile.compliance < 1.0 and self.phase is DriverPhase.CRUISING

    def park_in(self, slot: Slot, approach_heading: float) -> Pose:
        """이미 주차를 마친 상태로 시작한다. 시작부터 붐비는 주차장을 만들 때 쓴다.

        나갈 때 쓸 궤적까지 여기서 만들어 둔다. 그게 없으면 이 차만 주차면에서
        통로 건너편으로 곧장 나가려 해서 교통이 엉킨다 (docs/DECISIONS.md D-012).
        """
        self._maneuver = plan_reverse_parking(
            slot_center=slot.center,
            slot_heading=slot.heading,
            approach_heading=approach_heading,
            rear_axle_to_center=self.spec.rear_axle_to_center,
            turn_radius=self.spec.min_turn_radius * TURN_RADIUS_MARGIN,
        )
        self.target_slot = slot.id
        self.phase = DriverPhase.PARKED
        return self._maneuver.final

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
                if not self.self_directed:
                    return self._halt()
                self.phase = DriverPhase.SEEKING
            else:
                self._accept(perception.guidance)

        if self.phase is DriverPhase.SEEKING:
            return self._seek(state, perception, limit)

        if self.phase is DriverPhase.CRUISING:
            return self._cruise(state, perception, limit)

        if self.phase is DriverPhase.STAGING:
            return self._shift_to_reverse(state, perception)

        if self.phase is DriverPhase.REVERSING:
            return self._reverse(state)

        if self.phase is DriverPhase.PARKED:
            if self._wants_to_leave and self._begin_exit(state):
                return self._drive(state, limit)
            return self._halt()

        if self.phase is DriverPhase.LEAVING:
            if self._has_left(state) or self._follower.is_finished(state):
                self.phase = DriverPhase.GONE
                return self._halt()
            return self._drive(state, limit)

        return self._halt()

    # ── 각 단계 ───────────────────────────────────────────────────

    def _cruise(
        self, state: SelfState, perception: Perception, limit: float | None
    ) -> ControlInput:
        guidance = perception.guidance

        if guidance is not None and guidance.revision != self._revision:
            if self._defected:
                # 이미 다른 자리를 노리기로 했다. 새 선이 그려져도 따르지 않는다 —
                # 그게 '비협조'의 정의다. 다만 개정 번호는 봤다고 기억해 둔다.
                self._revision = guidance.revision
            else:
                # 안내가 갈아끼워졌다 — 자리를 빼앗겼거나 재배치됐다.
                # 운전자는 이유를 모른다. 선이 바뀌었으니 새 선을 따라갈 뿐이다.
                self._accept(guidance)
        elif guidance is None and not self._defected:
            # 선이 사라졌다. 갈 곳을 잃었으니 세운다 — 다음 안내를 기다린다.
            self.phase = DriverPhase.ARRIVING
            self.target_slot = None
            return self._halt()

        if self._abandon_if_taken(perception):
            return self._halt()

        self._consider_defection(state, perception)

        if self._follower.is_finished(state):
            self.phase = DriverPhase.STAGING
            return self._halt()

        return self._drive(state, limit)

    # ── 무안내 탐색 (베이스라인) ──────────────────────────────────

    def _seek(
        self, state: SelfState, perception: Perception, limit: float | None
    ) -> ControlInput:
        """유도선 없이 통로를 돌며 눈으로 자리를 찾는다.

        **이것이 이 시스템이 없을 때의 세상이다** (D-010). 운전자는 어디가 비었는지
        모른 채 통로를 훑고, 눈에 들어온 자리 중 자기 기준으로 가장 나은 것을 잡는다.
        같은 시드로 안내 모드와 나란히 돌리면 "왜 이 시스템이 필요한가"가 숫자로 나온다.

        판단 기준은 이탈(`_consider_defection`)과 **같은 저울**을 쓴다. 비협조
        운전자가 안내를 무시할 때 쓰는 그 기준이, 안내가 아예 없을 때는 유일한 기준이 된다.
        """
        if perception.guidance is not None:
            self._accept(perception.guidance)      # 안내가 켜졌다면 따른다
            return self._drive(state, limit)

        if self._take_best_visible(state, perception):
            # 자리를 잡았으면 탐색은 끝이다. 이제부터는 안내를 받은 차와 똑같이
            # 그 자리로 향한다 — 정차 · 후진 · 주차 절차가 그대로 이어진다.
            self.phase = DriverPhase.CRUISING
            return self._drive(state, limit)

        if len(self._follower._path) < 2 or self._follower.is_finished(state):
            if not self._wander(state):
                return self._halt()
        return self._drive(state, limit)

    def _take_best_visible(self, state: SelfState, perception: Perception) -> bool:
        """눈에 들어온 자리 중 가장 나은 것을 잡는다. 없으면 계속 돈다."""
        if not perception.visible_slots or len(self._lane) < 2:
            return False

        ct = math.cos(state.pose.theta)
        st = math.sin(state.pose.theta)
        best: SlotId | None = None
        best_gain = -math.inf

        for vs in perception.visible_slots:
            if not vs.looks_free or vs.slot_id in self._considered:
                continue
            d = vs.center - state.pose.position
            ahead = d.x * ct + d.y * st
            if ahead < self._min_ahead or not self._is_on_this_aisle(vs.slot_id, state):
                continue

            self._considered.add(vs.slot_id)
            # 가까이서 보이고 덜 걸어도 되는 자리가 좋다 — 운전자 자신의 저울이다.
            gain = -ahead - self.profile.walk_weight * vs.walk_distance
            if gain > best_gain:
                best_gain, best = gain, vs.slot_id

        if best is None:
            return False

        # 지금 달리는 통로를 기준으로 진입한다. 순회 경로 전체(여러 통로를 잇는
        # 경로)를 기준으로 삼으면 진입 구간을 잘라내는 계산이 엉킨다 — 실제로
        # 그것 때문에 무안내 모드에서 한 대도 주차하지 못했다.
        here = state.pose.position
        forward = Vec2.from_angle(state.pose.theta)
        self._lane = [here - forward * 5.0, here + forward * AISLE_PROBE]
        self._approach = state.pose.theta
        return self._divert(best)

    def _wander(self, state: SelfState) -> bool:
        """다음으로 훑어볼 통로를 정한다.

        건물에 가까운 쪽부터 본다. 실제로도 사람은 좋은 자리부터 뒤지고, 그래야
        '안내가 없으면 인기 구역만 붐빈다'는 현상이 재현된다.
        """
        here = self._nearest_node(state.pose.position)
        candidates = [
            s for s in self.lot.slots.values() if s.access_node != here
        ]
        if not candidates:
            return False

        # 도보거리가 짧은 쪽에 가중치를 준다
        candidates.sort(key=lambda s: self.lot.walk_distance(s.id))
        pick = candidates[self.rng.randrange(max(1, len(candidates) // 3))]

        route = self.lot.shortest_path(here, pick.access_node)
        if route is None or len(route) < 2:
            return False

        lane = offset_polyline([self.lot.node_pos(n) for n in route], self._keep_right)
        self._lane = lane
        self._approach = _approach_heading(lane, self.lot.node_pos(pick.access_node))
        self._follower.set_path([state.pose.position] + lane, gear=1)
        return True

    # ── 이탈 ──────────────────────────────────────────────────────

    def _abandon_if_taken(self, perception: Perception) -> bool:
        """가려던 자리가 이미 차 있는 것이 눈에 보이면 포기한다.

        배정받은 자리를 남이 차지했을 때도, 내가 노리던 자리를 남이 먼저
        차지했을 때도 같은 행동이다. 운전자는 그 자리에 도착해서야 알게 되는 것이
        아니라 멀리서 보고 안다.
        """
        if self.target_slot is None:
            return False
        for vs in perception.visible_slots:
            if vs.slot_id == self.target_slot and not vs.looks_free:
                self.target_slot = None
                self._defected = False
                self._revision = -1        # 다음 안내는 새것으로 받는다
                self.phase = DriverPhase.ARRIVING
                return True
        return False

    def _consider_defection(self, state: SelfState, perception: Perception) -> bool:
        """눈에 들어온 빈자리가 더 나으면, 성향에 따라 안내를 무시하고 그리로 간다.

        **이 함수가 이 연구가 다루는 돌발 상황 그 자체다.** 관제는 이 판단을 볼 수
        없고, 차가 엉뚱한 자리에 들어앉은 뒤에야 센서로 알게 된다.

        판단 기준 (docs/PLAN.md 7):
          (a) **운전자 자신의 저울**로 재서 지금 가는 것보다 뚜렷하게 낫고
              (덜 달리거나, 덜 걷거나, 그 둘의 조합이)
          (b) 성향 판정(compliance)을 통과하지 못했을 때

        (a) 의 저울이 관제의 저울과 다르다는 점이 핵심이다. 관제는 전체를 보고
        배분하고, 운전자는 자기만 본다.
        """
        if self.profile.compliance >= 1.0 or self.target_slot is None:
            return False
        if not perception.visible_slots:
            return False

        ct = math.cos(state.pose.theta)
        st = math.sin(state.pose.theta)
        remaining = self._follower.remaining(state)

        for vs in perception.visible_slots:
            if not vs.looks_free or vs.slot_id == self.target_slot:
                continue
            if vs.slot_id in self._considered:
                continue        # 한 번 지나친 자리는 다시 고민하지 않는다

            d = vs.center - state.pose.position
            ahead = d.x * ct + d.y * st
            if ahead < self._min_ahead:
                continue        # 후진 주차를 하기엔 이미 늦었다
            if not self._is_on_this_aisle(vs.slot_id, state):
                continue

            self._considered.add(vs.slot_id)

            # 운전자 자신의 저울로 잰다. 덜 달리는 것과 덜 걷는 것을 함께 본다.
            gain = (remaining - ahead) + self.profile.walk_weight * (
                self._target_walk - vs.walk_distance
            )
            if gain < MIN_TEMPTATION_GAIN:
                continue
            if self.rng.random() < self.profile.compliance:
                continue        # 참았다

            if self._divert(vs.slot_id):
                return True
        return False

    def _is_on_this_aisle(self, slot_id: SlotId, state: SelfState) -> bool:
        """지금 달리는 통로에 접한 자리인가.

        수직 통로를 달릴 때는 옆으로 보이는 주차면이 **다른 통로 소속**이다. 그리로
        꺾어 들어가면 진입 방향이 맞지 않아 주차가 성립하지 않는다. 주차면 방향이
        내 진행 방향과 직각일 때만 지금 이 통로의 자리다.
        """
        slot = self.lot.slots.get(slot_id)
        if slot is None:
            return False
        return abs(math.cos(slot.heading - state.pose.theta)) < 0.35

    def _divert(self, slot_id: SlotId) -> bool:
        """목적지를 바꾼다. 관제에는 알리지 않는다 — 알릴 방법도 없다."""
        slot = self.lot.slots.get(slot_id)
        if slot is None or len(self._lane) < 2:
            return False

        maneuver = plan_reverse_parking(
            slot_center=slot.center,
            slot_heading=slot.heading,
            approach_heading=self._approach,
            rear_axle_to_center=self.spec.rear_axle_to_center,
            turn_radius=self._parking_radius(slot, self._approach),
        )
        staging = maneuver.staging
        lead_in = _lead_in_point(self._lane, staging)
        path = _trim_before(self._lane, lead_in) + [lead_in, staging.position]
        if len(path) < 2:
            return False

        self._maneuver = maneuver
        self._follower.set_path(path, gear=1)
        self.target_slot = slot_id
        self._defected = True
        return True

    def _parking_radius(self, slot: Slot, approach: float) -> float:
        """후진 주차에 쓸 회전반경. **정차 지점이 주행 차선 위에 오도록** 정한다.

        고정 반경을 쓰면 통로가 넓어져도 차는 주차면 코앞에서만 꺾는다. 그러면
        정차 지점이 주행 차선에서 멀찍이 떨어지고, 거기 붙으려고 차가 통로를
        20m 넘게 사선으로 가로지른다 — 화면에서 터무니없이 큰 호로 보이고,
        그동안 반대 차선까지 막는다.

        실제 운전자는 **차선을 따라 직진하다가 그 자리에서** 후진해 들어간다.
        통로가 넓으면 그만큼 크게 돌 뿐이다. 그 기하를 그대로 계산한다.

        후진 궤적의 기하(`common.maneuver.plan_reverse_parking`)에서, 정차 지점이
        주차면 중심으로부터 통로 쪽으로 떨어지는 거리는

            -뒷축_차체중심_거리 + 주차면_탈출_직선 + 회전반경

        이다. 이것이 주행 차선까지의 거리와 같아지는 반경을 구한다.
        """
        outward = (self.lot.node_pos(slot.access_node) - slot.center)
        depth = outward.length              # 주차면 중심 → 통로 중심선
        if depth <= 0.0:
            return self.spec.min_turn_radius * TURN_RADIUS_MARGIN

        # 주행 차선은 진행 방향의 오른쪽. 주차면이 그쪽이면 가깝고, 건너편이면 멀다.
        right = Vec2.from_angle(approach - math.pi / 2)
        lane = depth + right.dot(outward.normalized()) * self._keep_right

        radius = lane + self.spec.rear_axle_to_center - EXIT_CLEARANCE
        return max(self.spec.min_turn_radius * TURN_RADIUS_MARGIN, radius)

    def _walk_distance(self, point: Vec2) -> float:
        """건물 출입구까지의 도보 거리. 운전자도 출입구가 어디인지는 안다."""
        gates = self.lot.pedestrian_gates
        return min((point.distance_to(g) for g in gates), default=0.0)

    def _shift_to_reverse(self, state: SelfState, perception: Perception) -> ControlInput:
        """완전히 멈춘 뒤에만 후진으로 넣는다. 물리가 그것을 강제한다.

        후진을 시작하기 **직전에 한 번 더** 자리를 확인한다. 여기가 마지막 기회다 —
        일단 들어가기 시작하면 남이 이미 있는 자리에 그대로 밀고 들어가게 된다.
        관제가 자리를 잘못 줬든, 오는 동안 누가 먼저 차지했든, 눈으로 보면 안다.
        """
        if self._abandon_if_taken(perception):
            return self._halt()
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
        # 바닥에 그려진 선을 **그대로** 따른다. 관제가 이미 주행 차선 위에 그렸다.
        slot = self.lot.slots[guidance.target_slot]
        lane = _lane_part(guidance.polyline, slot.entry_point, slot.center)
        approach = _approach_heading(lane, self.lot.node_pos(slot.access_node))

        self._maneuver = plan_reverse_parking(
            slot_center=slot.center,
            slot_heading=slot.heading,
            approach_heading=approach,
            rear_axle_to_center=self.spec.rear_axle_to_center,
            turn_radius=self._parking_radius(slot, approach),
        )

        staging = self._maneuver.staging
        lead_in = _lead_in_point(lane, staging)
        drive_path = _trim_before(lane, lead_in) + [lead_in, staging.position]

        self._follower.set_path(drive_path, gear=1)
        self.target_slot = guidance.target_slot
        self._revision = guidance.revision
        self._lane = lane
        self._approach = approach
        self._target_walk = self._walk_distance(slot.center)
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

        nodes = [self.lot.node_pos(n) for n in route]
        lane = _run_past_exit(offset_polyline(nodes, self._keep_right))

        # 정지선은 **비껴 달리기 전의** 노드 위치로 잡는다. 우측통행 오프셋이
        # 들어간 경로 위 점으로 잡으면 판정 기준이 차선마다 달라진다.
        if len(nodes) >= 2:
            self._exit_gate = (nodes[-1], (nodes[-1] - nodes[-2]).normalized())

        if self._maneuver is None:
            return [state.pose.position] + lane

        staging = self._maneuver.staging
        ahead = _drop_behind(lane, staging.position, Vec2.from_angle(staging.theta))
        return list(reversed(self._maneuver.reverse_path)) + (ahead or lane)

    def _has_left(self, state: SelfState) -> bool:
        """출구를 통과했는가. 차단기 앞에서 서지 않고 그대로 나간다."""
        if self._exit_gate is None:
            return False
        gate, out = self._exit_gate
        return (state.pose.position - gate).dot(out) >= EXIT_MARGIN

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


def _lead_in_point(lane: Sequence[Vec2], staging: Pose) -> Vec2:
    """정차 지점으로 들어가기 시작할 지점.

    주행 차선에서 정차 지점까지 옆으로 얼마나 벌어져 있는지를 재서, 그만큼
    완만하게 붙을 수 있는 길이를 잡는다. 통로 폭에 따라 자동으로 따라간다.
    """
    lead = MIN_STAGING_LEAD_IN
    if len(lane) >= 2:
        direction = (lane[-1] - lane[-2]).normalized()
        if direction.length > 1e-6:
            sideways = abs((staging.position - lane[-1]).cross(direction))
            lead = max(lead, sideways * STAGING_APPROACH_RATIO)
    return staging.position - Vec2.from_angle(staging.theta) * lead


def _run_past_exit(lane: Sequence[Vec2]) -> list[Vec2]:
    """출구 바깥으로 경로를 연장한다. 감속할 '끝'을 없애기 위해서다."""
    pts = list(lane)
    if len(pts) < 2:
        return pts
    tail = pts[-1] - pts[-2]
    if tail.length < 1e-6:
        return pts
    return pts + [pts[-1] + tail.normalized() * EXIT_RUN_OUT]


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
