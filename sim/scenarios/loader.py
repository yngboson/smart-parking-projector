"""시나리오 — 실험 한 판의 조건을 데이터로 적어 둔다.

**조건을 코드에 적으면 실험이 아니라 일화가 된다.** 같은 조건을 다시 돌릴 수 없고,
발표에서 "이 숫자는 어떤 설정에서 나왔나요"에 답할 수 없다. 그래서 도착률·성향
분포·시드·쓸 전략 이름을 YAML 파일 하나에 모아 두고, 실행도 보고도 그 파일을 가리킨다.

    scenario = load("sim/scenarios/rush_hour.yaml")
    for seed in scenario.seeds:
        sim = Simulation(lot, control, scenario.config_for(seed))

전략 이름을 여기 두는 이유: 복구 전략 6종 비교(D-009)는 **같은 시나리오 위에서**
전략만 바꿔 돌려야 한다. 시나리오가 전략을 품고 있으면 그 대응이 명시적으로 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from sim.world.simulation import SimConfig

SCENARIO_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class Scenario:
    """실험 한 판의 조건 전부."""

    name: str
    description: str
    config: SimConfig
    allocator: str
    recovery: str
    duration: float
    seeds: tuple[int, ...]

    def config_for(self, seed: int) -> SimConfig:
        """이 시나리오를 주어진 시드로 돌릴 설정."""
        return replace(self.config, seed=seed)

    def build_control(self, lot, recovery: str | None = None):
        """이 시나리오가 지정한 전략으로 관제를 만든다.

        :param recovery: 시나리오의 복구 전략을 덮어쓴다. 전략 비교 실험이
            **같은 시나리오 위에서 전략만** 바꿔 돌리기 위한 통로다.
        """
        from sim.control.api import NullControl
        from sim.control.system import ProjectorControl

        if self.allocator == "none":
            # 무안내 베이스라인 (D-010). 관제를 갈아끼우는 것만으로 모드가 바뀐다.
            return NullControl()

        return ProjectorControl(
            lot,
            allocator=self.allocator,
            recovery=recovery or self.recovery,
            slot_sensor_mode=self.config.slot_sensor_mode,
        )


def load(path: str | Path) -> Scenario:
    """YAML 하나를 읽어 `Scenario` 로 만든다.

    모르는 키는 조용히 넘기지 않고 바로 실패시킨다. 오타 하나로 도착률이 기본값으로
    돌아간 채 30번 돌리고 나서야 알아채는 일을 막는다.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    sim_fields = {f for f in SimConfig.__dataclass_fields__}
    given: dict[str, Any] = raw.get("simulation") or {}
    unknown = set(given) - sim_fields
    if unknown:
        raise ValueError(
            f"{Path(path).name}: 알 수 없는 simulation 항목 {sorted(unknown)}. "
            f"쓸 수 있는 것: {sorted(sim_fields)}"
        )

    control = raw.get("control") or {}
    run = raw.get("run") or {}

    return Scenario(
        name=raw.get("name") or Path(path).stem,
        description=raw.get("description", ""),
        config=SimConfig(**given),
        allocator=control.get("allocator", "greedy_nearest"),
        recovery=control.get("recovery", "global_rematch"),
        duration=float(run.get("duration", 900.0)),
        seeds=tuple(run.get("seeds") or [0]),
    )


def available() -> list[str]:
    """저장소에 들어 있는 시나리오 이름들."""
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml"))


def load_named(name: str) -> Scenario:
    path = SCENARIO_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"시나리오 {name!r} 이 없습니다. 있는 것: {', '.join(available())}"
        )
    return load(path)
