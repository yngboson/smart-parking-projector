"""2D 기하 유틸.

좌표계: +x = 동쪽, +y = 북쪽 (수학 표준). 각도는 라디안이며 0 = +x 방향,
반시계 방향이 양수. 단위는 전부 미터.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

TAU = math.tau


@dataclass(frozen=True, slots=True)
class Vec2:
    x: float
    y: float

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, k: float) -> "Vec2":
        return Vec2(self.x * k, self.y * k)

    __rmul__ = __mul__

    def __truediv__(self, k: float) -> "Vec2":
        return Vec2(self.x / k, self.y / k)

    def dot(self, other: "Vec2") -> float:
        return self.x * other.x + self.y * other.y

    def cross(self, other: "Vec2") -> float:
        return self.x * other.y - self.y * other.x

    @property
    def length(self) -> float:
        return math.hypot(self.x, self.y)

    @property
    def angle(self) -> float:
        return math.atan2(self.y, self.x)

    def normalized(self) -> "Vec2":
        n = self.length
        return Vec2(0.0, 0.0) if n == 0.0 else Vec2(self.x / n, self.y / n)

    def rotated(self, theta: float) -> "Vec2":
        c, s = math.cos(theta), math.sin(theta)
        return Vec2(self.x * c - self.y * s, self.x * s + self.y * c)

    def distance_to(self, other: "Vec2") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    @staticmethod
    def from_angle(theta: float, length: float = 1.0) -> "Vec2":
        return Vec2(math.cos(theta) * length, math.sin(theta) * length)


@dataclass(frozen=True, slots=True)
class Pose:
    """위치 + 진행 방향."""

    x: float
    y: float
    theta: float

    @property
    def position(self) -> Vec2:
        return Vec2(self.x, self.y)

    @property
    def forward(self) -> Vec2:
        return Vec2.from_angle(self.theta)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.theta)


def wrap_angle(a: float) -> float:
    """각도를 (-pi, pi] 로 정규화."""
    a = (a + math.pi) % TAU - math.pi
    return math.pi if a == -math.pi else a


def angle_diff(target: float, source: float) -> float:
    """source 에서 target 까지의 최소 회전량 (부호 있음)."""
    return wrap_angle(target - source)


def polyline_length(points: Sequence[Vec2]) -> float:
    return sum(points[i].distance_to(points[i + 1]) for i in range(len(points) - 1))


def cumulative_lengths(points: Sequence[Vec2]) -> list[float]:
    """각 점까지의 누적 거리. 길이는 points 와 같고 첫 값은 0."""
    out = [0.0]
    for i in range(len(points) - 1):
        out.append(out[-1] + points[i].distance_to(points[i + 1]))
    return out


def resample_polyline(points: Sequence[Vec2], spacing: float) -> list[Vec2]:
    """폴리라인을 일정 간격으로 다시 샘플링. 시작점과 끝점은 항상 보존한다.

    유도선 리본 메시를 만들 때 정점 간격이 균일해야 쉐브론 패턴이 일그러지지 않는다.
    """
    if len(points) < 2 or spacing <= 0:
        return list(points)

    cum = cumulative_lengths(points)
    total = cum[-1]
    if total == 0.0:
        return [points[0]]

    out: list[Vec2] = []
    n = max(1, int(round(total / spacing)))
    seg = 0
    for i in range(n + 1):
        target = total * i / n
        while seg < len(cum) - 2 and cum[seg + 1] < target:
            seg += 1
        span = cum[seg + 1] - cum[seg]
        t = 0.0 if span == 0.0 else (target - cum[seg]) / span
        out.append(points[seg] + (points[seg + 1] - points[seg]) * t)
    return out


def point_at_distance(points: Sequence[Vec2], distance: float) -> Vec2:
    """폴리라인 시작점에서 주어진 거리만큼 진행한 지점."""
    cum = cumulative_lengths(points)
    if distance <= 0.0:
        return points[0]
    if distance >= cum[-1]:
        return points[-1]
    for i in range(len(cum) - 1):
        if cum[i + 1] >= distance:
            span = cum[i + 1] - cum[i]
            t = 0.0 if span == 0.0 else (distance - cum[i]) / span
            return points[i] + (points[i + 1] - points[i]) * t
    return points[-1]


def turn_angle_total(points: Sequence[Vec2]) -> float:
    """경로 전체의 회전량 절대값 합. 비용 함수의 '회전수' 항에 쓴다."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i in range(len(points) - 2):
        a = (points[i + 1] - points[i]).angle
        b = (points[i + 2] - points[i + 1]).angle
        total += abs(angle_diff(b, a))
    return total


MITER_LIMIT = 4.0
"""꼭짓점을 밀어낼 때 허용하는 최대 배율. 되돌아가는 각에서 발산하는 것을 막는다."""


def offset_polyline(points: Sequence[Vec2], offset: float) -> list[Vec2]:
    """폴리라인을 진행 방향 기준 오른쪽으로 민다. 음수면 왼쪽.

    꼭짓점에서는 이등분선 방향으로 밀되 **1/cos(반각) 만큼 더 밀어낸다**. 이 배율이
    없으면 밀어낸 선이 원래 코너를 통과하지 못하고 안쪽으로 잘려, 직각 코너가
    비스듬한 대각선 한 구간으로 변한다. 통로 폭이 넓을수록 그 대각선이 길어진다 —
    14m 통로에서 13m 짜리 사선이 생겼다 (D-023).

    **세 계층이 같은 함수를 쓴다.** 관제는 이걸로 바닥에 그릴 유도선을 만들고,
    운전자는 안내가 없을 때 스스로 주행 차선을 잡는다. 둘이 다른 계산을 쓰면
    바닥의 선과 차가 가는 길이 어긋난다 — 실제로 어긋났다.
    """
    pts = list(points)
    if len(pts) < 2 or offset == 0.0:
        return pts

    out: list[Vec2] = []
    last = len(pts) - 1
    for i, p in enumerate(pts):
        scale = 1.0
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
            else:
                cos_half = d.dot(a)
                scale = min(MITER_LIMIT, 1.0 / cos_half) if cos_half > 1e-6 else MITER_LIMIT
        out.append(p + Vec2(d.y, -d.x) * (offset * scale))
    return out


def rect_corners(center: Vec2, heading: float, length: float, width: float) -> list[Vec2]:
    """직사각형(차량/주차면)의 네 꼭짓점. 충돌 판정과 렌더링에 공용."""
    fwd = Vec2.from_angle(heading)
    left = Vec2.from_angle(heading + math.pi / 2)
    hl, hw = length / 2.0, width / 2.0
    return [
        center + fwd * hl + left * hw,
        center + fwd * hl - left * hw,
        center - fwd * hl - left * hw,
        center - fwd * hl + left * hw,
    ]


def centroid(points: Iterable[Vec2]) -> Vec2:
    pts = list(points)
    if not pts:
        return Vec2(0.0, 0.0)
    return Vec2(sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts))
