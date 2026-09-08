"""계층 분리를 코드 레벨에서 강제하는 테스트.

**이 저장소에서 가장 중요한 테스트다.**

이 연구의 질문은 "관제가 예측 불가능한 돌발 상황에 대처할 수 있는가"이다.
관제 코드가 차량의 비협조 성향(compliance)을 조금이라도 참조할 수 있으면
알고리즘이 정답을 훔쳐보게 되어 실험이 무의미해진다.

관례나 주석으로는 못 막는다. 그래서 import 그래프를 직접 파싱해서 검사한다.
이 테스트가 깨지면 기능이 아니라 **연구 설계가 깨진 것**이다.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SIM = REPO / "sim"

# 각 계층이 import 해서는 안 되는 패키지
FORBIDDEN: dict[str, set[str]] = {
    "sim.control": {"sim.agents", "sim.world"},
    "sim.agents": {"sim.control", "sim.world"},
}

WHY = {
    ("sim.control", "sim.agents"): (
        "관제가 차량 내부(운전자 성향, 현재 의도)를 들여다보게 된다. "
        "관제는 SensorEvent 로만 세상을 알아야 한다."
    ),
    ("sim.control", "sim.world"): (
        "관제가 시뮬레이터의 정답(실제 점유 상태, 차량 위치)에 직접 접근하게 된다. "
        "실제 하드웨어로는 얻을 수 없는 정보다."
    ),
    ("sim.agents", "sim.control"): (
        "차량이 관제의 내부 계획을 알게 된다. 운전자는 바닥에 그려진 자기 유도선만 본다."
    ),
    ("sim.agents", "sim.world"): (
        "차량이 시뮬레이터 전역 상태를 보게 된다. 운전자는 자기 시야만 안다."
    ),
}


def _python_files(package_dir: Path) -> list[Path]:
    return sorted(p for p in package_dir.rglob("*.py") if "__pycache__" not in p.parts)


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(path: Path) -> set[str]:
    """파일이 import 하는 모듈들의 절대 이름. 상대 import 도 해석한다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(path).rsplit(".", 1)[0] if path.name != "__init__.py" else _module_name(path)

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    found.add(node.module)
            else:
                base = package.split(".")
                up = node.level - 1
                base = base[: len(base) - up] if up else base
                target = ".".join(base + ([node.module] if node.module else []))
                found.add(target)
    return found


def _layer_files(layer: str) -> list[Path]:
    return _python_files(REPO / layer.replace(".", "/"))


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_does_not_import_forbidden_packages(layer: str) -> None:
    violations: list[str] = []
    for path in _layer_files(layer):
        for imported in _imported_modules(path):
            for banned in FORBIDDEN[layer]:
                if imported == banned or imported.startswith(banned + "."):
                    rel = path.relative_to(REPO).as_posix()
                    violations.append(
                        f"\n  {rel}\n"
                        f"      import {imported}\n"
                        f"      → {WHY[(layer, banned)]}"
                    )
    assert not violations, (
        f"{layer} 가 금지된 계층을 import 했습니다."
        + "".join(violations)
        + "\n\n  계층 분리 원칙은 CLAUDE.md 와 docs/DECISIONS.md D-001 을 참조하세요."
    )


def test_control_never_mentions_compliance() -> None:
    """관제 코드에 'compliance' 라는 단어 자체가 등장하면 안 된다.

    간접 조회, 문자열 키 접근, 주석으로 남긴 우회로까지 한 번에 잡기 위해
    토큰 수준에서 막는다.
    """
    hits: list[str] = []
    for path in _layer_files("sim.control"):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "compliance" in line:
                rel = path.relative_to(REPO).as_posix()
                hits.append(f"\n  {rel}:{lineno}: {line.strip()}")
    assert not hits, (
        "관제 코드가 운전자 성향(compliance)을 언급합니다. "
        "관제는 이 값의 존재조차 몰라야 합니다." + "".join(hits)
    )


def test_common_stays_a_pure_data_layer() -> None:
    """sim.common 은 누구나 import 하므로, 여기에 계층 의존이 생기면 경계가 무너진다."""
    violations: list[str] = []
    for path in _layer_files("sim.common"):
        for imported in _imported_modules(path):
            if imported.startswith("sim.") and not imported.startswith("sim.common"):
                rel = path.relative_to(REPO).as_posix()
                violations.append(f"\n  {rel}: import {imported}")
    assert not violations, (
        "sim.common 이 다른 계층을 import 합니다. 공용 영역은 데이터만 담아야 합니다."
        + "".join(violations)
    )


def test_sensor_events_are_immutable_and_carry_only_values() -> None:
    """관제에 전달되는 이벤트는 불변이어야 하고, 객체 참조를 품으면 안 된다."""
    from sim.common import messages as M

    events = [
        M.VehicleEntered,
        M.SlotOccupancyChanged,
        M.LaneDetection,
        M.VehicleExited,
    ]
    allowed = {"float", "bool", "str", "int", "PlateId", "SlotId", "NodeId",
               "VehicleClass", "PlateId | None"}

    for cls in events:
        assert dataclasses.is_dataclass(cls), f"{cls.__name__} 이 dataclass 가 아닙니다"
        params = getattr(cls, "__dataclass_params__")
        assert params.frozen, (
            f"{cls.__name__} 이 frozen 이 아닙니다. "
            "관제가 센서 기록을 수정할 수 있으면 안 됩니다."
        )
        for f in dataclasses.fields(cls):
            ann = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
            assert ann in allowed, (
                f"{cls.__name__}.{f.name} 의 타입 {ann!r} 은 센서 관측값이 아닙니다. "
                "실제 하드웨어가 관측할 수 있는 스칼라/식별자만 허용됩니다."
            )


def test_driver_profile_lives_only_in_agents() -> None:
    """운전자 성향 타입이 agents 밖으로 새어나가지 않았는지 확인한다."""
    for layer in ("sim.common", "sim.control", "sim.world"):
        for path in _layer_files(layer):
            text = path.read_text(encoding="utf-8")
            assert "class DriverProfile" not in text, (
                f"{path.relative_to(REPO).as_posix()} 에 DriverProfile 이 정의되어 있습니다. "
                "이 타입은 sim/agents/driver.py 에만 존재해야 합니다."
            )
