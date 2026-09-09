"""실험 하네스 — 발표에 쓸 숫자가 여기서 나온다.

여기서 지켜야 할 성질은 셋이다.

1. **같은 시드는 같은 결과를 낸다.** 재현되지 않으면 표에 적은 숫자를 방어할 수
   없다. 전략 비교의 전제이기도 하다 — 두 전략의 차이가 전략 때문인지 난수
   때문인지 구분되지 않는다.
2. **원장에 보상 산정 로직이 없다** (docs/DECISIONS.md D-008). 기록만 한다.
3. **처음부터 세워져 있던 차는 통계에서 빠진다.** 안 빼면 주행거리 0m 짜리 행이
   수십 개 섞여 평균이 무너진다.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from sim.common.lotmap import LotMap
from sim.metrics.collector import MetricsCollector, read_ledger
from sim.scenarios.loader import load_named
from sim.world.lot_builder import GridSpec, build_grid_lot
from sim.world.simulation import SimConfig, Simulation

SHORT = 120.0
"""검사에 쓰는 실행 길이(초). 성질을 보는 데는 이것으로 충분하다."""


@pytest.fixture(scope="module")
def lot() -> LotMap:
    return build_grid_lot(GridSpec())


def run(lot: LotMap, seed: int = 1, **kw) -> tuple[Simulation, MetricsCollector]:
    settings = {"arrival_rate": 0.3, "prefill": 0.4, "noncompliant_share": 0.35, **kw}
    config = SimConfig(seed=seed, **settings)
    sim = Simulation(lot, config=config)
    collector = MetricsCollector(lot)
    for frame in sim.run(SHORT, stride=10):
        collector.observe(sim, frame)
    collector.finish(sim)
    return sim, collector


# ── 원장 ──────────────────────────────────────────────────────────


def test_the_ledger_records_but_does_not_price(lot: LotMap, tmp_path) -> None:
    """보상 **정책**은 이번 범위 밖이다 (D-008). 원장에 금액이 있으면 안 된다."""
    _sim, collector = run(lot)
    rows = read_ledger(collector.write(tmp_path))

    assert rows, "원장이 비어 있습니다"
    fields = set(rows[0])
    for required in ("reroute_count", "detour_m", "extra_time_s", "was_victim", "was_taker"):
        assert required in fields, f"D-008 이 요구한 {required} 가 없습니다"

    priced = {
        f for f in fields
        if any(w in f for w in ("won", "price", "discount", "fee", "amount", "refund"))
    }
    assert not priced, f"원장에 보상 산정이 섞여 들어갔습니다: {priced}"


def test_prefilled_cars_are_kept_out_of_the_statistics(lot: LotMap) -> None:
    """들어온 적이 없는 차가 평균을 끌어내린다.

    처음 쟀을 때 평균 우회거리가 **-90m** 로 나왔다. 초기점유 60% 면 주행거리 0m
    짜리 행이 72개다.
    """
    _sim, collector = run(lot, prefill=0.6)
    rows = collector.ledger()

    prefilled = [r for r in rows if r["prefilled"]]
    assert prefilled, "초기점유 60% 인데 처음부터 있던 차가 하나도 없습니다"
    # 들어오는 장면이 없었다 = 주차 소요시간이 0 에 붙어 있다.
    # (나중에 출차하며 굴러가므로 주행거리는 0 이 아닐 수 있다.)
    assert all((r["park_time_s"] or 0.0) < 1.0 for r in prefilled)

    summary = collector.summary()
    assert summary["seen"] == len(rows) - len(prefilled)
    assert abs(summary["detour_mean_m"]) < 20.0, (
        f"평균 우회거리가 {summary['detour_mean_m']}m 입니다 — 안 들어온 차가 섞였습니다"
    )


def test_a_perfectly_guided_run_has_almost_no_detour(lot: LotMap) -> None:
    """기준선이 맞는지 확인한다.

    우회거리는 **주행 차선 위의** 최단 경로에 견준다. 노드 중심선으로 재면 차가
    우측통행으로 비껴 달리는 만큼 짧게 나와, 완벽하게 달려도 음수가 된다 —
    읽는 사람이 헷갈리는 기준선은 없느니만 못하다.
    """
    _sim, collector = run(lot, noncompliant_share=0.0)
    detour = collector.summary()["detour_mean_m"]
    assert -3.0 < detour < 15.0, f"완벽한 안내인데 평균 우회가 {detour}m 입니다"


# ── 재현성 ────────────────────────────────────────────────────────


def test_the_same_seed_gives_the_same_numbers(lot: LotMap) -> None:
    """**표에 적은 숫자를 방어할 수 있는가.** 이 검사가 실험의 전제다."""
    _a, first = run(lot, seed=7)
    _b, again = run(lot, seed=7)
    assert first.summary() == again.summary()


def test_different_seeds_actually_differ(lot: LotMap) -> None:
    """시드를 30개 돌리는 이유가 있으려면 시드가 결과를 바꿔야 한다."""
    _a, one = run(lot, seed=1)
    _b, two = run(lot, seed=2)
    assert one.summary() != two.summary()


# ── 매트릭스 ──────────────────────────────────────────────────────


def test_a_matrix_cell_runs_and_reports(tmp_path) -> None:
    """하네스 한 판이 끝까지 돌고 표와 그림이 나오는가."""
    from sim.experiments.report import build
    from sim.experiments.run_matrix import Cell, run_cell

    scenario = replace(load_named("light"), duration=180.0)
    out = tmp_path / "run"
    out.mkdir()

    rows = [
        run_cell(Cell("light", "greedy_nearest", recovery, seed), scenario, out)
        for recovery in ("global_rematch", "local_reassign")
        for seed in (0, 1)
    ]
    (out / "matrix.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )

    text = build(out).read_text(encoding="utf-8")
    assert "global_rematch" in text and "local_reassign" in text
    assert "시드 2개" in text
    assert (out / "ledger").is_dir(), "보상 원장이 저장되지 않았습니다 (D-008)"


def test_every_allocator_runs_without_crashing() -> None:
    """전략을 등록만 해두고 한 번도 안 돌려 보면 발표 당일에 터진다."""
    from sim.control.allocators import api as alloc_api
    from sim.experiments.run_matrix import Cell, run_cell

    scenario = replace(load_named("busy"), duration=150.0)
    for name in alloc_api.available_names():
        row = run_cell(Cell("busy", name, "global_rematch", 0), scenario, None)
        assert row["parked"] > 0, f"{name} 이 한 대도 세우지 못했습니다"


def test_the_baseline_scenario_still_parks_cars() -> None:
    """무안내라고 아무도 못 세우면 비교가 아니라 고장이다.

    실제로 한 번 그랬다 — 유도선 없이 통로를 순회하는 경로가 잘못 잘려 900초
    동안 0대가 주차했다.
    """
    from sim.experiments.run_matrix import Cell, run_cell

    scenario = replace(load_named("baseline"), duration=180.0)
    row = run_cell(Cell("baseline", "none", "global_rematch", 0), scenario, None)
    assert row["parked"] > 5, f"무안내 모드가 {row['parked']}대만 세웠습니다"


def test_guidance_beats_no_guidance_on_the_same_seed() -> None:
    """**발표의 첫 슬라이드** (D-010). 이게 뒤집히면 발표할 것이 없다."""
    from sim.experiments.run_matrix import Cell, run_cell

    scenario = replace(load_named("busy"), duration=300.0)
    blind = run_cell(Cell("busy", "none", "global_rematch", 0), scenario, None)
    guided = run_cell(Cell("busy", "greedy_nearest", "global_rematch", 0), scenario, None)

    assert guided["park_time_mean"] < blind["park_time_mean"], (
        f"안내 {guided['park_time_mean']}초 vs 무안내 {blind['park_time_mean']}초"
    )
    assert guided["parked"] > blind["parked"]
