"""시뮬레이션 루프 — **계층을 배선하는 유일한 곳**.

세 계층은 서로를 모른다. 여기서만 서로 연결된다:

    센서 ──SensorEvent──▶ 관제 ──ProjectorCommand──▶ 프로젝터
                                                        │
                                                   GuidanceView
                                                        ▼
    물리 ◀──ControlInput── 운전자 ◀──Perception──── (자기 색 선 + 시야 + 앞차 여유)

그러니 이 파일이 지저분해지는 것은 정상이다. 대신 **다른 파일이 깨끗해진다.**
배선이 여기 말고 다른 곳에 생기면 그 순간 계층 분리가 무너지므로, 새 연결이
필요하면 반드시 이 파일에 추가하라.

**차간거리를 여기서 강제하지 않는다.** 월드는 앞차까지의 여유 거리를 재서 알려줄
뿐이고(`Perception.pose_forward_clearance`), 감속은 운전자가 한다. 앞차를 보고
멈추는 것은 시뮬레이터의 기능이 아니라 사람의 행동이기 때문이다
(docs/HANDOFF.md 미해결 이슈 1).
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from sim.agents.driver import Driver, DriverPhase, DriverProfile
from sim.agents.driving import DrivingSkill
from sim.agents.perception import VisionModel
from sim.common.geometry import Vec2
from sim.common.ids import PlateId, SlotId, SlotStatus, SlotType, VehicleClass
from sim.common.lotmap import LotMap
from sim.common.messages import (
    Perception,
    RouteDeviation,
    SlotStolen,
    VisibleSlot,
)
from sim.common.vehicle import SelfState, VehicleSpec, body_center, footprint
from sim.common.geometry import Pose
from sim.control.api import ControlSystem
from sim.control.system import ProjectorControl
from sim.world import physics
from sim.world.projector import Projector
from sim.world.sensors import SensorSuite
from sim.world.traffic import AisleTraffic

_HANGUL = "가나다라마바사아자차카타파하거너더러머버서어저"

_CLASS_MIX: tuple[tuple[VehicleClass, float], ...] = (
    (VehicleClass.COMPACT, 0.15),
    (VehicleClass.SEDAN, 0.45),
    (VehicleClass.SUV, 0.30),
    (VehicleClass.VAN, 0.10),
)

_MODEL_OF = {
    VehicleClass.COMPACT: "hatch_a",
    VehicleClass.SEDAN: "sedan_a",
    VehicleClass.SUV: "suv_a",
    VehicleClass.VAN: "van_a",
}


@dataclass(frozen=True, slots=True)
class SimConfig:
    """한 번의 실행을 규정하는 값들. 같은 설정 + 같은 시드 = 같은 결과."""

    dt: float = 0.10
    """고정 timestep(초). 가변 timestep 은 결과 재현이 안 되므로 쓰지 않는다."""

    seed: int = 0
    arrival_rate: float = 0.10
    """초당 도착 대수. 0.10 이면 분당 6대."""

    dwell_mean: float = 150.0
    """주차 후 머무는 평균 시간(초). 발표용으로 실제보다 훨씬 짧게 잡았다."""

    dwell_min: float = 30.0
    max_guided: int = 20
    """동시에 **안내 중인** 최대 대수. 주차를 마친 차는 세지 않는다.

    유도선 색 구분의 한계다 (D-006). 주차장 자체의 수용력은 주차면 수가 정하므로
    여기서 제한하면 안 된다 — 주차한 차까지 세면 주차장이 인위적으로 막힌다.
    """

    returning_share: float = 0.35
    """이미 다녀간 적 있는 번호판이 다시 들어올 비율 (0~1).

    **이게 0 이면 `reputation_aware`(R5)가 구조적으로 작동할 수 없다.** 아무도
    돌아오지 않는 주차장에서는 번호판별 과거 이력이 언제나 비어 있고, 전략은
    `local_reassign` 과 똑같이 행동한다. 실제로 그렇게 만들어 놓고 비교했더니
    세 전략의 숫자가 소수점까지 같았다.

    실제 주차장 이용자의 상당수는 단골이다. 그 사실이 있어야 "알고리즘이 관측만으로
    학습한다"(D-003)는 서사가 성립한다.
    """

    prefill: float = 0.0
    """시작할 때 미리 차 있는 주차면의 비율 (0~1).

    **강탈은 빈 자리가 흔할 때 일어나지 않는다.** 텅 빈 주차장에서는 비협조
    운전자도 남의 자리를 건드릴 이유가 없어 그냥 아무 빈자리나 차지한다.
    자리가 귀해져야 "남에게 배정된 자리"를 노리게 된다 — 주말 대형마트가 그렇다.

    건물에 가까운 자리부터 채운다. 실제 주차장이 그렇고, 그래야 늦게 온 차량이
    받는 자리와 눈에 보이는 좋은 자리의 격차가 생긴다.
    """

    entry_clearance: float = 9.0
    """입구에 이 거리 안에 차가 있으면 다음 차를 들여보내지 않는다(m)."""

    slot_sensor_mode: str = "anpr"
    """"anpr" = 주차면 센서가 번호판까지 읽음, "presence" = 점유 여부만 (D-002)."""

    noncompliant_share: float = 0.0
    """비협조 성향을 가진 운전자의 비율 (0~1).

    **이 연구의 독립변수다.** 0 이면 전원이 안내를 따르고 강탈이 일어나지 않는다.
    올릴수록 관제가 복구해야 할 상황이 잦아진다. 관제는 이 값도, 개별 차량의
    성향도 볼 수 없다 (docs/DECISIONS.md D-001).
    """

    compliance_low: float = 0.30
    compliance_high: float = 0.75
    """비협조 운전자의 compliance 범위. 유혹 한 번을 참아낼 확률이다."""

    vision_enabled: bool = False
    """협조적인 운전자에게도 육안 관측을 넘길 것인가.

    평소에는 필요 없다 — 유도선만 보고 가는 사람에게 빈자리 목록을 계산해 주는 것은
    낭비다. 무안내 베이스라인(D-010)에서는 **모두가** 눈으로 찾아야 하므로 켠다.
    """


@dataclass(slots=True)
class WorldVehicle:
    """주차장 안에 실재하는 차량 한 대.

    **이 객체는 관제에게 절대 넘어가지 않는다.** 관제가 받는 것은 센서 이벤트뿐이다.
    """

    plate: PlateId
    vehicle_class: VehicleClass
    spec: VehicleSpec
    driver: Driver
    state: SelfState
    color: int
    model: str
    entered_t: float
    dwell: float
    parked_t: float | None = None
    parked_slot: SlotId | None = None

    @property
    def body_pose(self) -> Pose:
        """차체 중심 기준 자세. 뷰어가 메시를 놓는 기준이다."""
        c = body_center(self.state.pose, self.spec)
        return Pose(c.x, c.y, self.state.pose.theta)


@dataclass(slots=True)
class Frame:
    """한 시점의 화면 상태. 라이브 스트림과 trace 재생이 같은 포맷을 쓴다 (D-005)."""

    t: float
    vehicles: list[dict]
    guidance: list[dict]
    slots: list[dict]
    events: list[dict]
    kpi: dict

    def to_dict(self) -> dict:
        return {
            "t": round(self.t, 3),
            "vehicles": self.vehicles,
            "guidance": self.guidance,
            "slots": self.slots,
            "events": self.events,
            "kpi": self.kpi,
        }


class Simulation:
    """고정 timestep 시뮬레이션."""

    def __init__(
        self,
        lot: LotMap,
        control: ControlSystem | None = None,
        config: SimConfig | None = None,
    ) -> None:
        self.lot = lot
        self.config = config or SimConfig()
        self.control = control or ProjectorControl(
            lot, slot_sensor_mode=self.config.slot_sensor_mode
        )
        self.sensors = SensorSuite(lot, self.config.slot_sensor_mode)
        self.projector = Projector()
        self.traffic = AisleTraffic(lot)
        self.vision = VisionModel()

        self.t = 0.0
        self.vehicles: list[WorldVehicle] = []
        self.rng = random.Random(self.config.seed)

        self._entry_pose = _entry_pose(lot)
        self._plates: set[PlateId] = set()
        self._departed: list[PlateId] = []
        self._free_colors: list[int] = []
        self._next_color = 0
        self._backlog = 0
        self._park_times: list[float] = []
        self._sent_revision: dict[PlateId, int] = {}
        self._sent_status: dict[SlotId, str] = {}
        self._prefill()

        # 시작부터 차 있는 자리는 관제도 처음부터 알아야 한다. 모르는 채로 배정하면
        # 첫 몇 초 동안 있지도 않은 강탈이 쏟아진다.
        if self.vehicles:
            self.control.on_events(self.sensors.prime(0.0, self.vehicles))
        self._pending: list = []
        """아직 발행하지 않은 추론 사건들. 프레임을 솎아내도 잃지 않는다."""

        self._stolen = 0

    # ── 루프 ──────────────────────────────────────────────────────

    def run(self, duration: float, stride: int = 1) -> Iterator[Frame]:
        """duration 초 동안 돌리며 프레임을 내보낸다.

        :param stride: 몇 틱마다 한 프레임을 발행할 것인가. 물리는 언제나 dt 간격으로
            푼다 — 솎아내는 것은 **관측**뿐이다. 녹화본 크기를 줄일 때 쓴다.
        """
        steps = int(round(duration / self.config.dt))
        for i in range(steps):
            frame = self.step(publish=(i % max(1, stride) == 0))
            if frame is not None:
                yield frame

    def step(self, publish: bool = True) -> Frame | None:
        dt = self.config.dt
        self.t += dt

        self._spawn()

        # ① 센서가 세상을 관측한다 → ② 관제가 판단한다 → ③ 프로젝터에 반영한다
        events = self.sensors.observe(self.t, self.vehicles)
        commands = self.control.on_events(events)
        self.projector.apply(commands, self.t)

        # ④ 운전자들이 각자 보고 판단하고, 물리가 그 결과를 적분한다
        shapes = {v.plate: footprint(v.state.pose, v.spec) for v in self.vehicles}
        self.traffic.update(self.t, self.vehicles)
        for v in self.vehicles:
            perception = self._perceive(v, shapes)
            cmd = v.driver.decide(self.t, v.state, perception)
            v.state = physics.step(v.state, v.spec, cmd, dt)
            self.projector.advance(v.plate, v.state.pose.position)
            self._update_schedule(v)

        # 사건은 프레임을 건너뛰어도 잃으면 안 된다. 발행할 때 한꺼번에 내보낸다.
        self._pending.extend(getattr(self.control, "inferences", []))
        self._retire()
        return self._frame() if publish else None

    # ── 지각 ──────────────────────────────────────────────────────

    def _perceive(self, v: WorldVehicle, shapes: dict) -> Perception:
        return Perception(
            t=self.t,
            pose_forward_clearance=self._clearance(v, shapes),
            stop_distance=self.traffic.stop_distance(v),
            visible_slots=self._visible_slots(v),
            guidance=self.projector.view(v.plate),
        )

    def _clearance(self, v: WorldVehicle, shapes: dict) -> float:
        """앞차까지의 여유 거리. 막혀 있지 않으면 무한대.

        **주차면 안에 들어가 있는 차는 세지 않는다.** 통로가 도로이고 주차면은
        도로 밖이다. 옆자리에 세워진 차를 장애물로 치면, 주차면에서 나오려는 차가
        자기 옆차를 보고 영원히 못 나온다 — 실제로 그렇게 굳었다.
        여전히 통로에 몸을 걸치고 있는 차(후진 주차 중)는 그대로 장애물이다.

        월드는 재서 알려줄 뿐이다. 이 값을 보고 속도를 줄이는 것은 운전자다.
        """
        parked = self.traffic.in_slot
        others = [
            c for w, c in shapes.items() if w != v.plate and w not in parked
        ]
        return physics.forward_clearance(v.state, v.spec, others)

    def _visible_slots(self, v: WorldVehicle) -> tuple[VisibleSlot, ...]:
        """운전자가 육안으로 확인한 주차면들.

        '보이는가'는 운전자의 판단 기준(`agents.perception.VisionModel`)을 쓰고,
        '비었는가'는 월드만이 아는 사실을 채운다. 다만 **예약 여부는 넣지 않는다** —
        운전자 눈에는 예약된 자리도 그냥 빈 자리로 보인다. 강탈이 일어나는 이유다.
        """
        if not (self.config.vision_enabled or v.driver.watches_for_slots):
            return ()

        # **주차를 마친 차만 세면 안 된다.** 지금 후진해 들어가는 중인 차도 그 자리를
        # 쓰고 있다. 완료 여부로만 판단하면 두 대가 같은 자리를 노리고 겹쳐 버린다.
        taken = set(self.traffic.in_slot.values())
        pose = v.state.pose
        here = pose.position
        reach = self.vision.radius * self.vision.radius
        out: list[VisibleSlot] = []
        for sid, slot in self.lot.slots.items():
            # 먼저 값싼 거리 검사로 걸러낸다. 주차면 120개 × 차량 20대를 매 틱
            # 삼각함수로 훑으면 시뮬레이션이 실시간을 못 따라간다.
            dx = slot.center.x - here.x
            dy = slot.center.y - here.y
            if dx * dx + dy * dy > reach:
                continue
            if not self.vision.can_see(pose, slot.center):
                continue
            out.append(
                VisibleSlot(
                    slot_id=sid,
                    center=slot.center,
                    looks_free=sid not in taken,
                    walk_distance=self.lot.walk_distance(sid),
                )
            )
        return tuple(out)

    # ── 차량 생성/퇴장 ────────────────────────────────────────────

    def _spawn(self) -> None:
        cfg = self.config
        if self.rng.random() < cfg.arrival_rate * cfg.dt:
            self._backlog += 1
        guided = sum(1 for v in self.vehicles if v.parked_t is None)
        if self._backlog == 0 or guided >= cfg.max_guided:
            return
        if not self._entry_is_clear():
            return

        self._backlog -= 1
        self.vehicles.append(self._make_vehicle())

    def _prefill(self) -> None:
        """시작부터 일부 주차면을 채워 둔다."""
        count = int(round(len(self.lot.slots) * self.config.prefill))
        if count <= 0:
            return

        usable = [
            s for s in self.lot.slots.values()
            if s.slot_type not in (SlotType.DISABLED, SlotType.EV)
        ]
        usable.sort(key=lambda s: self.lot.walk_distance(s.id))

        # 건물에 가까운 자리부터, 다만 딱 잘라 채우지는 않는다 — 실제 주차장에도
        # 좋은 자리 사이사이에 빈 칸이 남는다.
        pool = usable[: min(len(usable), int(count * 1.7) + 4)]
        aisle_heading = {a.id: a.heading for a in self.lot.aisles if a.axis == "h"}

        for slot in self.rng.sample(pool, min(count, len(pool))):
            approach = aisle_heading.get(self.lot.nodes[slot.access_node].aisle, 0.0)
            v = self._make_vehicle()
            pose = v.driver.park_in(slot, approach or 0.0)
            v.state = SelfState(pose=pose, speed=0.0, steer=0.0, gear=0)
            v.parked_t = 0.0
            v.parked_slot = slot.id
            v.dwell = self.rng.uniform(0.3, 1.0) * self.config.dwell_mean
            self._free_colors.append(v.color)   # 안내받지 않는 차는 색을 쓰지 않는다
            v.color = -1
            self.vehicles.append(v)

    def _entry_is_clear(self) -> bool:
        """진입 램프에 다음 차를 들여보낼 공간이 있는가.

        **주차면에 세워진 차는 세지 않는다.** 입구에서 가장 가까운 주차면은 램프에서
        7~8m 밖에 안 떨어져 있어서, 그 자리에 차가 서 있으면 주차장 입구가 영원히
        막힌다 — 실제로 그렇게 막혀서 900초 동안 4대만 들어왔다.
        """
        p = self._entry_pose.position
        return all(
            w.parked_slot is not None
            or w.state.pose.position.distance_to(p) > self.config.entry_clearance
            for w in self.vehicles
        )

    def _make_vehicle(self) -> WorldVehicle:
        vclass = _weighted_choice(self.rng, _CLASS_MIX)
        spec = VehicleSpec.of(vclass)
        plate = self._new_plate()

        # 사람마다 운전 실력도, 안내를 따르는 정도도 다르다.
        skill = DrivingSkill(
            cruise_speed=self.rng.uniform(3.0, 4.0),
            lookahead_gain=self.rng.uniform(0.95, 1.20),
        )
        profile = DriverProfile(
            compliance=self._sample_compliance(),
            walk_preference=self.rng.random(),
            skill=skill,
        )

        # 차량마다 독립된 난수원을 준다. 이탈 판정이 한 대의 결과에 따라 다른 대의
        # 결과까지 흔들면 같은 시드로도 재현이 안 된다.
        driver = Driver(
            spec=spec, lot=self.lot, profile=profile,
            rng=random.Random(self.rng.getrandbits(32)),
        )

        return WorldVehicle(
            plate=plate,
            vehicle_class=vclass,
            spec=spec,
            driver=driver,
            state=SelfState(pose=self._entry_pose, speed=0.0, steer=0.0, gear=1),
            color=self._take_color(),
            model=_MODEL_OF[vclass],
            entered_t=self.t,
            dwell=max(
                self.config.dwell_min, self.rng.expovariate(1.0 / self.config.dwell_mean)
            ),
        )

    def _sample_compliance(self) -> float:
        """이 운전자가 안내를 얼마나 따를 것인가.

        대부분은 그대로 따른다(1.0). 일부만 비협조 성향을 갖는다 — 실제로도
        대다수는 안내를 따르고 소수가 무시하며, 그 소수가 문제를 만든다.
        """
        cfg = self.config
        if self.rng.random() >= cfg.noncompliant_share:
            return 1.0
        return self.rng.uniform(cfg.compliance_low, cfg.compliance_high)

    def _update_schedule(self, v: WorldVehicle) -> None:
        """주차를 마친 차량의 체류 시간을 재고, 다 되면 나가라고 알린다."""
        if v.driver.is_parked:
            if v.parked_t is None:
                # 어느 자리에 댔는지는 **물리적 사실**이지 운전자의 의도가 아니다.
                sid = self.traffic.in_slot.get(v.plate) or v.driver.target_slot
                v.parked_t = self.t

                if sid is not None and sid in self._claimed(v):
                    # 들어와 보니 이미 임자가 있다. 이 시뮬레이터는 충돌을 모델링하지
                    # 않으므로 그냥 겹쳐 버리는데, 그건 화면에서도 통계에서도 거짓말이다.
                    # 실제 운전자가 하는 일을 시킨다 — 포기하고 나간다.
                    v.driver.leave()
                    return

                v.parked_slot = sid
                self._park_times.append(self.t - v.entered_t)
            elif self.t - v.parked_t >= v.dwell:
                v.driver.leave()
        elif v.parked_slot is not None:
            # 주차면을 떠났다. 더 이상 그 자리를 점유하고 있지 않다.
            v.parked_slot = None

    def _claimed(self, exclude: WorldVehicle) -> set[SlotId]:
        """지금 다른 차가 차지하고 있는 주차면들."""
        return {
            w.parked_slot for w in self.vehicles
            if w is not exclude and w.parked_slot is not None
        }

    def _retire(self) -> None:
        leaving = [v for v in self.vehicles if v.driver.is_done]
        for v in leaving:
            self._free_colors.append(v.color)
            self._departed.append(v.plate)   # 단골로 다시 올 수 있다
        if leaving:
            gone = {v.plate for v in leaving}
            self.vehicles = [v for v in self.vehicles if v.plate not in gone]

    def _new_plate(self) -> PlateId:
        """새 번호판, 또는 다녀간 적 있는 단골의 번호판."""
        if self._departed and self.rng.random() < self.config.returning_share:
            return self._departed.pop(
                self.rng.randrange(len(self._departed))
            )
        while True:
            p = PlateId(
                f"{self.rng.randint(10, 99)}{self.rng.choice(_HANGUL)}"
                f"{self.rng.randint(1000, 9999)}"
            )
            if p not in self._plates:
                self._plates.add(p)
                return p

    def _take_color(self) -> int:
        """유도선 색 번호. 주차를 마친 차의 색만 재사용한다 (D-006)."""
        if self._free_colors:
            return self._free_colors.pop(0)
        self._next_color += 1
        return self._next_color - 1

    # ── 프레임 ────────────────────────────────────────────────────

    def _frame(self) -> Frame:
        return Frame(
            t=self.t,
            vehicles=[self._vehicle_row(v) for v in self.vehicles],
            guidance=self._guidance_rows(),
            slots=self._slot_rows(),
            events=self._event_rows(),
            kpi=self._kpi(),
        )

    def _vehicle_row(self, v: WorldVehicle) -> dict:
        pose = v.body_pose
        return {
            "id": v.plate,
            # cm 단위면 충분하다. 뷰어는 프레임 사이를 보간하므로 mm 를 보내봐야
            # 화면에 차이가 없고, trace 파일만 커진다.
            "pose": [round(pose.x, 2), round(pose.y, 2), round(pose.theta, 3)],
            "state": _VIEW_STATE[v.driver.phase],
            "color": v.color,
            "model": v.model,
        }

    def _guidance_rows(self) -> list[dict]:
        """유도선. 바뀌지 않은 선은 폴리라인을 빼고 진행률만 보낸다.

        폴리라인이 프레임 크기의 대부분이라, 이 델타 압축이 trace 파일 크기를
        한 자릿수 줄인다 (docs/PLAN.md 10).
        """
        rows: list[dict] = []
        live: set[PlateId] = set()
        for beam in self.projector.beams():
            live.add(beam.plate)
            row = {
                "id": beam.plate,
                "color": self._color_of(beam.plate),
                "progress": round(beam.progress, 4),
                "target": beam.target_slot,
            }
            if self._sent_revision.get(beam.plate) != beam.revision:
                row["polyline"] = [[round(p.x, 2), round(p.y, 2)] for p in beam.polyline]
                row["revision"] = beam.revision
                self._sent_revision[beam.plate] = beam.revision
            rows.append(row)

        for plate in [p for p in self._sent_revision if p not in live]:
            del self._sent_revision[plate]
        return rows

    def _slot_rows(self) -> list[dict]:
        """상태가 바뀐 주차면만 보낸다. 첫 프레임은 전부 보낸다."""
        state = getattr(self.control, "state", None)
        rows: list[dict] = []
        for sid in self.lot.slots:
            status = (
                state.slots[sid].status.value if state is not None else SlotStatus.FREE.value
            )
            if self._sent_status.get(sid) != status:
                self._sent_status[sid] = status
                rows.append({"id": sid, "status": status})
        return rows

    def _event_rows(self) -> list[dict]:
        rows: list[dict] = []
        for inf in self._pending:
            if isinstance(inf, SlotStolen):
                self._stolen += 1
                rows.append(
                    {
                        "type": "slot_stolen",
                        "victim": inf.victim,
                        "taker": inf.taker,
                        "slot": inf.slot_id,
                    }
                )
            elif isinstance(inf, RouteDeviation):
                rows.append(
                    {"type": "route_deviation", "plate": inf.plate, "node": inf.at_node}
                )
        self._pending.clear()
        return rows

    def _kpi(self) -> dict:
        state = getattr(self.control, "state", None)
        reroutes = (
            sum(v.reroute_count for v in state.vehicles.values()) if state else 0
        )
        occupied = sum(1 for v in self.vehicles if v.parked_slot is not None)
        return {
            "occupancy": round(occupied / max(1, len(self.lot.slots)), 4),
            "avg_park_time": round(
                sum(self._park_times) / len(self._park_times), 2
            )
            if self._park_times
            else 0.0,
            "reroutes": reroutes,
            "active": len(self.vehicles),
            "parked_total": len(self._park_times),
            "stolen": self._stolen,
            "forced_merges": self.traffic.forced_merges,
        }

    def _color_of(self, plate: PlateId) -> int:
        for v in self.vehicles:
            if v.plate == plate:
                return v.color
        return 0


_VIEW_STATE = {
    DriverPhase.ARRIVING: "waiting",
    DriverPhase.CRUISING: "driving",
    DriverPhase.STAGING: "parking",
    DriverPhase.REVERSING: "parking",
    DriverPhase.PARKED: "parked",
    DriverPhase.LEAVING: "leaving",
    DriverPhase.GONE: "leaving",
}


def _entry_pose(lot: LotMap) -> Pose:
    """입구 노드에서 첫 통로 노드를 바라보는 자세."""
    entry = lot.entry_nodes[0]
    pos = lot.node_pos(entry)
    succ = lot.successors(entry)
    if not succ:
        return Pose(pos.x, pos.y, math.pi / 2)
    heading = (lot.node_pos(succ[0][0]) - pos).angle
    return Pose(pos.x, pos.y, heading)


def _weighted_choice(rng: random.Random, table: Sequence[tuple[VehicleClass, float]]):
    r = rng.random() * sum(w for _c, w in table)
    acc = 0.0
    for value, weight in table:
        acc += weight
        if r <= acc:
            return value
    return table[-1][0]


# ── 헤드리스 실행 ────────────────────────────────────────────────


def main() -> None:
    from sim.world.lot_builder import GridSpec, build_grid_lot

    ap = argparse.ArgumentParser(description="헤드리스 시뮬레이션 (3단계 확인용)")
    ap.add_argument("--layout", default="layouts/mid_grid_120.json")
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument("--arrival-rate", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    try:
        lot = LotMap.load(args.layout)
    except FileNotFoundError:
        lot = build_grid_lot(GridSpec())

    sim = Simulation(
        lot, config=SimConfig(seed=args.seed, arrival_rate=args.arrival_rate)
    )
    last: Frame | None = None
    for frame in sim.run(args.duration):
        last = frame

    kpi = last.kpi if last else {}
    print(f"{lot.name} · {args.duration:.0f}초 · 시드 {args.seed}")
    print(f"  주차 완료 {kpi.get('parked_total', 0)}대 · 잔류 {kpi.get('active', 0)}대")
    print(f"  평균 주차 소요 {kpi.get('avg_park_time', 0):.1f}초 · 점유율 {kpi.get('occupancy', 0):.1%}")
    print(f"  재탐색 {kpi.get('reroutes', 0)}회 · 강탈 {kpi.get('stolen', 0)}건")


if __name__ == "__main__":
    main()
