"""실험 매트릭스 — 같은 조건 위에서 전략만 바꿔 여러 시드로 돌린다.

    python -m sim.experiments.run_matrix --scenario rush_hour --seeds 30
    python -m sim.experiments.run_matrix --scenario busy --recovery global_rematch,reserve_pool
    python -m sim.experiments.run_matrix --scenario busy --compare-baseline --seeds 10

**왜 시드를 30개나 돌리는가.** 강탈은 드문 사건이다. 시드 하나로는 "전략이 좋아서"
와 "그날 운이 좋아서"가 구분되지 않는다. 복구 전략 여섯을 비교하는 것이 이 연구의
결론이므로(D-009), 그 비교가 우연이 아님을 보이지 못하면 결론이 없는 것과 같다.

**한 칸 = (시나리오, 할당 전략, 복구 전략, 시드) 하나.** 칸끼리는 완전히 독립이라
프로세스로 나눠 돌린다. 칸 하나의 결과는 `matrix.jsonl` 한 줄이고, 번호판별
보상 원장은 `ledger/` 아래 따로 쌓인다 (D-008 — 기록만, 정책 없음).

    runs/<run_id>/
      meta.json                무엇을 어떤 조건으로 돌렸는가
      matrix.jsonl             칸 하나당 한 줄
      ledger/<칸>-<시드>/       번호판별 누적

`--compare-baseline` 은 **발표의 첫 슬라이드**를 위한 것이다. 같은 시드로 안내
모드와 무안내 모드를 나란히 돌린다 (D-010). "왜 이 시스템이 필요한가"는 없을 때가
어떤지를 같은 조건에서 보여줘야 답이 된다.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

from sim.metrics.collector import MetricsCollector
from sim.scenarios.loader import SCENARIO_DIR, Scenario, load
from sim.world.lot_builder import GridSpec, build_grid_lot
from sim.world.simulation import Simulation

RUNS = Path("runs")

STRIDE = 10
"""지표 수집 간격(틱). 사건은 프레임을 솎아내도 잃지 않는다 (D-005)."""


@dataclass(frozen=True, slots=True)
class Cell:
    """매트릭스의 한 칸. 이것 하나가 시뮬레이션 한 판이다."""

    scenario: str
    allocator: str
    recovery: str
    seed: int

    @property
    def label(self) -> str:
        return f"{self.scenario}·{self.allocator}·{self.recovery}"

    @property
    def slug(self) -> str:
        return f"{self.scenario}-{self.allocator}-{self.recovery}-{self.seed:03d}"


def run_cell(cell: Cell, scenario: Scenario, out: Path | None) -> dict:
    """칸 하나를 돌리고 요약을 돌려준다. 프로세스 하나가 이것만 한다."""
    lot = build_grid_lot(GridSpec())
    control = scenario.build_control(
        lot, recovery=cell.recovery, allocator=cell.allocator
    )
    sim = Simulation(lot, control=control, config=scenario.config_for(cell.seed))
    collector = MetricsCollector(lot)

    started = time.perf_counter()
    last = None
    for frame in sim.run(scenario.duration, stride=STRIDE):
        collector.observe(sim, frame)
        last = frame
    collector.finish(sim)

    if out is not None:
        collector.write(out / "ledger" / cell.slug)

    return {
        **asdict(cell),
        "label": cell.label,
        "duration": scenario.duration,
        "wall_s": round(time.perf_counter() - started, 1),
        **collector.summary(),
        "forced_merges": sim.traffic.forced_merges,
        "occupancy_end": last.kpi["occupancy"] if last else 0.0,
    }


def _work(args) -> dict:
    """프로세스 진입점. `ProcessPoolExecutor` 는 최상위 함수만 보낼 수 있다."""
    cell, path, out = args
    return run_cell(cell, load(path), out)


def build_cells(
    scenario: Scenario,
    allocators: Sequence[str],
    recoveries: Sequence[str],
    seeds: Sequence[int],
) -> list[Cell]:
    return [
        Cell(scenario.name, a, r, s)
        for a in allocators
        for r in recoveries
        for s in seeds
    ]


def run_matrix(
    path: Path,
    cells: Sequence[Cell],
    out: Path,
    jobs: int = 0,
    quiet: bool = False,
) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    jobs = jobs or max(1, (os.cpu_count() or 2) - 1)

    rows: list[dict] = []
    started = time.perf_counter()
    payload = [(c, path, out) for c in cells]

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for i, row in enumerate(pool.map(_work, payload), 1):
            rows.append(row)
            if not quiet:
                print(
                    f"  [{i:3d}/{len(cells)}] {row['label']} 시드 {row['seed']:>2} — "
                    f"주차 {row['parked']:3d}대 · 평균 {row['park_time_mean']:6.1f}초 "
                    f"· 강탈 {row['victims']:2d}건 ({row['wall_s']:.0f}초)",
                    flush=True,
                )

    rows.sort(key=lambda r: (r["label"], r["seed"]))
    (out / "matrix.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )

    scenario = load(path)
    (out / "meta.json").write_text(
        json.dumps(
            {
                "scenario": scenario.name,
                "scenario_file": str(path),
                "description": scenario.description.strip(),
                "duration": scenario.duration,
                "cells": len(cells),
                "allocators": sorted({c.allocator for c in cells}),
                "recoveries": sorted({c.recovery for c in cells}),
                "seeds": sorted({c.seed for c in cells}),
                "config": asdict(scenario.config),
                "wall_s": round(time.perf_counter() - started, 1),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return out


# ── CLI ───────────────────────────────────────────────────────────


def _resolve(name: str) -> Path:
    """`--scenario` 는 파일 경로여도 되고 이름이어도 된다."""
    p = Path(name)
    if p.exists():
        return p
    named = SCENARIO_DIR / f"{Path(name).stem}.yaml"
    if not named.exists():
        raise SystemExit(
            f"시나리오 {name!r} 을 찾을 수 없습니다. "
            f"있는 것: {', '.join(sorted(p.stem for p in SCENARIO_DIR.glob('*.yaml')))}"
        )
    return named


def _split(value: str | None, fallback: Iterable[str]) -> list[str]:
    if not value:
        return list(fallback)
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="전략 비교 매트릭스를 돌린다")
    ap.add_argument("--scenario", default="rush_hour", help="YAML 경로 또는 이름")
    ap.add_argument("--seeds", type=int, default=0, help="앞에서부터 몇 개의 시드를 쓸 것인가")
    ap.add_argument("--allocator", help="쉼표로 구분. 기본은 시나리오가 지정한 것 하나")
    ap.add_argument("--recovery", help="쉼표로 구분. 기본은 복구 전략 6종 전부")
    ap.add_argument("--duration", type=float, help="시나리오의 실행 길이를 덮어쓴다(초)")
    ap.add_argument("--jobs", type=int, default=0, help="병렬 프로세스 수 (0 = 코어-1)")
    ap.add_argument("--out", help="결과 디렉터리 (기본 runs/<시나리오>-<시각>)")
    ap.add_argument(
        "--compare-baseline",
        action="store_true",
        help="같은 시드로 안내 모드와 무안내 모드를 나란히 돌린다 (D-010)",
    )
    args = ap.parse_args(argv)

    path = _resolve(args.scenario)
    scenario = load(path)
    if args.duration:
        scenario = replace(scenario, duration=args.duration)
        path = _write_override(scenario, path)

    seeds = _extend(list(scenario.seeds), args.seeds) if args.seeds else list(scenario.seeds)

    if args.compare_baseline:
        allocators = ["none", scenario.allocator]
        recoveries = [scenario.recovery]
    else:
        from sim.control.recovery import api as recovery_api

        allocators = _split(args.allocator, [scenario.allocator])
        recoveries = _split(args.recovery, recovery_api.available_names())

    cells = build_cells(scenario, allocators, recoveries, seeds)
    out = (
        Path(args.out)
        if args.out
        else RUNS / f"{scenario.name}-{time.strftime('%m%d-%H%M')}"
    )

    print(
        f"{scenario.name}: {len(cells)}칸 "
        f"(할당 {len(allocators)} × 복구 {len(recoveries)} × 시드 {len(seeds)}) "
        f"× {scenario.duration:.0f}초 → {out}"
    )
    run_matrix(path, cells, out, jobs=args.jobs)
    print(f"\n완료. 표와 그래프: python -m sim.experiments.report {out}")


def _extend(seeds: list[int], want: int) -> list[int]:
    """시나리오에 적힌 시드가 모자라면 이어서 채운다. 재현성을 위해 결정적으로."""
    out = list(seeds[:want])
    nxt = (max(seeds) + 1) if seeds else 0
    while len(out) < want:
        out.append(nxt)
        nxt += 1
    return out


def _write_override(scenario: Scenario, original: Path) -> Path:
    """길이를 덮어쓴 시나리오를 임시 YAML 로 남긴다.

    프로세스마다 시나리오를 **파일에서** 다시 읽기 때문이다 — 데이터클래스를
    피클로 보내는 것보다, 무엇을 돌렸는지가 파일로 남는 편이 재현에 낫다.
    """
    import yaml

    raw = yaml.safe_load(original.read_text(encoding="utf-8"))
    raw.setdefault("run", {})["duration"] = scenario.duration
    tmp = RUNS / "_scenario_override.yaml"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return tmp


if __name__ == "__main__":
    main()
