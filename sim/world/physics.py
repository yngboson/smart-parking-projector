"""차량 운동 — 운동학적 자전거 모델.

기준점은 **뒷축 중심**이다. 이렇게 두면 조향각과 회전반경의 관계가
R = wheelbase / tan(δ) 로 깔끔해지고, 후진 주차 궤적 계산도 단순해진다.

    ẋ = v·cos θ
    ẏ = v·sin θ
    θ̇ = (v / L)·tan δ

동역학(타이어 슬립, 하중 이동)은 넣지 않는다. 주차장 속도(15 km/h 이하)에서는
차이가 무시할 만하고, 이 연구의 질문은 "관제가 돌발에 대처하는가"이지
"차가 어떻게 미끄러지는가"가 아니다.

대신 **실제 차가 못 하는 일은 못 하게** 막는다:
  - 조향각과 조향 각속도 한계 (핸들을 순간이동시킬 수 없다)
  - 가감속 한계
  - 정지 상태에서만 기어 변경 (달리면서 후진으로 못 넣는다)

이 제약이 있어야 "유도선이 실제로 주행 가능한 선인가"를 검증할 수 있다.
"""

from __future__ import annotations

import math

from sim.common.geometry import Pose, Vec2, wrap_angle
from sim.common.vehicle import ControlInput, SelfState, VehicleSpec

GEAR_CHANGE_SPEED = 0.05
"""이 속력 아래에서만 기어를 바꿀 수 있다 (m/s)."""


def step(state: SelfState, spec: VehicleSpec, cmd: ControlInput, dt: float) -> SelfState:
    """제어 입력을 한 스텝 적분해 새 상태를 만든다.

    적분은 중점법을 쓴다. 오일러법으로는 원호 주행에서 반경이 눈에 띄게 커진다.
    """
    if dt <= 0:
        return state

    gear = _resolve_gear(state, cmd)

    # ── 조향: 각속도 한계 안에서 목표각을 향해 움직인다 ──
    target_steer = _clamp(cmd.steer, -spec.max_steer, spec.max_steer)
    max_delta = spec.max_steer_rate * dt
    steer = state.steer + _clamp(target_steer - state.steer, -max_delta, max_delta)
    steer = _clamp(steer, -spec.max_steer, spec.max_steer)

    # ── 종방향 ──
    accel = _clamp(cmd.accel, -spec.max_decel, spec.max_accel)
    speed = state.speed + accel * dt
    speed = _clamp(speed, 0.0, spec.speed_limit(gear))

    if gear == 0:
        speed = 0.0

    # ── 자세 적분 (중점법) ──
    v_signed = speed * (1 if gear >= 0 else -1)
    v_mid = (state.velocity_with(gear) + v_signed) / 2.0

    if abs(v_mid) < 1e-12:
        pose = state.pose
    else:
        omega = v_mid / spec.wheelbase * math.tan(steer)
        theta_mid = state.pose.theta + omega * dt / 2.0
        pose = Pose(
            state.pose.x + v_mid * math.cos(theta_mid) * dt,
            state.pose.y + v_mid * math.sin(theta_mid) * dt,
            wrap_angle(state.pose.theta + omega * dt),
        )

    return SelfState(pose=pose, speed=speed, steer=steer, gear=gear)


def _resolve_gear(state: SelfState, cmd: ControlInput) -> int:
    """기어 변경 요청을 검사한다. 달리는 중에는 바꿀 수 없다."""
    if cmd.gear == state.gear:
        return state.gear
    if state.speed <= GEAR_CHANGE_SPEED:
        return cmd.gear
    return state.gear


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def turn_radius(spec: VehicleSpec, steer: float) -> float:
    """주어진 조향각에서의 회전반경. 직진이면 무한대."""
    t = math.tan(abs(steer))
    return math.inf if t < 1e-9 else spec.wheelbase / t


def stopping_distance(spec: VehicleSpec, speed: float) -> float:
    """지금 속도에서 최대 감속으로 멈추기까지의 거리."""
    return speed * speed / (2.0 * spec.max_decel)


def gap_to(front: Vec2, behind: SelfState, spec: VehicleSpec) -> float:
    """앞차까지의 세로 방향 여유 거리. 뒤에 있으면 큰 값을 준다."""
    d = front - behind.pose.position
    along = d.x * math.cos(behind.pose.theta) + d.y * math.sin(behind.pose.theta)
    lateral = abs(-d.x * math.sin(behind.pose.theta) + d.y * math.cos(behind.pose.theta))
    if along <= 0 or lateral > spec.width * 1.1:
        return math.inf
    return along - spec.length
