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

from typing import Iterable, Sequence

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


LANE_MARGIN = 0.35
"""내 진행 통로의 좌우 여유(m). 이 안에 걸치는 물체만 나를 막는다."""

MAX_LOOK = 22.0
"""이 거리 너머는 보지 않는다(m). 주차장 통로 길이를 생각하면 충분하다."""

STRAIGHT_STEER = 0.02
"""이 조향각(rad) 아래는 직진으로 본다."""


def forward_clearance(
    behind: SelfState,
    spec: VehicleSpec,
    obstacles: Sequence[Sequence[Vec2]],
    centres: Sequence[Vec2] | None = None,
) -> tuple[float, int]:
    """(내 앞범퍼에서 앞차까지 남은 거리, 그 앞차의 번호). 없으면 (inf, -1).

    **누가 앞차인지도 함께 돌려준다.** 거리만으로는 얼마나 떨어져 따라갈지 정할 수
    없다 — 앞차가 같은 속도로 달리는 중인지, 주차하느라 한동안 서 있을 차인지에
    따라 필요한 간격이 다르다 (`agents.driver.Driver._speed_limit`).

    두 가지를 제대로 봐야 값이 쓸모 있다.

    **앞차의 차체 꼭짓점 전부를 본다.** 중심점 하나만 보면 통로를 가로지르는 차를
    놓친다 — 후진 주차 중인 차는 90° 를 도는 동안 중심이 옆으로 빠져 있어서
    "앞에 아무도 없다"로 읽히고, 뒤차가 그대로 밀고 들어간다.

    **지금 조향각이 만드는 원호를 따라 본다.** 직선으로만 재면 좌회전 중인 차가
    회전 바깥쪽에 있는 차를 정면의 장애물로 오인한다. 실제로는 그 차를 스쳐 지나갈
    뿐인데 급정거하고, 그 뒤로 통로 전체가 굳는다. 차는 직진하지 않는 순간에도
    자기가 갈 곳을 알고 있다 — 그 곡률이 `SelfState.steer` 에 들어 있다.

    이 함수는 **재기만 한다.** 이 값을 보고 속도를 줄이는 것은 운전자의 일이다
    (`sim.agents.driver.Driver._speed_limit`). 월드가 차를 강제로 세우면
    "앞차를 보고 멈추는 것"이 사람의 행동이 아니라 시뮬레이터의 기능이 되어버린다.
    """
    ct = math.cos(behind.pose.theta)
    st = math.sin(behind.pose.theta)
    ox, oy = behind.pose.position.x, behind.pose.position.y
    half = spec.width / 2.0 + LANE_MARGIN
    nose = spec.length - spec.rear_overhang

    radius = (
        None
        if abs(behind.steer) < STRAIGHT_STEER
        else spec.wheelbase / math.tan(behind.steer)
    )

    # 멀리 있는 차는 꼭짓점 네 개를 변환하기 전에 거리 하나로 걸러낸다.
    # 주차장이 커지면 대부분의 차가 서로 무관한데, 그걸 매번 전부 계산하면
    # 차량 수의 제곱으로 비용이 늘어난다.
    cutoff = (MAX_LOOK + spec.length) ** 2

    nearest = MAX_LOOK
    leader = -1
    for i, corners in enumerate(obstacles):
        if centres is not None:
            c0 = centres[i]
            if (c0.x - ox) ** 2 + (c0.y - oy) ** 2 > cutoff:
                continue
        for c in corners:
            dx, dy = c.x - ox, c.y - oy
            u = dx * ct + dy * st            # 진행 방향
            w = -dx * st + dy * ct           # 왼쪽이 양수
            d = _path_distance(u, w, radius, half)
            if d is not None and d < nearest:
                nearest = d
                leader = i

    if nearest >= MAX_LOOK:
        return math.inf, -1
    return nearest - nose, leader


def _path_distance(u: float, w: float, radius: float | None, half: float) -> float | None:
    """내 진행 궤적을 따라 그 점까지 가는 거리. 궤적 폭을 벗어나면 None."""
    if radius is None:
        if u <= 0.0 or abs(w) > half:
            return None
        return u

    # 회전 중심은 차의 왼쪽(좌회전) 또는 오른쪽(우회전)으로 radius 만큼.
    reach = math.hypot(u, w - radius)
    if abs(reach - abs(radius)) > half:
        return None

    phi = math.atan2(u, radius - w) if radius > 0 else math.atan2(u, w - radius)
    # atan2 로 얻은 각을 진행 방향 기준 [0, 2pi) 로 편다.
    turned = phi % (2.0 * math.pi)
    return abs(radius) * turned
