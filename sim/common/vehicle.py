"""차량 제원과 제어 입력.

이 타입들은 세 계층이 모두 알아야 한다:
    agents  — 운전자가 자기 차의 회전반경을 알고 조향을 결정한다
    world   — 물리 적분에 쓴다
    control — 차종별 크기 제약(대형차가 못 들어가는 주차면)을 판단한다

따라서 common 에 둔다. 다만 여기 들어가는 것은 **제원과 제어값뿐**이다.
운전자의 성향(compliance)은 이 파일에 절대 등장하면 안 된다 — 그 순간 관제가
읽을 수 있게 되고 실험이 무너진다 (docs/DECISIONS.md D-001).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sim.common.geometry import Pose, Vec2, rect_corners
from sim.common.ids import VehicleClass


@dataclass(frozen=True, slots=True)
class VehicleSpec:
    """차량 제원. 기본값은 국내 중형 세단(쏘나타급) 기준."""

    length: float = 4.70
    width: float = 1.85
    wheelbase: float = 2.70
    rear_overhang: float = 0.90
    """뒷축에서 차량 뒤끝까지의 거리."""

    max_steer: float = 0.56
    """최대 조향각(rad). 약 32°."""

    max_steer_rate: float = 1.10
    """조향 각속도 한계(rad/s). 운전자가 핸들을 돌리는 속도."""

    max_speed: float = 4.20
    """통로 주행 최고 속도(m/s). 약 15 km/h."""

    max_reverse_speed: float = 1.20
    max_accel: float = 1.60
    max_decel: float = 3.20

    @property
    def min_turn_radius(self) -> float:
        """최소 회전반경(m). 뒷축 중심 기준."""
        return self.wheelbase / math.tan(self.max_steer)

    @property
    def rear_axle_to_center(self) -> float:
        """뒷축에서 차체 중심까지의 거리. 주차면 정렬에 쓴다."""
        return self.length / 2.0 - self.rear_overhang

    def speed_limit(self, gear: int) -> float:
        return self.max_speed if gear >= 0 else self.max_reverse_speed

    @staticmethod
    def of(vehicle_class: VehicleClass) -> "VehicleSpec":
        """차종 등급에 맞는 제원. 축거는 전장에 비례한다고 본다."""
        length, width = vehicle_class.footprint
        scale = length / 4.70
        return VehicleSpec(
            length=length,
            width=width,
            wheelbase=2.70 * scale,
            rear_overhang=0.90 * scale,
            max_steer=0.56 if length < 5.0 else 0.52,
        )


@dataclass(frozen=True, slots=True)
class ControlInput:
    """운전자가 차에 넣는 값. agents → world 로 건너가는 유일한 제어 신호다."""

    steer: float
    """목표 조향각(rad). 실제 조향은 max_steer_rate 로 제한되어 서서히 따라간다."""

    accel: float
    """가감속(m/s²). 부호는 **진행 방향 기준** — 후진 중 양수면 더 빨리 후진한다."""

    gear: int = 1
    """+1 전진, -1 후진, 0 정차."""


@dataclass(frozen=True, slots=True)
class SelfState:
    """운전자가 아는 자기 차의 상태.

    실제 운전자도 자기 위치·속도·조향은 안다. 남의 차 상태나 관제의 계획은 모른다.
    """

    pose: Pose
    """뒷축 중심의 위치와 방향. 자전거 모델의 기준점이다."""

    speed: float
    """진행 방향 속력(m/s). 항상 0 이상이며 방향은 gear 가 결정한다."""

    steer: float
    gear: int

    @property
    def velocity(self) -> float:
        """부호 있는 속도. 후진이면 음수."""
        return self.velocity_with(self.gear)

    def velocity_with(self, gear: int) -> float:
        """주어진 기어에서의 부호 있는 속도. 적분 중 기어가 바뀌는 순간에 쓴다."""
        return self.speed * (1 if gear >= 0 else -1)


def body_center(pose: Pose, spec: VehicleSpec) -> Vec2:
    """뒷축 기준 자세로부터 차체 중심 좌표를 구한다."""
    return pose.position + Vec2.from_angle(pose.theta) * spec.rear_axle_to_center


def footprint(pose: Pose, spec: VehicleSpec) -> list[Vec2]:
    """차량이 바닥에서 차지하는 사각형의 네 꼭짓점. 충돌 판정과 주차 검사에 쓴다."""
    return rect_corners(body_center(pose, spec), pose.theta, spec.length, spec.width)
