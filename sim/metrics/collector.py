"""보상 원장 — 누가 얼마나 손해를 봤는지 **기록만** 한다 (docs/DECISIONS.md D-008).

    reroute_count · detour_distance_m · extra_time_s · was_victim · was_taker

**보상 산정 로직은 여기 없고, 앞으로도 여기 두지 않는다.** 얼마를 깎아 줄지는
알고리즘 문제가 아니라 운영 정책 문제이고, 사용자가 추후 논의하기로 명시했다
(CLAUDE.md '이번 범위에서 하지 않는 것'). 지금 임의로 정하면 나중에 걷어내야 하고,
발표에서 방어할 수 없는 숫자가 된다.

**우회 거리를 무엇에 견주는가.** 계획 경로 길이가 아니라 **도면상 최단 경로**에
견준다. 계획에 견주면 무안내 베이스라인에는 계획이 없어 비교 자체가 성립하지
않고, 안내 모드에서도 "계획이 나빴던 것"과 "계획을 안 따른 것"이 섞인다.
도면 최단 경로는 두 모드에 똑같이 존재하는 유일한 기준선이다.

    우회거리 = 실제 주행거리 − (입구 → 그 자리까지의 최단 경로)
    초과시간 = 실제 소요시간 − (그 최단 경로를 순항속도로 달렸을 때)

둘 다 음수가 나올 수 있다. 자르지 않는다 — 자르면 평균이 위로 치우친다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from sim.agents.driving import DrivingSkill
from sim.common.geometry import offset_polyline, polyline_length
from sim.common.ids import PlateId, SlotId
from sim.common.lotmap import LotMap

if TYPE_CHECKING:                       # 순환 import 방지 — 실행 시에는 필요 없다
    from sim.world.simulation import Frame, Simulation


@dataclass(slots=True)
class LedgerRow:
    """번호판 하나의 누적 기록. 한 줄이 JSONL 한 행이 된다."""

    plate: str
    vehicle_class: str = ""

    entered_t: float = 0.0
    parked_t: float | None = None
    exited_t: float | None = None

    park_time_s: float | None = None
    """입장에서 주차 완료까지. 못 세웠으면 None."""

    driven_m: float = 0.0
    ideal_m: float | None = None
    detour_m: float | None = None
    extra_time_s: float | None = None

    slot_id: str | None = None

    reroute_count: int = 0
    """자기 자리를 빼앗겨 다시 안내받은 횟수."""

    reshuffle_count: int = 0
    """남의 강탈을 흡수하느라 목적지가 바뀐 횟수. 본인은 피해자가 아니다."""

    deviations: int = 0
    """유도선을 벗어난 것이 검지된 횟수."""

    was_victim: bool = False
    was_taker: bool = False

    prefilled: bool = False
    """실행 시작부터 세워져 있던 차. 들어온 적이 없으므로 통계에서 뺀다.

    빼지 않으면 주행거리 0m·소요시간 0초짜리 행이 수십 개 섞여 평균이 무너진다 —
    처음 재 봤을 때 평균 우회거리가 **-90m** 로 나왔다. 초기점유 60%면 그런 행이
    72개다.
    """


class MetricsCollector:
    """실행 하나를 따라다니며 번호판별로 누적한다.

    매 발행 프레임마다 `observe()` 를 부르면 된다. 프레임을 솎아내도(stride) 사건은
    잃지 않는다 — `Frame.events` 가 그 사이의 것을 모아서 싣기 때문이다 (D-005).
    """

    def __init__(self, lot: LotMap, cruise_speed: float | None = None) -> None:
        self.lot = lot
        self.cruise_speed = cruise_speed or DrivingSkill.for_lot(lot).cruise_speed
        self.rows: dict[PlateId, LedgerRow] = {}
        self._ideal: dict[SlotId, float | None] = {}

    # ── 수집 ──────────────────────────────────────────────────────

    def observe(self, sim: "Simulation", frame: "Frame | None" = None) -> None:
        for v in sim.vehicles:
            row = self.rows.get(v.plate)
            if row is None:
                row = LedgerRow(
                    plate=str(v.plate),
                    vehicle_class=v.vehicle_class.value,
                    entered_t=v.entered_t,
                    # 한 번도 구르지 않았는데 이미 자리에 있다 = 처음부터 있던 차
                    prefilled=v.parked_slot is not None and v.odometer == 0.0,
                )
                self.rows[v.plate] = row

            row.driven_m = v.odometer
            if v.parked_slot is not None and row.parked_t is None:
                row.parked_t = v.parked_t if v.parked_t is not None else sim.t
                row.slot_id = str(v.parked_slot)
                self._settle(row)

        if frame is not None:
            self._events(frame.events)
        self._from_control(sim)

    def finish(self, sim: "Simulation") -> None:
        """실행이 끝났다. 관제가 아는 마지막 숫자를 옮겨 담는다."""
        self.observe(sim)

    # ── 내부 ──────────────────────────────────────────────────────

    def _settle(self, row: LedgerRow) -> None:
        """주차가 끝난 차량의 우회·초과를 확정한다."""
        ideal = self._ideal_distance(SlotId(row.slot_id)) if row.slot_id else None
        row.ideal_m = None if ideal is None else round(ideal, 2)
        row.park_time_s = round((row.parked_t or 0.0) - row.entered_t, 2)
        if ideal is None:
            return
        row.detour_m = round(row.driven_m - ideal, 2)
        row.extra_time_s = round(row.park_time_s - ideal / self.cruise_speed, 2)

    def _ideal_distance(self, slot_id: SlotId) -> float | None:
        """입구에서 그 자리까지, **완벽하게 안내를 따랐다면** 달렸을 거리(m).

        노드를 잇는 중심선 길이가 아니라 **주행 차선 위의 길이**를 잰다. 차는
        우측통행으로 중심선 옆을 달리므로(D-012·D-021) 중심선으로 재면 코너마다
        조금씩 짧게 나온다 — 처음 재 봤을 때 평균 우회거리가 -5m 로 나왔고,
        "완벽하게 달려도 음수"인 기준선은 읽는 사람을 헷갈리게 한다.

        `sim.common` 의 도면 기하만 쓴다. 관제의 `guidance_polyline` 을 부르지
        않는 이유는, 그러면 기준선이 **할당 전략에 따라 달라지기** 때문이다.
        비교의 기준은 전략과 무관해야 한다.

        자리마다 한 번만 풀고 캐시한다. 도면은 불변이다.
        """
        if slot_id in self._ideal:
            return self._ideal[slot_id]
        slot = self.lot.slots.get(slot_id)
        if slot is None or not self.lot.entry_nodes:
            return None
        path = self.lot.shortest_path(self.lot.entry_nodes[0], slot.access_node)
        if path is None:
            self._ideal[slot_id] = None
            return None

        lane = offset_polyline(
            [self.lot.node_pos(n) for n in path], self.lot.travel_lane_offset
        )
        total = polyline_length(lane)
        if lane:
            total += lane[-1].distance_to(slot.entry_point)
            total += slot.entry_point.distance_to(slot.center)
        self._ideal[slot_id] = total
        return total

    def _events(self, events: Sequence[dict]) -> None:
        for e in events:
            if e.get("type") == "slot_stolen":
                self._touch(e.get("taker")).was_taker = True
                self._touch(e.get("victim")).was_victim = True
            elif e.get("type") == "route_deviation":
                self._touch(e.get("plate")).deviations += 1

    def _from_control(self, sim: "Simulation") -> None:
        """관제가 세어 둔 재배정 횟수를 옮겨 담는다.

        관제의 믿음을 읽는 것이지 차량 내부를 읽는 것이 아니다 — 원장은 계층
        바깥의 관찰자이므로 양쪽을 다 볼 수 있다.
        """
        state = getattr(sim.control, "state", None)
        if state is None:
            return
        for plate, belief in state.vehicles.items():
            row = self._touch(plate)
            row.reroute_count = belief.reroute_count
            row.reshuffle_count = belief.reshuffle_count
            if belief.exited and row.exited_t is None:
                row.exited_t = sim.t

    def _touch(self, plate) -> LedgerRow:
        if plate is None:
            return LedgerRow(plate="")      # 버려질 행. 호출부를 단순하게 둔다
        key = PlateId(plate)
        row = self.rows.get(key)
        if row is None:
            row = LedgerRow(plate=str(plate))
            self.rows[key] = row
        return row

    # ── 출력 ──────────────────────────────────────────────────────

    def ledger(self) -> list[dict]:
        return [asdict(r) for r in sorted(self.rows.values(), key=lambda r: r.plate)]

    def write(self, run_dir: Path | str) -> Path:
        """`compensation_ledger.jsonl` 로 저장한다 (D-008)."""
        path = Path(run_dir) / "compensation_ledger.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in self.ledger():
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path

    def summary(self) -> dict:
        """실행 하나의 요약. 실험 하네스가 시드별로 모아 표를 만든다.

        **p95 를 함께 낸다.** 평균만 보면 "대체로 괜찮다"로 끝나는데, 이 연구가
        비교하려는 것은 사고가 났을 때의 꼬리다. `fairness_weighted` 처럼 평균을
        희생해 최악을 줄이는 전략은 평균만으로는 나쁜 전략으로 보인다.
        """
        live = [r for r in self.rows.values() if not r.prefilled]
        parked = [r for r in live if r.park_time_s is not None]
        times = [r.park_time_s for r in parked]
        detours = [r.detour_m for r in parked if r.detour_m is not None]
        extras = [r.extra_time_s for r in parked if r.extra_time_s is not None]

        return {
            "parked": len(parked),
            "seen": len(live),
            "park_time_mean": _mean(times),
            "park_time_p95": _pct(times, 0.95),
            "detour_mean_m": _mean(detours),
            "detour_p95_m": _pct(detours, 0.95),
            "extra_time_mean_s": _mean(extras),
            "extra_time_p95_s": _pct(extras, 0.95),
            "driven_total_m": round(sum(r.driven_m for r in live), 1),
            "reroutes": sum(r.reroute_count for r in live),
            "reshuffles": sum(r.reshuffle_count for r in live),
            "victims": sum(1 for r in live if r.was_victim),
            "takers": sum(1 for r in live if r.was_taker),
        }


def _mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def _pct(xs: Iterable[float], q: float) -> float:
    """단순 백분위수. 표본이 30개 안팎이라 보간법을 따질 실익이 없다."""
    xs = sorted(xs)
    if not xs:
        return 0.0
    i = min(len(xs) - 1, int(round(q * (len(xs) - 1))))
    return round(xs[i], 2)


def read_ledger(path: Path | str) -> list[dict]:
    """저장한 원장을 다시 읽는다. 중간에 잘려 있어도 거기까지는 읽는다."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return out
