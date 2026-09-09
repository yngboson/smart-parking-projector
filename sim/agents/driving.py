"""운전 기술 — 경로 추종과 속도 조절.

**이것은 운전자의 능력이지 시뮬레이터의 기능이 아니다.** 그래서 agents 계층에 있다.
관제가 바닥에 선을 그려줘도 그 선을 따라가는 것은 사람이 하는 일이고, 사람마다
잘하고 못하고가 다르다. 그 차이를 `skill` 로 표현한다.

이 파일은 `sim.common` 외에는 아무것도 import 하지 않는다 (CLAUDE.md 참조).
출력은 `ControlInput` 뿐이며, 그것을 실제로 적분하는 것은 world 의 몫이다.

횡방향 제어는 Pure Pursuit 을 쓴다. 앞을 내다보는 거리(lookahead)만큼 떨어진
경로 위의 점을 향해 원호를 그리는 방식으로, 사람이 운전하는 방식과 닮아 있고
저속에서 안정적이다.

    δ = atan( 2·L·sin(α) / Ld )
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from sim.common.geometry import Vec2, angle_diff, cumulative_lengths
from sim.common.lotmap import LotMap
from sim.common.vehicle import ControlInput, SelfState, VehicleSpec


@dataclass(frozen=True, slots=True)
class DrivingSkill:
    """운전 숙련도. 사람마다 다르고, 같은 유도선을 줘도 결과가 달라진다."""

    lookahead_gain: float = 1.05
    """속도 1 m/s 당 앞을 내다보는 거리(m). 작을수록 선에 딱 붙지만 흔들린다."""

    min_lookahead: float = 2.6
    max_lookahead: float = 7.0
    reverse_lookahead: float = 1.9
    """후진할 때는 훨씬 짧게 본다. 실제로도 후진은 천천히 조금씩 본다."""

    cruise_speed: float = 3.6
    """통로 순항 속도(m/s). 약 13 km/h — **좁은 통로(6m) 기준의 기본값이다.**

    실제 시뮬레이션에서는 `for_lot()` 이 도면의 통로 폭에서 계산한 값을 쓴다.
    """

    reverse_speed: float = 0.85
    lateral_accel_limit: float = 2.6
    """코너에서 견디는 횡가속도(m/s²). 작을수록 코너를 얌전히 돈다."""

    stop_margin: float = 0.12
    """경로 끝에서 이만큼 남기고 멈춘다."""

    @staticmethod
    def for_lot(lot: LotMap) -> "DrivingSkill":
        """이 주차장의 통로 폭에 맞춘 운전 습관.

        넓은 통로에서는 빨리 달리고 멀리 본다. 도면을 바꿀 때 속도·예견거리를
        따로 손보지 않아도 되도록 여기 한 곳에서 계산한다.
        """
        widths = [a.width for a in lot.aisles]
        aisle = min(widths) if widths else 6.0
        cruise = aisle * CRUISE_PER_AISLE_METRE
        return DrivingSkill(
            cruise_speed=cruise,
            reverse_speed=max(0.85, cruise * REVERSE_SPEED_RATIO),
            max_lookahead=max(7.0, cruise * LOOKAHEAD_PER_SPEED),
        )


CRUISE_PER_AISLE_METRE = 0.60
"""통로 폭 1m 당 순항 속도(m/s).

**도면 치수를 바꿀 때 건드릴 값이 하나이도록** 속도를 통로 폭에서 계산한다.
폭 6m 통로에서 3.6 m/s(13 km/h) — 좁은 통로에서 검증된 값이고, 폭 14m 짜리
왕복 2차선급 통로에서는 8.4 m/s(30 km/h)가 된다.

넓은 길에서 사람이 더 빨리 달리는 것은 관찰된 사실이다. 통로만 넓히고 속도를
그대로 두면 화면이 이상하게 느려 보이고, 주차장이 커진 만큼 주차 소요 시간만 늘어난다.
"""

LOOKAHEAD_PER_SPEED = 1.5
"""순항 속도 1 m/s 당 최대 예견 거리(m). 빨리 달릴수록 멀리 봐야 선을 놓치지 않는다."""

REVERSE_SPEED_RATIO = 0.21
"""순항 속도 대비 후진 속도. 폭 6m 통로 기준 0.85 m/s 를 재현한다."""


@dataclass
class PathFollower:
    """경로 하나를 따라가는 상태를 들고 있는 추종기.

    진행 인덱스를 기억하는 이유: 경로가 스스로 교차하거나 U 자로 접힐 때
    매번 최근접점을 전역 탐색하면 엉뚱한 구간으로 건너뛴다.
    """

    spec: VehicleSpec
    skill: DrivingSkill = field(default_factory=DrivingSkill)
    gear: int = 1

    _path: tuple[Vec2, ...] = field(default=(), init=False)
    _cum: list[float] = field(default_factory=list, init=False)
    _idx: int = field(default=0, init=False)

    def set_path(self, path: Sequence[Vec2], gear: int = 1) -> None:
        self._path = tuple(path)
        self._cum = cumulative_lengths(self._path)
        self._idx = 0
        self.gear = gear

    @property
    def path_length(self) -> float:
        return self._cum[-1] if self._cum else 0.0

    def travelled(self, state: SelfState) -> float:
        """경로 시작점 기준으로 얼마나 진행했는지 (m)."""
        i, t = self._project(state.pose.position)
        return self._cum[i] + t * self._seg_len(i)

    def remaining(self, state: SelfState) -> float:
        return max(0.0, self.path_length - self.travelled(state))

    def is_finished(self, state: SelfState) -> bool:
        return self.remaining(state) <= self.skill.stop_margin + 0.05 and state.speed < 0.08

    def control(self, state: SelfState, speed_limit: float | None = None) -> ControlInput:
        """지금 상태에서 넣어야 할 조향과 가감속."""
        if len(self._path) < 2:
            return ControlInput(steer=0.0, accel=-self.spec.max_decel, gear=0)

        steer = self._pure_pursuit(state)
        accel = self._longitudinal(state, steer, speed_limit)
        return ControlInput(steer=steer, accel=accel, gear=self.gear)

    # ── 횡방향 ────────────────────────────────────────────────────

    def _pure_pursuit(self, state: SelfState) -> float:
        ld = self._lookahead(state.speed)
        target = self._point_ahead(state.pose.position, ld)

        to_target = target - state.pose.position
        if to_target.length < 1e-6:
            return 0.0

        # 후진할 때는 차가 뒤쪽으로 가므로 기준 방향을 뒤집고 조향 부호도 뒤집는다
        heading = state.pose.theta if self.gear >= 0 else state.pose.theta + math.pi
        alpha = angle_diff(to_target.angle, heading)

        delta = math.atan2(2.0 * self.spec.wheelbase * math.sin(alpha), max(ld, 0.5))
        if self.gear < 0:
            delta = -delta

        return max(-self.spec.max_steer, min(self.spec.max_steer, delta))

    def _lookahead(self, speed: float) -> float:
        s = self.skill
        if self.gear < 0:
            return s.reverse_lookahead
        return max(s.min_lookahead, min(s.max_lookahead, s.lookahead_gain * speed + s.min_lookahead * 0.6))

    # ── 종방향 ────────────────────────────────────────────────────

    def _longitudinal(self, state: SelfState, steer: float, speed_limit: float | None) -> float:
        s = self.skill
        target = s.reverse_speed if self.gear < 0 else s.cruise_speed
        if speed_limit is not None:
            target = min(target, speed_limit)

        # 코너 감속 — 지금 조향각이 만드는 반경에서 견딜 수 있는 속도
        r = self._turn_radius(steer)
        if math.isfinite(r):
            target = min(target, math.sqrt(s.lateral_accel_limit * r))

        # 정지선까지 필요한 감속을 직접 계산한다.
        #
        # 목표 속도를 0 으로 두고 비례 제어만 하면 속도가 줄수록 제동도 같이 약해져서
        # 지수적으로 느려질 뿐 멈추지 않는다. 실제로는 정지선을 늘 지나친다.
        # 등가속도 관계 a = v² / 2d 로 "지금 얼마나 밟아야 하는지"를 직접 구한다.
        d = self.remaining(state) - s.stop_margin
        if d <= 0.02:
            return -self.spec.max_decel

        needed = state.speed * state.speed / (2.0 * d)
        if needed > 0.30 * self.spec.max_decel:
            return -min(needed, self.spec.max_decel)

        err = target - state.speed
        accel = err * 2.2
        return max(-self.spec.max_decel, min(self.spec.max_accel, accel))

    def _turn_radius(self, steer: float) -> float:
        t = math.tan(abs(steer))
        return math.inf if t < 1e-6 else self.spec.wheelbase / t

    # ── 경로 위치 계산 ────────────────────────────────────────────

    def _seg_len(self, i: int) -> float:
        return self._cum[i + 1] - self._cum[i]

    def _project(self, p: Vec2) -> tuple[int, float]:
        """현재 위치를 경로 위에 투영. (구간 번호, 구간 내 비율)을 준다.

        기억해둔 진행 인덱스 주변만 본다 — 경로가 접혀 있어도 건너뛰지 않는다.
        """
        best = (self._idx, 0.0, math.inf)
        lo = max(0, self._idx - 2)
        hi = min(len(self._path) - 1, self._idx + 40)

        for i in range(lo, hi):
            a, b = self._path[i], self._path[i + 1]
            ab = b - a
            n2 = ab.dot(ab)
            t = 0.0 if n2 < 1e-12 else max(0.0, min(1.0, (p - a).dot(ab) / n2))
            d = (a + ab * t).distance_to(p)
            if d < best[2]:
                best = (i, t, d)

        self._idx = best[0]
        return best[0], best[1]

    def _point_ahead(self, p: Vec2, lookahead: float) -> Vec2:
        """현재 투영점에서 경로를 따라 lookahead 만큼 앞선 점."""
        i, t = self._project(p)
        target_s = self._cum[i] + t * self._seg_len(i) + lookahead

        # 경로 끝을 넘어서면 마지막 점에 붙잡아 두지 않고, 끝 방향으로 연장한 가상의
        # 점을 본다. 마지막 점에 고정하면 위치는 맞아도 **방향이 수렴하지 않는다** —
        # 후진 주차에서 차가 비뚤어진 채로 서는 원인이 바로 이것이다.
        if target_s >= self._cum[-1]:
            last = self._path[-1]
            tangent = (last - self._path[-2]).normalized()
            return last + tangent * (target_s - self._cum[-1])

        j = i
        while j < len(self._path) - 2 and self._cum[j + 1] < target_s:
            j += 1
        span = self._seg_len(j)
        u = 0.0 if span < 1e-12 else (target_s - self._cum[j]) / span
        return self._path[j] + (self._path[j + 1] - self._path[j]) * u
