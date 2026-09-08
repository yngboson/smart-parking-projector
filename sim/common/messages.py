"""계층 간에 오가는 메시지.

**이 파일이 계층 분리의 실질적 관문이다.**

관제(`sim.control`)가 세상에 대해 알 수 있는 것은 오직 아래 `SensorEvent` 들뿐이며,
전부 실제 주차장에 설치 가능한 장비가 만들어낼 수 있는 값으로 제한되어 있다.
차량 객체 참조나 운전자 성향(compliance)은 어떤 필드에도 들어가지 않는다.

새 이벤트를 추가할 때 스스로에게 물을 것:
    "실제 하드웨어가 이 값을 관측할 수 있는가?"
아니라면 그 필드는 여기에 들어올 자격이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sim.common.geometry import Vec2
from sim.common.ids import NodeId, PlateId, SlotId, VehicleClass

# ─────────────────────────── 센서 → 관제 ───────────────────────────


@dataclass(frozen=True)
class SensorEvent:
    """모든 센서 관측의 기반. 불변이며 값만 담는다."""

    t: float


@dataclass(frozen=True)
class VehicleEntered(SensorEvent):
    """입구 ANPR 이 번호판을 읽었다.

    vehicle_class 는 차량 크기 등급으로, 실제로도 카메라/루프 검지기로 추정 가능하다.
    """

    plate: PlateId
    vehicle_class: VehicleClass


@dataclass(frozen=True)
class SlotOccupancyChanged(SensorEvent):
    """주차면 점유 센서의 상태 변화.

    plate 는 `slot_sensor_mode == "anpr"` (구역 카메라가 있는 경우) 에만 채워진다.
    "presence" 모드에서는 None 이며, 이때 관제는 '누가 점유했는지'를 통로 검지기
    기록으로부터 추론해야 한다 — 더 저렴한 하드웨어를 가정한 확장 실험용.
    """

    slot_id: SlotId
    occupied: bool
    plate: PlateId | None = None


@dataclass(frozen=True)
class LaneDetection(SensorEvent):
    """통로 검지기를 차량이 통과했다.

    관제가 '유도선 이탈'을 알아채는 유일한 수단이다. 차량이 지금 어디로 가려는지가
    아니라, 어디를 지나갔는지만 알 수 있다는 점이 중요하다.
    """

    node_id: NodeId
    plate: PlateId


@dataclass(frozen=True)
class VehicleExited(SensorEvent):
    """출구를 통과했다."""

    plate: PlateId


# ─────────────────────────── 관제 → 프로젝터 ───────────────────────────


class GuidanceReason(str, Enum):
    """유도선이 왜 발급/갱신되었는가. KPI 집계와 발표용 이벤트 로그에 쓴다."""

    INITIAL = "initial"
    """입구에서 최초 배정."""

    REROUTE = "reroute"
    """자기 자리를 빼앗겨 재배정 — reroute_count 를 올리는 것은 이 사유뿐이다."""

    RESHUFFLE = "reshuffle"
    """다른 차량의 강탈을 흡수하느라 연쇄적으로 배정이 바뀜 (global_rematch 등).
    본인이 피해자는 아니지만 우회 거리는 늘어날 수 있어 별도로 센다."""

    ZONE_REFINE = "zone_refine"
    """구역만 안내하던 상태에서 최종 주차면이 확정됨 (zone_late_binding)."""


@dataclass(frozen=True)
class GuidanceCommand:
    """프로젝터에게 '이 차량의 유도선을 이렇게 그려라'라고 지시한다.

    polyline 은 주차장 좌표계의 경로 점열이며, 뷰어는 이것을 스무딩해서
    중앙 리본 + 쉐브론으로 렌더한다 (docs/DECISIONS.md D-006).
    """

    plate: PlateId
    target_slot: SlotId
    polyline: tuple[Vec2, ...]
    reason: GuidanceReason
    revision: int = 0
    """같은 차량에 대해 몇 번째 안내인가. 0 = 최초."""


@dataclass(frozen=True)
class ClearGuidance:
    """유도선을 지운다 (주차 완료, 출차, 안내 포기)."""

    plate: PlateId


ProjectorCommand = GuidanceCommand | ClearGuidance


# ─────────────────────────── 관제 내부 판정 결과 ───────────────────────────
# 센서 이벤트로부터 관제가 '추론'해낸 사건. 월드가 알려주는 것이 아니라
# 관제가 스스로 판단한 결과라는 점이 중요하다.


@dataclass(frozen=True)
class SlotStolen:
    """예약자가 아닌 차량이 주차면을 점유했다고 관제가 판단했다."""

    t: float
    slot_id: SlotId
    taker: PlateId | None
    victim: PlateId


@dataclass(frozen=True)
class RouteDeviation:
    """차량이 안내한 경로를 벗어났다고 관제가 판단했다 (강탈 조기 경보)."""

    t: float
    plate: PlateId
    at_node: NodeId


ControlInference = SlotStolen | RouteDeviation


# ─────────────────────────── 차량이 지각하는 것 ───────────────────────────


@dataclass(frozen=True)
class GuidanceView:
    """차량(운전자)이 바닥에서 보는 자기 색 유도선.

    운전자는 다른 차의 유도선을 따라가지 않으므로 자기 것만 전달한다.
    목표 주차면이 어디인지는 선 끝을 보면 알 수 있으므로 함께 준다.
    """

    target_slot: SlotId
    polyline: tuple[Vec2, ...]
    revision: int


@dataclass(frozen=True)
class VisibleSlot:
    """운전자가 육안으로 확인한 주차면 하나.

    관제 시스템이 아니라 운전자의 눈이 만들어내는 정보다. 따라서 '예약됨'은 알 수 없고
    비어 보이는지 아닌지만 알 수 있다 — 강탈이 일어나는 근본 이유.
    """

    slot_id: SlotId
    center: Vec2
    looks_free: bool
    walk_distance: float


@dataclass(frozen=True)
class Perception:
    """한 시점에 차량 하나가 지각하는 세계 전부."""

    t: float
    pose_forward_clearance: float
    """앞차까지의 여유 거리 (m). 막혀 있지 않으면 큰 값."""

    visible_slots: tuple[VisibleSlot, ...] = ()
    guidance: GuidanceView | None = None
