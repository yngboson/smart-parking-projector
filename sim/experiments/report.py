"""비교표와 박스플롯 — 매트릭스 결과를 발표에 쓸 모양으로 만든다.

    python -m sim.experiments.report runs/<run_id>

만드는 것:

    report.md        전략별 비교표 (마크다운, 그대로 슬라이드에 붙일 수 있다)
    park_time.png    주차 소요시간 분포
    detour.png       우회 거리 분포
    incidents.png    강탈 건수 분포

**평균만 적지 않는다.** 이 연구가 비교하려는 것은 사고가 났을 때의 꼬리다.
`fairness_weighted` 처럼 평균을 희생해 최악을 줄이는 전략은 평균만 보면 나쁜
전략으로 보인다. 그래서 표에는 평균과 p95 를 나란히 적고, 그림은 박스플롯으로
그린다 — 분포를 통째로 보여주는 것이 "이 차이가 우연인가"에 답하는 가장 정직한
방법이다.

**시드 수를 표에 적는다.** 표만 떼어 가면 그 숫자가 몇 판에서 나온 것인지 알 수
없게 되고, 발표에서 방어할 수 없다.
"""

from __future__ import annotations

import argparse
import json
import statistics as stats
from pathlib import Path
from typing import Sequence

METRICS = [
    ("park_time_mean", "평균 주차소요(초)", "낮을수록"),
    ("park_time_p95", "p95 주차소요(초)", "낮을수록"),
    ("detour_mean_m", "평균 우회(m)", "낮을수록"),
    ("detour_p95_m", "p95 우회(m)", "낮을수록"),
    ("parked", "주차 완료(대)", "높을수록"),
    ("victims", "피해 차량(대)", "낮을수록"),
    ("reroutes", "재배정", "낮을수록"),
    ("reshuffles", "휘말린 재배치", "낮을수록"),
    ("forced_merges", "강제 합류", "낮을수록"),
]

PLOTS = [
    ("park_time_mean", "park_time.png", "평균 주차 소요시간 (초)"),
    ("detour_mean_m", "detour.png", "평균 우회 거리 (m)"),
    ("victims", "incidents.png", "자리를 빼앗긴 차량 수"),
]

NEEDS_GUIDANCE = {"victims", "reroutes", "reshuffles"}
"""안내가 없으면 **정의되지 않는** 지표들.

무안내 모드에는 예약이 없으므로 빼앗길 자리도 없다. 그것을 '강탈 0건'으로 적으면
"안내를 켰더니 피해자가 5.2명 생겼다"로 읽힌다 — 사실이 아니다. 비교 대상이 아닌
것은 비교하지 않는다.
"""

KOREAN_FONTS = ["AppleGothic", "Apple SD Gothic Neo", "Malgun Gothic", "NanumGothic"]


def read(run_dir: Path) -> tuple[list[dict], dict]:
    rows = [
        json.loads(line)
        for line in (run_dir / "matrix.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return rows, meta


def group(rows: Sequence[dict]) -> dict[str, list[dict]]:
    """전략 조합별로 묶는다. 무엇이 달라졌는지 이름에서 보이도록 축약한다."""
    out: dict[str, list[dict]] = {}
    allocators = {r["allocator"] for r in rows}
    recoveries = {r["recovery"] for r in rows}

    for r in rows:
        if len(allocators) > 1 and len(recoveries) > 1:
            key = f"{r['allocator']} + {r['recovery']}"
        elif len(allocators) > 1:
            key = r["allocator"]
        else:
            key = r["recovery"]
        out.setdefault(key, []).append(r)
    return out


# ── 표 ────────────────────────────────────────────────────────────


def table(rows: Sequence[dict], meta: dict) -> str:
    groups = group(rows)
    seeds = sorted({r["seed"] for r in rows})

    head = ["전략"] + [label for _k, label, _d in METRICS]
    lines = [
        "| " + " | ".join(head) + " |",
        "|" + "|".join(["---"] * len(head)) + "|",
    ]

    for name, cells in sorted(groups.items()):
        row = [name]
        for key, _label, _d in METRICS:
            row.append(_cell([c[key] for c in cells if c.get(key) is not None]))
        lines.append("| " + " | ".join(row) + " |")

    duration = meta.get("duration", rows[0].get("duration", 0)) if rows else 0
    caption = (
        f"\n시드 {len(seeds)}개 × {duration:.0f}초. 값은 **평균 ± 표준편차**이며, "
        f"편차가 적힌 것은 시드마다 결과가 다르다는 뜻이다.\n"
    )
    return "\n".join(lines) + "\n" + caption


def _cell(values: Sequence[float]) -> str:
    if not values:
        return "—"
    m = stats.mean(values)
    if len(values) < 2:
        return f"{m:.1f}"
    sd = stats.stdev(values)
    return f"{m:.1f}" if sd < 0.05 else f"{m:.1f} ± {sd:.1f}"


def deltas(rows: Sequence[dict]) -> str:
    """무안내 대비 안내가 얼마나 나은가 — 발표의 첫 슬라이드 (D-010)."""
    groups = group(rows)
    base_key = next((k for k in groups if k.startswith("none")), None)
    if base_key is None or len(groups) < 2:
        return ""

    base = groups[base_key]
    other = next(k for k in groups if k != base_key)
    lines = [
        "",
        "### 무안내 대비",
        "",
        "| 지표 | 무안내 | 안내 | 차이 |",
        "|---|---|---|---|",
    ]

    for key, label, direction in METRICS:
        b = [c[key] for c in base if c.get(key) is not None]
        g = [c[key] for c in groups[other] if c.get(key) is not None]
        if not b or not g:
            continue
        gm = stats.mean(g)

        if key in NEEDS_GUIDANCE:
            lines.append(f"| {label} | 해당 없음 | {gm:.1f} | — |")
            continue

        bm = stats.mean(b)
        change = "—" if abs(bm) < 1e-9 else f"{(gm - bm) / abs(bm) * 100.0:+.0f}%"
        lines.append(f"| {label} ({direction} 좋다) | {bm:.1f} | {gm:.1f} | **{change}** |")

    lines += [
        "",
        "'해당 없음' 은 무안내 모드에 **예약이 없어** 그 사건이 정의되지 않는다는 "
        "뜻이다. 빼앗길 자리가 없으므로 강탈도 재배정도 0 이 아니라 성립하지 않는다.",
    ]
    return "\n".join(lines) + "\n"


# ── 그림 ──────────────────────────────────────────────────────────


def plots(rows: Sequence[dict], out: Path) -> list[Path]:
    try:
        import matplotlib
    except ImportError:
        print("matplotlib 이 없어 그림은 건너뜁니다 (pip install -e '.[dev]')")
        return []

    matplotlib.use("Agg")           # 화면 없는 환경에서도 그린다
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    _use_korean_font(matplotlib, font_manager)

    groups = group(rows)
    names = sorted(groups)
    made = []

    for key, filename, label in PLOTS:
        data = [[c[key] for c in groups[n] if c.get(key) is not None] for n in names]
        if not any(data):
            continue

        fig, ax = plt.subplots(figsize=(max(6.0, 1.7 * len(names)), 4.2))
        ax.boxplot(data, tick_labels=names, showmeans=True)
        ax.set_ylabel(label)
        ax.set_title(f"{label} — 시드 {len(data[0])}개")
        ax.grid(axis="y", alpha=0.3)
        fig.autofmt_xdate(rotation=20, ha="right")
        fig.tight_layout()
        path = out / filename
        fig.savefig(path, dpi=150)
        plt.close(fig)
        made.append(path)

    return made


def _use_korean_font(matplotlib, font_manager) -> None:
    """한글이 네모로 나오면 발표에 못 쓴다. 있는 폰트 중 하나를 골라 쓴다."""
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in KOREAN_FONTS:
        if name in installed:
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


# ── CLI ───────────────────────────────────────────────────────────


def build(run_dir: Path) -> Path:
    rows, meta = read(run_dir)
    if not rows:
        raise SystemExit(f"{run_dir}/matrix.jsonl 이 비어 있습니다")

    body = [
        f"# {meta.get('scenario', run_dir.name)} — 전략 비교",
        "",
        meta.get("description", "").strip(),
        "",
        table(rows, meta),
        deltas(rows),
    ]

    made = plots(rows, run_dir)
    if made:
        body += ["", "### 분포", ""]
        body += [f"![{p.stem}]({p.name})" for p in made]
        body.append("")

    path = run_dir / "report.md"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="매트릭스 결과로 비교표와 그래프를 만든다")
    ap.add_argument("run_dir", help="run_matrix 가 만든 디렉터리")
    args = ap.parse_args(argv)

    path = build(Path(args.run_dir))
    print(path.read_text(encoding="utf-8"))
    print(f"\n→ {path}")


if __name__ == "__main__":
    main()
