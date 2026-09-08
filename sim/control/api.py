"""관제 시스템의 외부 계약 — 월드가 관제를 부르는 **유일한** 통로.

    센서 이벤트를 넣으면 프로젝터 명령이 나온다.

이 함수 시그니처가 계층 분리의 실체다. 들어가는 것은 실제 하드웨어가 관측할 수
있는 값뿐이고(`SensorEvent`), 나오는 것은 실제 하드웨어가 실행할 수 있는 지시뿐이다
(`ProjectorCommand`). 차량 객체도, 운전자 성향도, 시뮬레이터의 정답도 오가지 않는다.

관제 구현을 통째로 갈아끼워도(예: 무안내 베이스라인, D-010) 월드는 아무것도 모른다.
그래서 동일 시드로 공정하게 비교할 수 있다.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from sim.common.messages import ProjectorCommand, SensorEvent


@runtime_checkable
class ControlSystem(Protocol):
    """관제 알고리즘 하나."""

    def on_events(self, events: Sequence[SensorEvent]) -> list[ProjectorCommand]:
        """한 틱 분량의 센서 관측을 받아 유도선 지시를 만든다.

        구현체는 이 호출 안에서만 세계 모델을 갱신해야 한다. 다른 경로로 상태를
        받아오는 순간 실험이 무너진다.
        """
        ...


class NullControl:
    """아무 안내도 하지 않는 관제 — 베이스라인(무안내 모드, D-010)의 뼈대.

    운전자가 육안으로만 자리를 찾는 상황을 만들기 위한 것이다. 유도선이 없어도
    시뮬레이션은 동일한 배선으로 돌아가야 하므로, '아무것도 안 하는 구현'이
    처음부터 있어야 한다.
    """

    name = "none"

    def on_events(self, events: Sequence[SensorEvent]) -> list[ProjectorCommand]:
        return []
