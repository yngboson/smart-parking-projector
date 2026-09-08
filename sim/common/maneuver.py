"""주차 진입 궤적 기하.

수직 주차면에 **후진으로** 들어가는 궤적을 만든다. 국내 주차장에서 후진 주차가
일반적이기도 하고, 전진으로 들어가면 나올 때 통로로 후진해야 해서 훨씬 위험하다.

궤적을 만드는 방법은 뒤집어서 생각하는 것이다:

    주차를 마친 자세에서 **전진으로 빠져나오는** 경로를 그린 다음 순서를 뒤집는다.

자전거 모델은 전·후진이 대칭이므로, 전진으로 나올 수 있는 길이면 후진으로 들어갈
수도 있다. 직접 후진 궤적을 풀면 비선형 문제가 되지만 이렇게 하면 원호 하나로 끝난다.

    ┌─────┐ 주차면
    │  ▲  │        ① 주차 자세에서 직진으로 빠져나옴 (exit_clearance)
    └──┼──┘        ② 90° 원호로 통로 방향에 정렬
       │  ╲        ③ 통로를 따라 조금 더 (aisle_run)
    ───┴───╲───────────────  통로
            ● staging  ← 차량은 여기까지 전진해 정차한 뒤, 이 경로를 거꾸로 후진한다

이 파일은 순수 기하다. 정책(어느 주차면을 고를지)도, 제어(어떻게 따라갈지)도 없다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sim.common.geometry import Pose, Vec2, polyline_length, wrap_angle


@dataclass(frozen=True, slots=True)
class ParkingManeuver:
    """후진 주차 한 번에 필요한 모든 것."""

    staging: Pose
    """후진을 시작하기 위해 차량이 전진해 정차하는 지점. 주차면을 지나친 위치다."""

    reverse_path: tuple[Vec2, ...]
    """staging 에서 주차 완료 지점까지, 차량이 **후진으로** 따라갈 경로."""

    final: Pose
    """주차를 마쳤을 때 뒷축 중심의 자세. 차량 앞머리는 통로를 향한다."""

    turn_radius: float

    @property
    def reverse_length(self) -> float:
        return polyline_length(self.reverse_path)


def arc_poses(start: Pose, radius: float, turn: float, step: float = 0.25) -> list[Pose]:
    """start 에서 시작해 radius 로 turn(rad)만큼 도는 원호 위의 자세들.

    turn 이 양수면 좌회전. 시작점은 포함하지 않는다.
    """
    if abs(turn) < 1e-9 or radius <= 0:
        return []

    sign = 1.0 if turn > 0 else -1.0
    span = abs(turn)

    # 회전 중심은 진행 방향의 옆쪽 radius 만큼 떨어진 곳
    cx = start.x + radius * math.cos(start.theta + sign * math.pi / 2)
    cy = start.y + radius * math.sin(start.theta + sign * math.pi / 2)
    a0 = math.atan2(start.y - cy, start.x - cx)

    n = max(2, int(math.ceil(span * radius / step)))
    out: list[Pose] = []
    for i in range(1, n + 1):
        t = span * i / n
        a = a0 + sign * t
        out.append(
            Pose(
                cx + radius * math.cos(a),
                cy + radius * math.sin(a),
                wrap_angle(start.theta + sign * t),
            )
        )
    return out


def line_poses(start: Pose, distance: float, step: float = 0.25) -> list[Pose]:
    """start 에서 진행 방향으로 distance 만큼 직진. 시작점은 포함하지 않는다."""
    if distance <= 0:
        return []
    n = max(1, int(math.ceil(distance / step)))
    fwd = Vec2.from_angle(start.theta)
    return [
        Pose(start.x + fwd.x * distance * i / n, start.y + fwd.y * distance * i / n, start.theta)
        for i in range(1, n + 1)
    ]


def parked_pose(slot_center: Vec2, slot_heading: float, rear_axle_to_center: float) -> Pose:
    """주차를 마친 차량의 뒷축 자세.

    차체 중심이 주차면 중심에 오도록 뒷축을 뒤로 물린다.
    slot_heading 은 차량 앞머리가 향하는 방향(= 통로 쪽)이다.
    """
    back = Vec2.from_angle(slot_heading) * rear_axle_to_center
    return Pose(slot_center.x - back.x, slot_center.y - back.y, slot_heading)


def plan_reverse_parking(
    slot_center: Vec2,
    slot_heading: float,
    approach_heading: float,
    rear_axle_to_center: float,
    turn_radius: float,
    exit_clearance: float = 1.30,
    aisle_run: float = 2.20,
    step: float = 0.25,
) -> ParkingManeuver:
    """수직 주차면에 후진으로 진입하는 궤적을 만든다.

    :param slot_heading: 주차 완료 시 차량 앞머리 방향 (통로 쪽)
    :param approach_heading: 차량이 통로를 달려오는 방향
    :param turn_radius: 사용할 회전반경. 차량의 최소 회전반경 이상이어야 한다
    :param exit_clearance: 주차면에서 곧장 빠져나오는 직선 구간. 옆 차를 긁지 않게 한다
    :param aisle_run: 원호를 마친 뒤 통로를 따라 더 가는 거리. 정차 여유
    """
    final = parked_pose(slot_center, slot_heading, rear_axle_to_center)

    # 주차 자세에서 전진으로 빠져나오는 경로를 만든 뒤 뒤집는다
    forward: list[Pose] = [final]
    forward += line_poses(final, exit_clearance, step)

    turn = wrap_angle(approach_heading - slot_heading)
    forward += arc_poses(forward[-1], turn_radius, turn, step)
    forward += line_poses(forward[-1], aisle_run, step)

    staging = forward[-1]
    reverse_path = tuple(Vec2(p.x, p.y) for p in reversed(forward))

    return ParkingManeuver(
        staging=staging,
        reverse_path=reverse_path,
        final=final,
        turn_radius=turn_radius,
    )


def slot_contains(
    corners: list[Vec2], slot_center: Vec2, slot_heading: float,
    slot_length: float, slot_width: float, margin: float = 0.0,
) -> bool:
    """차량의 네 꼭짓점이 주차면 안에 들어갔는지.

    주차면 좌표계로 옮겨서 축 정렬 사각형 안에 있는지만 보면 된다.
    """
    ct = math.cos(-slot_heading)
    st = math.sin(-slot_heading)
    hl = slot_length / 2.0 + margin
    hw = slot_width / 2.0 + margin

    for c in corners:
        dx = c.x - slot_center.x
        dy = c.y - slot_center.y
        u = dx * ct - dy * st          # 주차면 길이 방향
        v = dx * st + dy * ct          # 주차면 폭 방향
        if abs(u) > hl or abs(v) > hw:
            return False
    return True
