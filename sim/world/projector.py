"""빔 프로젝터 — 바닥에 그려진 유도선의 현재 상태.

관제가 "이 차의 선을 이렇게 그려라"고 지시하면 여기에 반영된다. 하드웨어로 치면
프로젝터에 보낸 프레임 버퍼이고, 시뮬레이션에서는 두 가지 역할을 한다:

  1. 운전자가 보는 것 — `view(plate)` 가 그 차의 `GuidanceView` 를 준다.
     **자기 색 선만 준다.** 다른 차의 유도선은 실제로도 자기 것이 아니면
     따라갈 이유가 없고, 넘겨주는 순간 운전자가 전지적 시점을 얻는다.
  2. 뷰어가 그리는 것 — `beams()` 가 프레임에 실릴 선 전부를 준다.

**지나온 구간을 지우는 일**도 여기서 한다 (docs/DECISIONS.md D-006). 차량 위치를
폴리라인에 투영해 진행률을 갱신하면, 뷰어의 셰이더가 그만큼을 잘라낸다.
바닥에 남는 선의 총량이 줄어야 여러 대가 동시에 안내받아도 화면이 읽힌다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from sim.common.geometry import Vec2, cumulative_lengths
from sim.common.ids import PlateId, SlotId
from sim.common.messages import (
    ClearGuidance,
    GuidanceCommand,
    GuidanceReason,
    GuidanceView,
    ProjectorCommand,
)


@dataclass(slots=True)
class Beam:
    """바닥에 그려져 있는 유도선 하나."""

    plate: PlateId
    target_slot: SlotId
    polyline: tuple[Vec2, ...]
    revision: int
    reason: GuidanceReason
    issued_t: float = 0.0
    progress: float = 0.0
    """0~1. 이 비율만큼은 이미 지나갔으므로 그리지 않는다."""

    _cum: list[float] = field(default_factory=list, repr=False)
    _idx: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        self._cum = cumulative_lengths(self.polyline)

    @property
    def length(self) -> float:
        return self._cum[-1] if self._cum else 0.0

    def view(self) -> GuidanceView:
        return GuidanceView(
            target_slot=self.target_slot,
            polyline=self.polyline,
            revision=self.revision,
        )

    def advance(self, position: Vec2) -> float:
        """차량 위치를 선에 투영해 진행률을 갱신한다.

        기억해 둔 구간부터 앞쪽만 본다. 경로가 접히는 곳에서 뒤로 튀지 않게 하려는
        것이고, 진행률은 한 번 올라가면 내려오지 않는다 — 지웠던 선이 다시 나타나면
        운전자가 혼란스럽다.
        """
        total = self.length
        if total <= 0.0 or len(self.polyline) < 2:
            return self.progress

        best_s, best_d, best_i = None, float("inf"), self._idx
        hi = min(len(self.polyline) - 1, self._idx + 40)
        for i in range(self._idx, hi):
            a, b = self.polyline[i], self.polyline[i + 1]
            ab = b - a
            n2 = ab.dot(ab)
            t = 0.0 if n2 < 1e-12 else max(0.0, min(1.0, (position - a).dot(ab) / n2))
            d = (a + ab * t).distance_to(position)
            if d < best_d:
                best_d, best_i = d, i
                best_s = self._cum[i] + t * (self._cum[i + 1] - self._cum[i])

        if best_s is not None:
            self._idx = best_i
            self.progress = max(self.progress, min(1.0, best_s / total))
        return self.progress


class Projector:
    """지금 바닥에 떠 있는 유도선 전부."""

    def __init__(self) -> None:
        self._beams: dict[PlateId, Beam] = {}

    def apply(self, commands: Sequence[ProjectorCommand], t: float = 0.0) -> None:
        for cmd in commands:
            if isinstance(cmd, GuidanceCommand):
                self._beams[cmd.plate] = Beam(
                    plate=cmd.plate,
                    target_slot=cmd.target_slot,
                    polyline=tuple(cmd.polyline),
                    revision=cmd.revision,
                    reason=cmd.reason,
                    issued_t=t,
                )
            elif isinstance(cmd, ClearGuidance):
                self._beams.pop(cmd.plate, None)

    def advance(self, plate: PlateId, position: Vec2) -> None:
        beam = self._beams.get(plate)
        if beam is not None:
            beam.advance(position)

    def view(self, plate: PlateId) -> GuidanceView | None:
        """그 운전자가 바닥에서 보는 자기 색 선. 없으면 None."""
        beam = self._beams.get(plate)
        return None if beam is None else beam.view()

    def beams(self) -> list[Beam]:
        return list(self._beams.values())

    def __len__(self) -> int:
        return len(self._beams)

    def __contains__(self, plate: object) -> bool:
        return plate in self._beams
