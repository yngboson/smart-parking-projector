# CLAUDE.md — 이 저장소에서 작업하는 에이전트를 위한 계약서

> 다른 세션이 이 저장소를 이어받을 때 **가장 먼저 읽어야 하는 문서**입니다.
> 설계 배경은 `docs/PLAN.md`, 결정의 근거는 `docs/DECISIONS.md` 에 있습니다.

## 이 프로젝트가 무엇인가

야외 주차장에 **센서 + 빔 프로젝터**를 설치해, 입구에서 번호판을 인식하고 차량마다 빈 자리를
할당한 뒤, **바닥에 차량별 고유 색 유도선**을 투사해 주차 요원 없이 한 번에 자리를 찾아가게
하는 시스템 — 그 **개념검증 시뮬레이터**입니다. 공모전 발표가 목적입니다.

연구의 핵심 질문은 이것입니다:

> **비협조적 운전자가 남에게 할당된 자리를 강탈했을 때, 관제 알고리즘이 얼마나 매끄럽게
> 복구하는가?**

---

## 절대 규칙 — 3계층 분리 (이걸 어기면 연구가 무효가 됩니다)

```
sim/world/    ① 주차장: 도면, 물리, 센서, 프로젝터
sim/control/  ② 관제 알고리즘: 할당, 경로, 복구 전략
sim/agents/   ③ 개별 차량: 운전자 성향, 인지, 판단
sim/common/   ↑ 세 계층이 공유하는 불변 값 타입(DTO)만
```

### 1. import 금지 규칙

| 패키지 | import 해도 되는 것 | **금지** |
|---|---|---|
| `sim/control/` | `sim.common` 만 | `sim.agents`, `sim.world` |
| `sim/agents/`  | `sim.common` 만 | `sim.control`, `sim.world` |
| `sim/world/`   | `sim.common`, `sim.control`, `sim.agents` (배선 담당) | — |

배선(wiring)은 **`sim/world/simulation.py` 한 곳에서만** 합니다.

### 2. `compliance` 는 관제가 볼 수 없습니다

운전자의 비협조 성향(`DriverProfile.compliance`)은 `sim/agents/` 안에서만 존재합니다.
`sim/control/` 안에서 이 값을 읽는 코드는 **어떤 형태로든** 작성하면 안 됩니다.
우회로(전역 변수, 몽키패칭, `world` 를 통한 간접 조회 등)도 전부 위반입니다.

> **이유**: 알고리즘이 "누가 배신할지" 미리 알면 돌발 상황 대처 능력을 측정할 수 없습니다.
> 이 분리 자체가 실험 설계이자 발표의 핵심 논지입니다.

### 3. 관제가 볼 수 있는 것 = 실제 하드웨어가 줄 수 있는 것뿐

`sim/common/messages.py` 의 `SensorEvent` 만 관제에 전달됩니다. 전부 `frozen=True` 이고
필드는 id·타임스탬프·관측 스칼라뿐이며, 차량 객체 참조는 절대 넣지 않습니다.

- `VehicleEntered(plate, t, vehicle_class)` — 입구 ANPR
- `SlotOccupancyChanged(slot_id, occupied, t, plate?)` — 슬롯 센서
- `LaneDetection(node_id, plate, t)` — 통로 검지기 (유도선 이탈 감지용)
- `VehicleExited(plate, t)`

**허용됨**: 번호판별 과거 관측 이력을 관제가 누적·학습하는 것. 센서 기록의 집계일 뿐이며
실제 시스템도 할 수 있는 일입니다 (복구 전략 `reputation_aware` 가 이걸 씁니다).

### 4. 헷갈리기 쉬운 경계

| 무엇 | 어디에 | 왜 |
|---|---|---|
| 차량 제원·제어 입력 (`VehicleSpec`, `ControlInput`) | `common/vehicle.py` | 세 계층이 다 알아야 한다. 운전자는 자기 회전반경을 알고, world 는 적분하고, control 은 차종 제약을 본다 |
| 주차 진입 궤적 기하 (`maneuver.py`) | `common/` | 순수 기하다. 정책도 제어도 없다 |
| 자전거 모델 적분 (`physics.py`) | `world/` | 시뮬레이터의 물리 |
| 경로 추종 · 속도 조절 (`driving.py`) | `agents/` | **운전 기술이지 시뮬레이터 기능이 아니다.** 관제가 선을 그려줘도 따라가는 건 사람이고, 사람마다 잘하고 못한다 |

### 5. 커밋 전 필수 확인

```bash
pytest tests/test_layer_isolation.py -v     # 반드시 통과
grep -rn "compliance" sim/control/          # 결과가 비어 있어야 함
```

---

## 이번 범위에서 하지 않는 것

- **보상 정책(주차 할인 등) 산정 로직** — `reroute_count`, `detour_distance_m` 등을
  `runs/<run_id>/compensation_ledger.jsonl` 에 **기록만** 합니다. 정책 설계는 추후 논의이므로
  임의로 구현하지 마세요.
- 실제 번호판 인식 영상처리 (시뮬레이션에서는 완벽 인식으로 가정)
- 프로젝터 캘리브레이션·왜곡 보정 (이상적 투사로 가정)
- 비협조 행동 중 배회·통로 정차·이중주차 — 이번엔 **"자리 강탈" 하나로 한정**합니다.
  구조는 확장 가능하게 두되, 요청 없이 추가하지 마세요.

---

## 디렉터리 규약

```
sim/common/      geometry.py, ids.py, messages.py, lotmap.py, vehicle.py, maneuver.py
sim/world/       lot_builder.py, physics.py, sensors.py, projector.py, simulation.py
sim/control/     api.py, state.py, routing.py, cost.py, allocators/, recovery/
sim/agents/      driver.py, vehicle.py, perception.py, driving.py
sim/scenarios/   *.yaml  (도착률, 성향 분포, 시드, 사용할 전략 이름)
sim/metrics/     collector.py, trace_writer.py
sim/experiments/ run_matrix.py, report.py
layouts/         주차장 도면 JSON (코드에 좌표를 하드코딩하지 말 것)
server/app.py    FastAPI: WebSocket 라이브 스트림 + 정적 서빙
viewer/          three.js 뷰어 — 빌드 스텝 없음 (ES 모듈 + importmap)
tests/
```

## 전략을 추가하는 방법

할당 전략과 복구 전략은 **플러그인**입니다. 코드를 고치지 말고 파일을 추가하세요.

1. `sim/control/allocators/` 또는 `sim/control/recovery/` 에 새 모듈 추가
2. 해당 디렉터리의 `api.py` 프로토콜을 구현
3. 시나리오 YAML 에서 이름으로 선택
4. `sim/experiments/run_matrix.py` 의 비교 매트릭스에 이름 추가

기존 전략(특히 기본값 `global_rematch`)을 **다른 것으로 바꾸지 마세요.** 비교 실험 대상입니다.

## 실행

```bash
pytest tests/ -v                                    # 전체 테스트
uvicorn server.app:app --reload                     # 라이브 뷰어 → localhost:8000
python -m sim.experiments.run_matrix --scenario sim/scenarios/rush_hour.yaml --seeds 30
python -m sim.experiments.report runs/<run_id>      # 비교표·그래프
```

## 커밋 규칙

- `docs/PLAN.md` 의 구현 순서 단계마다 커밋 — 다른 세션이 히스토리로 진행 상황을 파악합니다.
- **토큰·자격증명은 절대 커밋 금지.** `.gitignore` 를 먼저 확인하고,
  `git ls-files | grep -Ei "token|secret|credential|\.env|\.key|\.pem"` 이 비어 있어야 합니다.
- 설계 결정을 바꿔야 한다면 `docs/DECISIONS.md` 에 근거와 함께 새 항목을 추가하세요.
  기존 결정을 조용히 뒤집지 마세요.
