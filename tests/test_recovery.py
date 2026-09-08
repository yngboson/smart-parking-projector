"""복구 전략 6종 — **이 연구가 비교하려는 대상**.

각 전략이 "무엇을 최적화하는가"는 서로 다르므로, 여기서 검사하는 것은 성능이 아니라
**성격이 설계대로인가**다. `local_reassign` 은 피해자만 건드려야 하고,
`global_rematch` 는 남까지 옮길 수 있어야 하고, `reserve_pool` 은 평시에 자리를
빼둬야 한다. 그 성격이 어긋나면 비교표의 각 줄이 무엇을 뜻하는지 알 수 없게 된다.

**어느 전략이 이기는지는 여기서 검사하지 않는다.** 그건 실험으로 답할 질문이지
테스트로 못 박을 전제가 아니다 (docs/DECISIONS.md D-009).
"""

from __future__ import annotations

import pytest

from sim.common.ids import PlateId, SlotId, SlotType, VehicleClass
from sim.common.lotmap import LotMap
from sim.common.messages import GuidanceReason, SlotStolen, VehicleEntered
from sim.control.cost import CostWeights, slot_is_eligible
from sim.control.recovery import api as R
from sim.control.routing import LaneRouter
from sim.control.system import ProjectorControl
from sim.scenarios.loader import load_named
from sim.world.lot_builder import GridSpec, build_grid_lot
from sim.world.simulation import Simulation

VICTIM = PlateId("11가1111")
HOLDER = PlateId("22나2222")
OTHER = PlateId("33다3333")


@pytest.fixture(scope="module")
def lot() -> LotMap:
    return build_grid_lot(GridSpec())


@pytest.fixture(scope="module")
def router(lot: LotMap) -> LaneRouter:
    return LaneRouter(lot)


def usable_slots(lot: LotMap, count: int) -> list[SlotId]:
    return [
        sid for sid, s in sorted(lot.slots.items())
        if s.slot_type not in (SlotType.DISABLED, SlotType.EV)
    ][:count]


def make_ctx(lot: LotMap, router: LaneRouter, *, free: list[SlotId], candidates) -> R.RecoveryContext:
    return R.RecoveryContext(
        lot=lot, router=router, candidates=candidates,
        free_slots=free, load={}, weights=CostWeights(),
        trust=lambda p: 0.2 if p == OTHER else 1.0,
    )


def victim(lot: LotMap, plate: PlateId = VICTIM, reroutes: int = 0) -> R.Candidate:
    return R.Candidate(
        plate=plate, vehicle_class=VehicleClass.SEDAN,
        from_node=lot.entry_nodes[0], held_slot=None,
        reroute_count=reroutes, is_victim=True,
    )


def holder(lot: LotMap, slot_id: SlotId, plate: PlateId = HOLDER) -> R.Candidate:
    return R.Candidate(
        plate=plate, vehicle_class=VehicleClass.SEDAN,
        from_node=lot.entry_nodes[0], held_slot=slot_id, is_victim=False,
    )


# ── 계약 ──────────────────────────────────────────────────────────


def test_all_six_strategies_are_registered() -> None:
    """6종 비교가 이 연구의 결론이다 (D-009). 하나라도 빠지면 표가 불완전해진다."""
    assert set(R.available_names()) == {
        "local_reassign",
        "global_rematch",
        "chain_shift",
        "reserve_pool",
        "reputation_aware",
        "fairness_weighted",
    }


@pytest.mark.parametrize("name", sorted(R.available_names()))
def test_each_strategy_honours_the_protocol(name: str, lot: LotMap) -> None:
    strategy = R.get(name)
    assert isinstance(strategy, R.RecoveryStrategy)
    assert strategy.name == name
    assert isinstance(strategy.withhold(lot), set)


def test_the_default_recovery_is_global_rematch(lot: LotMap) -> None:
    """**기본값을 바꾸지 마라.** 비교의 기준점이다 (D-009)."""
    assert ProjectorControl(lot).recovery.name == "global_rematch"
    for name in ("light", "busy", "rush_hour"):
        assert load_named(name).recovery == "global_rematch"


@pytest.mark.parametrize("name", sorted(R.available_names()))
def test_a_reassignment_is_always_usable(name: str, lot: LotMap, router: LaneRouter) -> None:
    """어떤 전략이든, 갈 수 없거나 못 쓰는 자리를 주면 안 된다."""
    free = usable_slots(lot, 12)
    ctx = make_ctx(lot, router, free=free, candidates=[victim(lot)])
    moves = R.get(name).recover(R.RecoveryRequest(t=1.0, victims=(VICTIM,)), ctx)

    assert moves, f"{name} 이 피해 차량에게 아무것도 주지 못했습니다"
    seen = set()
    for m in moves:
        assert m.slot_id not in seen, "같은 자리를 두 대에게 줬습니다"
        seen.add(m.slot_id)
        assert slot_is_eligible(lot.slots[m.slot_id], VehicleClass.SEDAN)
        assert m.route.goal == lot.slots[m.slot_id].access_node


@pytest.mark.parametrize("name", sorted(R.available_names()))
def test_no_strategy_crashes_when_the_lot_is_full(name: str, lot: LotMap, router: LaneRouter) -> None:
    """만차에서 복구는 불가능하다. 불가능한 것을 조용히 받아들여야 한다."""
    ctx = make_ctx(lot, router, free=[], candidates=[victim(lot)])
    assert R.get(name).recover(R.RecoveryRequest(t=1.0, victims=(VICTIM,)), ctx) == []


# ── 전략별 성격 ───────────────────────────────────────────────────


def test_local_reassign_touches_only_the_victim(lot: LotMap, router: LaneRouter) -> None:
    free = usable_slots(lot, 8)
    ctx = make_ctx(
        lot, router, free=free,
        candidates=[victim(lot), holder(lot, free[0]), holder(lot, free[1], OTHER)],
    )
    moves = R.get("local_reassign").recover(R.RecoveryRequest(1.0, (VICTIM,)), ctx)

    assert [m.plate for m in moves] == [VICTIM]
    assert moves[0].reason is GuidanceReason.REROUTE


def test_global_rematch_may_move_bystanders(lot: LotMap, router: LaneRouter) -> None:
    """남까지 옮길 수 있어야 '모두 조금씩 양보'가 성립한다.

    옮겨진 남은 피해자가 아니므로 RESHUFFLE 로 센다. 두 숫자를 섞으면
    "피해자 수"와 "휘말린 대수"가 구분되지 않는다.
    """
    held = usable_slots(lot, 40)
    ctx = make_ctx(
        lot, router,
        free=held[30:],                       # 빈 자리는 멀리 있다
        candidates=[victim(lot), holder(lot, held[0]), holder(lot, held[1], OTHER)],
    )
    moves = R.get("global_rematch").recover(R.RecoveryRequest(1.0, (VICTIM,)), ctx)

    assert any(m.plate == VICTIM for m in moves)
    for m in moves:
        expected = (
            GuidanceReason.REROUTE if m.plate == VICTIM else GuidanceReason.RESHUFFLE
        )
        assert m.reason is expected


def test_chain_shift_bounds_how_many_cars_are_disturbed(lot: LotMap, router: LaneRouter) -> None:
    """사슬 길이가 곧 '휘말리는 대수'의 상한이다. 그 보장이 이 전략의 존재 이유다."""
    from sim.control.recovery.chain_shift import MAX_DEPTH

    held = usable_slots(lot, 40)
    holders = [
        holder(lot, sid, PlateId(f"{i:02d}가{i:04d}"))
        for i, sid in enumerate(held[:6])
    ]
    ctx = make_ctx(lot, router, free=held[30:], candidates=[victim(lot), *holders])
    moves = R.get("chain_shift").recover(R.RecoveryRequest(1.0, (VICTIM,)), ctx)

    assert moves and moves[0].plate == VICTIM
    assert len(moves) <= MAX_DEPTH
    assert len({m.plate for m in moves}) == len(moves), "같은 차량을 두 번 옮겼습니다"


def test_reserve_pool_keeps_slots_out_of_normal_allocation(lot: LotMap) -> None:
    """평시에 자리를 놀리는 것이 이 전략의 대가다. 실제로 놀려야 한다."""
    pool = R.get("reserve_pool").withhold(lot)
    assert 0 < len(pool) < len(lot.slots) * 0.2

    control = ProjectorControl(lot, recovery="reserve_pool")
    handed = set()
    for i in range(40):
        plate = PlateId(f"{i:02d}나{i:04d}")
        cmds = control.on_events(
            [VehicleEntered(t=i * 0.1, plate=plate, vehicle_class=VehicleClass.SEDAN)]
        )
        handed |= {c.target_slot for c in cmds if hasattr(c, "target_slot")}

    assert handed, "평시 배정이 아예 동작하지 않습니다"
    assert not (handed & pool), "예비석을 평시에 나눠줬습니다"


def test_reserve_pool_spends_the_reserve_on_a_victim(lot: LotMap, router: LaneRouter) -> None:
    strategy = R.get("reserve_pool")
    pool = sorted(strategy.withhold(lot))
    ctx = make_ctx(lot, router, free=pool, candidates=[victim(lot)])

    moves = strategy.recover(R.RecoveryRequest(1.0, (VICTIM,)), ctx)
    assert moves and moves[0].slot_id in set(pool)


def test_reputation_aware_offers_low_trust_plates_a_better_spot(lot: LotMap) -> None:
    """이탈 이력이 있는 번호판을 **달랜다** — 벌을 주는 것이 아니다 (D-014).

    이탈은 배신이 아니라 저울의 불일치이므로, 본인이 만족할 자리를 주면 이탈할
    이유가 사라진다는 것이 이 전략의 논지다.
    """
    strategy = R.get("reputation_aware")
    ctx = R.BiasContext(lot=lot, trust=lambda p: 0.2 if p == OTHER else 1.0,
                        reroute_count=lambda _p: 0)

    good = min(lot.slots, key=lot.walk_distance)
    far = max(lot.slots, key=lot.walk_distance)

    assert strategy.bias(VICTIM, good, ctx) == 0.0, "신뢰도가 높으면 보정이 없어야 한다"
    assert strategy.bias(OTHER, good, ctx) < 0.0, "좋은 자리를 더 싸게 봐야 한다"
    assert strategy.bias(OTHER, good, ctx) < strategy.bias(OTHER, far, ctx)


def test_fairness_weighted_prefers_the_repeatedly_displaced(lot: LotMap, router: LaneRouter) -> None:
    """평균이 아니라 최악을 줄이는 전략. 많이 밀린 사람이 경쟁에서 이겨야 한다."""
    strategy = R.get("fairness_weighted")
    ctx = R.BiasContext(lot=lot, trust=lambda _p: 1.0,
                        reroute_count=lambda p: 3 if p == OTHER else 0)
    sid = next(iter(lot.slots))

    assert strategy.bias(VICTIM, sid, ctx) == 0.0
    assert strategy.bias(OTHER, sid, ctx) < 0.0

    # 재배정 순서도 재탐색 횟수를 따른다
    free = usable_slots(lot, 6)
    rc = make_ctx(
        lot, router, free=free,
        candidates=[victim(lot, VICTIM, reroutes=0), victim(lot, OTHER, reroutes=3)],
    )
    moves = strategy.recover(R.RecoveryRequest(1.0, (VICTIM, OTHER)), rc)
    assert moves[0].plate == OTHER, "많이 밀린 차량이 먼저 골라야 합니다"


def test_priority_is_capped(lot: LotMap) -> None:
    """상한이 없으면 한 사람이 영원히 모든 경쟁을 이긴다."""
    from sim.control.recovery.fairness_weighted import MAX_PRIORITY, PRIORITY_STEP

    strategy = R.get("fairness_weighted")
    ctx = R.BiasContext(lot=lot, trust=lambda _p: 1.0, reroute_count=lambda _p: 99)
    sid = next(iter(lot.slots))
    assert strategy.bias(VICTIM, sid, ctx) == pytest.approx(-PRIORITY_STEP * MAX_PRIORITY)


# ── 시뮬레이션에서 갈아끼우기 ─────────────────────────────────────


@pytest.mark.parametrize("name", sorted(R.available_names()))
def test_every_strategy_survives_a_crowded_run(name: str, lot: LotMap) -> None:
    """전략을 갈아끼워도 시뮬레이션이 끝까지 돌아야 비교가 가능하다."""
    scenario = load_named("rush_hour")
    control = scenario.build_control(lot, recovery=name)
    sim = Simulation(lot, control=control, config=scenario.config_for(0))

    thefts = 0
    for _f in sim.run(180.0):
        thefts += sum(1 for i in control.inferences if isinstance(i, SlotStolen))

    assert control.recovery.name == name
    parked = {v.parked_slot for v in sim.vehicles if v.parked_slot is not None}
    assert len(parked) == len([v for v in sim.vehicles if v.parked_slot is not None]), (
        "두 대가 같은 주차면에 들어갔습니다"
    )
    reroutes = sum(v.reroute_count for v in control.state.vehicles.values())
    assert reroutes >= thefts, f"{name}: 피해 차량이 재배정을 못 받았습니다"
