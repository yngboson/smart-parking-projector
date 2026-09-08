"""프레임을 파일로 남긴다 — 발표장의 보험.

라이브 스트리밍(WebSocket)이 화려하지만, 발표 당일 서버가 죽거나 노트북이 바뀌거나
네트워크가 막히는 일은 실제로 일어난다. 그래서 **같은 프레임 포맷**을 파일로도
남긴다 (docs/DECISIONS.md D-005). 뷰어는 둘을 구분하지 않는다.

    runs/<run_id>/
      meta.json     도면 이름, 설정, 시드, 프레임 수 — 무엇을 녹화한 것인지
      trace.jsonl   한 줄에 프레임 하나

JSON Lines 인 이유: 뷰어가 스트리밍처럼 한 줄씩 읽을 수 있고, 파일이 중간에
잘려도 거기까지는 재생된다. 단일 JSON 배열이면 마지막 괄호가 없는 순간 전부 못 읽는다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from sim.common.lotmap import LotMap
from sim.world.simulation import Frame, SimConfig, Simulation

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "runs"


class TraceWriter:
    """프레임을 `trace.jsonl` 로 흘려 쓴다."""

    def __init__(self, run_dir: Path | str, lot_name: str, config: SimConfig) -> None:
        self.dir = Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trace.jsonl"
        self._fh = self.path.open("w", encoding="utf-8")
        self.frames = 0
        self._meta = {"layout": lot_name, "config": asdict(config)}

    def write(self, frame: Frame) -> None:
        json.dump(frame.to_dict(), self._fh, ensure_ascii=False, separators=(",", ":"))
        self._fh.write("\n")
        self.frames += 1

    def close(self, last: Frame | None = None) -> Path:
        self._fh.close()
        meta = dict(self._meta)
        meta["frames"] = self.frames
        if last is not None:
            meta["duration"] = round(last.t, 2)
            meta["kpi"] = last.kpi
        (self.dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return self.path

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc) -> None:
        if not self._fh.closed:
            self.close()


def record(
    lot: LotMap,
    config: SimConfig,
    duration: float,
    run_dir: Path | str,
    stride: int = 2,
) -> Path:
    """시뮬레이션을 끝까지 돌리며 녹화한다.

    :param stride: 몇 틱마다 한 프레임을 남길 것인가. 기본 2 = 초당 5프레임.

    물리는 0.1초 간격으로 풀되 녹화는 솎아낸다. 뷰어가 프레임 사이를 보간하므로
    화면은 그대로이고 파일은 절반이 된다. 발표용 녹화본을 저장소에 넣어야 하므로
    크기가 중요하다.

    솎아내는 일은 `Simulation.run(stride=…)` 이 한다. 여기서 프레임을 골라 버리면
    안 된다 — 주차면 상태와 유도선 폴리라인은 **델타**로 나가므로, 발행된 줄 알고
    버린 변화는 영원히 사라진다. 시뮬레이션만이 "무엇을 아직 안 보냈는지" 안다.
    """
    sim = Simulation(lot, config=config)
    writer = TraceWriter(run_dir, lot.name, config)
    last: Frame | None = None
    for frame in sim.run(duration, stride=stride):
        writer.write(frame)
        last = frame
    return writer.close(last)


def read(path: Path | str) -> Iterable[dict]:
    """녹화본을 한 줄씩 읽는다. 잘린 마지막 줄은 조용히 버린다."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                return


def main() -> None:
    from sim.world.lot_builder import GridSpec, build_grid_lot

    ap = argparse.ArgumentParser(description="시뮬레이션을 녹화해 trace.jsonl 로 저장")
    ap.add_argument("--layout", default="layouts/mid_grid_120.json")
    ap.add_argument("--out", default="runs/demo", help="녹화본을 넣을 디렉터리")
    ap.add_argument("--duration", type=float, default=600.0)
    ap.add_argument("--arrival-rate", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--stride", type=int, default=2, help="몇 틱마다 한 프레임 (2 = 초당 5장)")
    args = ap.parse_args()

    try:
        lot = LotMap.load(args.layout)
    except FileNotFoundError:
        lot = build_grid_lot(GridSpec())

    config = SimConfig(seed=args.seed, arrival_rate=args.arrival_rate)
    path = record(lot, config, args.duration, args.out, stride=args.stride)

    size = path.stat().st_size
    meta = json.loads((path.parent / "meta.json").read_text(encoding="utf-8"))
    print(f"녹화 완료: {path}")
    print(f"  {meta['frames']}프레임 · {meta['duration']:.0f}초 · {size / 1024:.0f} KB")
    print(f"  주차 완료 {meta['kpi']['parked_total']}대 · 강탈 {meta['kpi']['stolen']}건")


if __name__ == "__main__":
    main()
