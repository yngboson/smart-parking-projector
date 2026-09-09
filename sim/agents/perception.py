"""운전자의 시야 — 무엇이 눈에 보이는가.

**이 파일은 규칙만 정한다.** 실제로 무엇이 보이는지 채우는 것은 월드다
(월드만이 진실을 알고 있으므로). 여기 있는 것은 "사람이 운전석에서 볼 수 있는
범위는 이렇다"는 판단 기준이고, 그것은 운전자의 성질이므로 agents 계층에 있다.

이 구분이 왜 중요한가. 강탈은 **운전자가 빈 자리를 눈으로 발견해서** 일어난다.
시야 규칙이 월드에 있으면 "시뮬레이터가 정한 강탈"이 되고, 여기 있으면
"운전자가 하는 강탈"이 된다. 후자여야 실험이 성립한다.

시야 규칙 (docs/PLAN.md 7):
  - 반경 25m 이내
  - 전방 시야각 안 (뒤통수에 눈은 없다)
  - 지금 달리는 통로에 접한 주차면만 — 두 줄 건너 자리는 차에 가려 안 보인다

이 파일은 ``sim.common`` 외에는 아무것도 import 하지 않는다 (CLAUDE.md).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sim.common.geometry import Pose, Vec2, angle_diff
from sim.common.lotmap import LotMap


@dataclass(frozen=True, slots=True)
class VisionModel:
    """운전석에서 주차면을 알아볼 수 있는 범위."""

    radius: float = 25.0
    """이 거리를 넘으면 비었는지 찼는지 구분이 안 된다."""

    half_angle: float = 1.40
    """전방 시야 반각(rad). 약 80°. 사람의 유효 시야보다 좁게 잡았다 —
    운전 중에는 전방을 보지 주차면을 두리번거리지 않는다."""

    lateral_reach: float = 8.0
    """통로 중심선에서 옆으로 이만큼까지만 본다(m).

    지금 달리는 통로에 **접한 한 줄**은 들어오고, 그 너머는 빠진다. 건너편 두 번째
    줄은 주차된 차들에 가려 실제로도 보이지 않는다.

    **도면 치수를 바꾸면 이 값도 따라가야 한다.** 고정값으로 두면 통로가 넓어진
    순간 바로 옆 주차면조차 시야 밖이 되어, 비협조 운전자가 이탈할 대상을 아예
    못 보게 된다. `for_lot()` 을 쓰면 도면에서 계산된다."""

    behind_tolerance: float = 2.0
    """살짝 지나친 자리까지는 곁눈으로 본 것으로 친다(m)."""

    @staticmethod
    def for_lot(lot: "LotMap") -> "VisionModel":
        """이 주차장의 치수에 맞춘 시야.

        통로 중심선에서 접한 주차면 중심까지의 거리는 `통로 폭/2 + 주차면 길이/2`
        다. 거기에 반 칸쯤 여유를 두면 접한 줄은 들어오고 건너편 줄은 빠진다.
        """
        aisles = [a.width for a in lot.aisles]
        slots = [s.length for s in lot.slots.values()]
        if not aisles or not slots:
            return VisionModel()

        reach = max(aisles) / 2.0 + max(slots) / 2.0 + max(slots) * 0.25
        return VisionModel(lateral_reach=reach)

    def can_see(self, observer: Pose, target: Vec2) -> bool:
        """관측자의 자세에서 그 지점을 알아볼 수 있는가."""
        d = target - observer.position
        dist = d.length
        if dist > self.radius:
            return False
        if dist < 1e-6:
            return True

        forward = d.x * math.cos(observer.theta) + d.y * math.sin(observer.theta)
        lateral = -d.x * math.sin(observer.theta) + d.y * math.cos(observer.theta)

        if forward < -self.behind_tolerance:
            return False
        if abs(lateral) > self.lateral_reach:
            return False
        return abs(angle_diff(d.angle, observer.theta)) <= self.half_angle
