"""식별자와 열거형.

문자열 그대로 쓰면 SlotId 자리에 NodeId 를 넣는 실수를 잡을 수 없으므로
NewType 으로 구분한다 (런타임 비용 없음, 타입 체커에서만 구분).
"""

from __future__ import annotations

from enum import Enum
from typing import NewType

PlateId = NewType("PlateId", str)
"""차량 번호판. 시스템 전체에서 차량을 가리키는 유일한 키.

주의: 관제는 차량 '객체'를 절대 받지 않는다. 오직 이 번호판 문자열만 안다.
"""

SlotId = NewType("SlotId", str)
NodeId = NewType("NodeId", str)
EdgeId = NewType("EdgeId", str)


class VehicleClass(str, Enum):
    """차량 크기 등급. 입구 ANPR 이 관측할 수 있는 값이다."""

    COMPACT = "compact"
    SEDAN = "sedan"
    SUV = "suv"
    VAN = "van"

    @property
    def footprint(self) -> tuple[float, float]:
        """(전장 m, 전폭 m)."""
        return _FOOTPRINT[self]


_FOOTPRINT: dict[VehicleClass, tuple[float, float]] = {
    VehicleClass.COMPACT: (3.6, 1.6),
    VehicleClass.SEDAN: (4.7, 1.85),
    VehicleClass.SUV: (4.9, 1.9),
    VehicleClass.VAN: (5.2, 1.95),
}


class SlotType(str, Enum):
    """주차면 종류. 차종 제약(비용 함수의 w5)에 쓰인다."""

    STANDARD = "standard"
    COMPACT = "compact"
    DISABLED = "disabled"
    EV = "ev"


class SlotStatus(str, Enum):
    """주차면 상태.

    RESERVED 는 관제가 특정 차량에게 배정했다는 뜻일 뿐, 물리적으로는 비어 있다.
    바로 이 틈이 '자리 강탈'이 일어나는 지점이다.
    """

    FREE = "free"
    RESERVED = "reserved"
    OCCUPIED = "occupied"
