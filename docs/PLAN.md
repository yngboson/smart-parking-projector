# 주차장 유도선 시뮬레이션 — 개념검증(PoC) 구현 계획

> **진행 상황 (2026-09-09): 0~6단계 완료. 다음은 7단계(무안내 베이스라인).**
> 이어받는 세션은 [`HANDOFF.md`](HANDOFF.md) 를 먼저 읽으세요 —
> 완료 내역, 미해결 이슈, 다음 작업 지침이 정리돼 있습니다.
> 3단계에서 차간거리·교착을 어떻게 풀었는지는 [`DECISIONS.md`](DECISIONS.md) D-012 에 있습니다.

## Context

**최종 목표(장기)**: 실제 야외 주차장에 센서 + 빔 프로젝터를 설치해, 입구 번호판 인식으로 차량마다 빈 자리를 할당하고 바닥에 **차량별 고유 색 유도선**을 투사하여 주차 요원 없이 한 번에 자리를 찾아가게 하는 시스템.

**이번 세션의 범위(개념 연구)**: 가상 주차장 시뮬레이션 환경을 만들어
1. 자리 할당 알고리즘과 경로 안내 알고리즘을 설계·검증하고,
2. 공모전 발표에 쓸 그래픽 시뮬레이션을 만든다.

**해결하려는 핵심 난제**: 비협조적 운전자가 사전 할당된 자리를 무시하고 **타인에게 할당된 자리를 강탈**했을 때, 원래 주인이 갈 곳이 마땅치 않다. 이 돌발 상황에 관제 알고리즘이 얼마나 매끄럽게 대처하는지를 정직하게 검증하는 것이 실험의 본질이다.

**따라서 결정적인 설계 제약**: 관제 알고리즘은 차량의 비협조 성향(`compliance`)을 **절대 읽을 수 없어야 한다.** 알고리즘은 오직 실제 하드웨어가 줄 수 있는 **센서 관측값**만으로 강탈을 사후 인지하고 대응해야 한다. 이를 위해 주차장(환경)·관제(알고리즘)·개별 차량(에이전트) 코드를 물리적으로 분리하고, 그 경계를 테스트로 강제한다.

**보상 정책(주차 할인 등)은 이번 범위 밖.** 재탐색 횟수와 우회 거리를 차량별로 누적 기록만 해두고, 정책 설계는 추후 논의.

**협업 방식**: 이 저장소를 GitHub에 올려 **다른 Claude 세션도 이어받아 작업**한다. 따라서 프로젝트 컨텍스트(계층 분리 원칙 포함)를 저장소 안 문서로 남겨야 하며, 토큰·자격증명은 **절대 커밋하지 않는다.**

### 확정된 사항 (사용자 결정)
| 항목 | 결정 |
|---|---|
| 스택 | **Python 시뮬레이션 코어 + 웹 뷰어** |
| 연결 | **라이브(WebSocket) + 녹화본(trace 재생) 둘 다** — 동일 프레임 포맷 |
| 주차장 규모 | **중형 격자, 약 100~150면**, 통로 3~4개 + 순환 통로, 일방통행 구간 포함 |
| 주행 모델 | **자전거 모델** (조향각·최소 회전반경·후진 주차) |
| 비협조 행동 | **"할당 자리 무시 → 타인 자리 강탈" 하나로 한정** |
| 복구 정책 | **전체 재최적화(연쇄 재배치)를 기본**으로 하되, 후보 알고리즘 여러 개를 만들어 비교 |
| 베이스라인 | **포함** — 무안내(운전자 자율 탐색) vs 유도선 안내 |
| 차량 3D | 나중에 **STL 모델**을 받아 교체. 크기를 모르므로 **자동 스케일 + 수동 오버라이드** 필요 |
| 저장소 | GitHub **`smart-parking-projector`**, **Private** |
| 저장소 문서 | `CLAUDE.md`, `README.md`, `docs/PLAN.md`, `docs/DECISIONS.md` |

---

## 0단계 — Git / GitHub 셋업 (가장 먼저)

**전제조건 (사용자가 직접 실행)**
```
! gh auth login
```
`gh` 2.96.0 은 설치돼 있으나 **현재 어떤 GitHub 호스트에도 로그인돼 있지 않다.** 또한 git 전역 `user.name` / `user.email` 이 **비어 있어** 커밋이 실패하므로, 로그인 후 확인하고 없으면 설정한다 (계정 이메일: `yanagikana54@gmail.com`).

**작업 순서**
1. `git init` (현재 디렉터리는 git 저장소가 아니며 완전히 비어 있음), 기본 브랜치 `main`
2. **`.gitignore` 를 코드보다 먼저 커밋** — 사고를 원천 차단
3. 프로젝트 문서 4종 작성 (아래)
4. `gh repo create smart-parking-projector --private --source=. --remote=origin --push`
5. 푸시 직후 `git ls-files` 로 **실제 올라간 파일 목록을 눈으로 검수**하고 사용자에게 보고

### `.gitignore` — 토큰/자격증명 차단이 최우선
```gitignore
# ── 절대 커밋 금지: 자격증명 ──
.env
.env.*
*.token
*token*
*secret*
*credential*
*.pem
*.key
*.pfx
.netrc
.npmrc
auth.json
gh_token*
.claude/settings.local.json

# ── 개인/로컬 설정 ──
.claude/                 # 사용자 전역 지침(RTK 등)은 저장소에 넣지 않음
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.idea/
.vscode/

# ── 대용량 산출물 ──
runs/                    # trace.jsonl, KPI 로그 (샘플 1개만 예외 허용)
!runs/demo/trace.jsonl
*.mp4

# ── 3D 모델 (선택) ──
# STL 이 수십 MB 이면 Git LFS 로 전환. 우선은 커밋하되 크기를 확인한다.
```
> 커밋 전 `git status --porcelain` 결과에 위 패턴에 해당하는 파일이 하나라도 있으면 **중단하고 사용자에게 확인**한다.

### 저장소에 넣을 문서
| 파일 | 목적 | 핵심 내용 |
|---|---|---|
| `CLAUDE.md` | **다른 Claude 세션용 계약서** | 3계층 분리 원칙, `control` 에서 `agents`/`world` import 금지, `compliance` 를 관제 코드에서 참조 금지, 디렉터리 규약, `pytest tests/test_layer_isolation.py` 를 커밋 전 필수 실행, 시나리오/전략 추가 방법 |
| `README.md` | 사람(심사위원 포함)용 | 프로젝트 개요, 문제의식(자리 강탈), 실행법, 스크린샷 자리 |
| `docs/PLAN.md` | 구현 로드맵 | 이 계획서 전문 복사 — 다른 세션이 중간 단계부터 이어감 |
| `docs/DECISIONS.md` | 결정 기록(ADR) | 왜 Python+웹인지, 왜 계층을 나눴는지(실험 타당성), 왜 보상 로직을 제외했는지, 왜 복구 전략을 6종 비교하는지. **다른 세션이 임의로 뒤집는 것을 방지** |

> **사용자 전역 지침(`C:\Users\subpc\.claude\CLAUDE.md`, `RTK.md`)은 커밋하지 않는다.** 개인 도구(RTK) 설정이며 이 프로젝트와 무관하고, 사용자 머신에서는 어차피 자동 적용된다. 프로젝트용 `CLAUDE.md` 를 새로 작성한다.

**커밋 리듬**: 위 구현 순서의 각 단계 완료 시마다 커밋. 다른 세션이 어디까지 됐는지 히스토리로 파악할 수 있게 한다.

---

## 아키텍처 — 3계층 분리와 경계 강제

```
┌──────────────────────── sim/world  ① 주차장(환경 + 하드웨어) ────────────────────────┐
│  도면(LotMap) · 자전거모델 물리 · 충돌/차간거리 · 시간 진행                            │
│  센서: 입구 ANPR, 슬롯 점유센서, 통로 루프 검지기                                     │
│  액추에이터: 프로젝터(유도선 렌더 상태)                                                │
└───────────┬───────────────────────────────────────────────────┬──────────────────────┘
            │ SensorEvent (frozen DTO, 값만)                    │ 유도선/시야
            ▼                                                   ▼
┌──── sim/control  ② 관제 알고리즘 ────┐        ┌──── sim/agents  ③ 개별 차량 ────┐
│  센서 이벤트만으로 세계 모델 추정     │        │  DriverProfile(compliance …)     │
│  슬롯 할당 · 레인그래프 A* · 복구전략 │        │  perceive → decide → 조향/가감속  │
│  → GuidanceCommand 출력              │        │  유도선 따라가기 or 이탈 판단     │
│  ✗ agents/world 를 import 금지        │        │  ✗ control 을 import 금지        │
└──────────────────────────────────────┘        └──────────────────────────────────┘
```

**경계를 강제하는 방법**
- `sim/common/` 에만 세 계층이 공유하는 **불변 값 타입**을 둔다 (`Vec2`, `Pose`, `PlateId`, `SlotId`, `NodeId`, `SensorEvent`, `GuidanceCommand`). 객체 참조는 오가지 않는다.
- 배선(wiring)은 `sim/world/simulation.py` **한 곳에서만** 한다. 관제에 넘기는 것은 `SensorEvent` 리스트뿐.
- `tests/test_layer_isolation.py`: `ast` 로 각 패키지의 import 문을 파싱해 금지된 의존이 있으면 **테스트 실패**. 이게 이 프로젝트의 가장 중요한 테스트다 (발표 때 "저희는 이걸 코드 레벨에서 강제했습니다"로 쓸 수 있음).
- 런타임 방어: `SensorEvent` 는 `@dataclass(frozen=True)`, 필드는 id·타임스탬프·관측 스칼라만. `DriverProfile` 은 `sim/agents/` 밖으로 나가는 어떤 자료구조에도 등장하지 않는다.

**관제가 볼 수 있는 것 (실제 하드웨어로 가능한 관측만)**
- `VehicleEntered(plate, t, vehicle_class)` — 입구 ANPR
- `SlotOccupancyChanged(slot_id, occupied, t, plate?)` — 슬롯 센서. `plate` 유무를 `slot_sensor_mode = "anpr" | "presence"` 로 전환 가능. `presence` 모드에서는 관제가 "누가 훔쳤는지"를 추론해야 하므로 난이도가 올라간다 (확장 실험용, 기본은 `anpr`).
- `LaneDetection(node_id, plate, t)` — 통로 검지기. 이걸로 **유도선 이탈**을 감지한다.
- `VehicleExited(plate, t)`
- 과거 누적 관측 통계(번호판별 이탈 이력) — 센서 기록의 집계이므로 사용해도 계층 위반이 아니다.

**관제가 볼 수 없는 것**: `compliance`, 차량의 현재 의도, 차량이 지금 어느 슬롯을 노리는지, 차량 객체 자체.

---

## 디렉터리 구조

```
parkinglot/
├─ pyproject.toml
├─ sim/
│  ├─ common/          geometry.py, ids.py, messages.py, lotmap.py
│  ├─ world/           lot_builder.py, physics.py, sensors.py, projector.py, simulation.py
│  ├─ control/         api.py, state.py, routing.py, cost.py,
│  │                   allocators/{greedy_nearest,hungarian_batch,congestion_aware,zone_late_binding}.py
│  │                   recovery/{local_reassign,global_rematch,chain_shift,reserve_pool,
│  │                             reputation_aware,fairness_weighted}.py
│  ├─ agents/          driver.py, vehicle.py, perception.py
│  ├─ scenarios/       *.yaml (도착률, 성향 분포, 시드)
│  ├─ metrics/         collector.py, trace_writer.py
│  └─ experiments/     run_matrix.py, report.py
├─ layouts/            mid_grid_120.json   ← 주차장 도면 (데이터로 분리)
├─ server/app.py       FastAPI: WebSocket 라이브 + 정적 서빙 + trace 목록
├─ viewer/             빌드 스텝 없음 (ES 모듈 + importmap)
│  ├─ index.html
│  ├─ js/{scene,lot_floor,vehicles,guidance,palette,hud,source,controls}.js
│  └─ models/          사용자가 STL 을 넣는 곳 + models.json
└─ tests/
```

---

## 핵심 구현 상세

### 1) 주차장 도면 (`layouts/mid_grid_120.json`)
- 데이터 파일로 분리 — 규모 변경 시 코드 수정 불필요.
- 구성: 슬롯(중심 좌표·방향·폭/길이·타입[일반/장애인/EV]), **레인 그래프**(노드/방향성 엣지, 일방통행 플래그), 입구/출구, 보행 출입구(도보거리 계산용).
- `lot_builder.py` 에 격자 생성기: `rows × bays × aisles` 파라미터로 100~150면 도면을 자동 생성 후 JSON 저장. 손으로 좌표를 찍지 않는다.

### 2) 차량 물리 — 자전거 모델 (`world/physics.py`)
- 상태 `(x, y, θ, v, δ)`, 축거 2.7m, `δ_max` → 최소 회전반경 R_min ≈ 5.3m.
- **Pure Pursuit** 로 유도선 추종. lookahead = `clamp(k·v, 3m, 8m)`.
- 종방향: 목표속도(통로 15km/h) + 선행차 추종(간이 IDM) + 코너 감속.
- **주차 진입**: 슬롯 정면 진입점 도달 → 사전 계산된 원호/클로소이드 후진 주차 궤적 재생 (수직 주차면이므로 후진이 현실적). 소요 시간은 운전 숙련도에 따라 변동.
- 검증: `tests/test_bicycle_model.py` — R_min 이하 곡률 요구 시 궤적이 이탈하지 않는지, 후진 주차가 슬롯 경계 내에 들어오는지.

### 3) 관제 — 경로와 비용 (`control/routing.py`, `cost.py`)
- 레인 그래프 위 **A\* + 회전 페널티** (일방통행 준수). 우회전보다 좌회전에 페널티를 더 준다.
- 슬롯 비용: `w₁·주행거리 + w₂·회전수 + w₃·경로혼잡도 + w₄·도보거리(출입구까지) + w₅·차종제약`
- 경로혼잡도는 현재 안내 중인 유도선들의 엣지 점유를 세어 산출 → 여러 차가 같은 통로로 몰리지 않게 한다.

### 4) 초기 할당 전략 (교체 가능)
| 전략 | 요지 |
|---|---|
| `greedy_nearest` | 도착 즉시 최소비용 슬롯 (단순 기준선) |
| `hungarian_batch` | 짧은 시간창(예 3초) 내 도착 차량을 묶어 헝가리안 최적 매칭 |
| `congestion_aware` | 통로 혼잡 비용을 포함해 흐름을 분산 |
| `zone_late_binding` | 입구에선 **구역**만 안내, 최종 슬롯은 구역 진입 시 확정 → 강탈에 원천적으로 강함 |

### 5) 강탈 감지 (`control/state.py`)
관제는 성향을 모르므로 **관측으로만** 판단한다:
1. `SlotOccupancyChanged(slot=S, occupied=True, plate=P)` 수신
2. `S` 의 예약자가 `P` 가 아니면 → **`SlotStolen(slot=S, taker=P, victim=V)`** 판정
3. `LaneDetection` 이 예상 경로를 벗어나면 → `RouteDeviation(plate)` (이탈 조기 경보, 강탈 확정 전 선제 대응 실험에 사용)

### 6) 복구 전략 6종 — 이번 연구의 비교 대상
| # | 전략 | 요지 | 기대 특성 |
|---|---|---|---|
| R1 | `local_reassign` | 피해 차량에게만 현재 위치 기준 최근접 빈 슬롯 재할당 | 단순·빠름, 연쇄 피해 가능 |
| R2 | **`global_rematch`** | 미주차 차량 전체 × 빈 슬롯 전체 비용행렬 재구성 → 헝가리안 재매칭. 변경된 차량만 유도선 갱신 | **기본 채택.** 총 우회거리 최소, "모두 조금씩 양보" |
| R3 | `chain_shift` | 최소비용 증가경로(min-cost augmenting path)로 예약을 연쇄 이양 — 소수 차량만 건드리며 균등 분산 | R2의 저비용 근사, 유도선 변경 차량 수 최소 |
| R4 | `reserve_pool` | 전체의 k% 슬롯을 예비로 남겨두고 강탈 시 즉시 투입 | 복구는 즉각적이나 평시 이용률 손해 (k 스윕 실험) |
| R5 | `reputation_aware` | **관측된** 번호판별 과거 이탈 이력으로 신뢰도를 추정, 신뢰도가 낮은 차량에는 애초에 남에게 피해가 적은 슬롯(또는 달래기용 인기 슬롯)을 배정 | ★ 계층 분리를 깨지 않고 학습하는 전략. 발표 임팩트 |
| R6 | `fairness_weighted` | 누적 재탐색 횟수가 많은 차량에 우선권 부여 | 평균은 손해, 최악 케이스(p95) 개선 |

> **주의**: R5는 `compliance` 를 읽지 않는다. 오직 `SlotStolen`/`RouteDeviation` 관측 로그를 번호판별로 집계할 뿐이며, 이 구분이 계층 분리 원칙의 정당성을 보여주는 좋은 사례가 된다.

모든 전략은 `control/recovery/api.py` 의 동일 프로토콜을 구현하고 시나리오 YAML에서 이름으로 선택한다.

### 7) 차량 에이전트 (`agents/`)
- `DriverProfile`: `compliance ∈ [0,1]`, 조급함, 도보거리 선호, 운전 숙련도. **여기서만 존재.**
- 시야: 반경 25m 이내 + 같은 통로 열의 슬롯만 육안 확인 가능 (`perception.py`).
- 이탈 판정: 발견한 빈 슬롯이 (a) 목적지보다 남은 주행거리가 D 이상 짧고 (b) 도보거리도 나쁘지 않으면 → `compliance` 에 따른 확률로 이탈해 그 자리를 차지.
- 유도선 인지: 차량은 프로젝터가 그린 **자기 색 유도선만** 본다 (다른 차 유도선은 안 보임 — 실제와 동일).

### 8) 베이스라인 — 무안내 모드
- 유도선 없이 운전자가 통로를 돌며 육안 탐색. `perception.py` 의 시야 로직을 그대로 재사용하므로 추가 비용이 작다.
- 비교 KPI: 평균/ p95 주차 소요시간, 총 주행거리, 통로 혼잡도, 슬롯 이용률.

### 9) 기록 (보상 정책은 미구현)
`metrics/collector.py` 가 번호판별로 누적:
`reroute_count`, `detour_distance_m`, `extra_time_s`, `was_victim`, `was_taker`
→ `runs/<run_id>/compensation_ledger.jsonl` 로 저장. **보상 산정 로직은 만들지 않는다.**

### 10) 프레임 포맷 (라이브·녹화 공통)
```json
{"t": 12.34,
 "vehicles": [{"id":"12가3456","pose":[x,y,theta],"state":"driving","color":3,"model":"sedan_a"}],
 "guidance": [{"id":"12가3456","color":3,"polyline":[[x,y],...],"progress":0.42,"target":"C-14"}],
 "slots":    [{"id":"C-14","status":"reserved"}],
 "events":   [{"type":"slot_stolen","victim":"...","taker":"...","slot":"C-14"}],
 "kpi":      {"occupancy":0.62,"avg_park_time":41.2,"reroutes":3}}
```
- 변하지 않은 유도선은 `polyline` 을 생략하고 `progress` 만 보내 델타 압축 (trace 파일 크기 억제).
- WebSocket 스트림과 `trace.jsonl` 이 **동일 포맷** → 뷰어 코드는 한 벌.

---

## 웹 뷰어 (three.js, 빌드 스텝 없음)

### 유도선 렌더링 — 이 프로젝트의 얼굴 (`viewer/js/guidance.js`)
사용자 요구를 그대로 반영:
- **차선형(양쪽 두 줄) ❌** → 경로 **중앙에 굵은 리본 하나** (폭 ~0.6m)
- 한국 고속도로 안내선처럼 진행 방향으로 흐르는 **쉐브론(`<<`) 패턴**
- **지나온 구간은 소거** — `progress` 유니폼으로 트림
- 차량마다 확실히 구분되는 고유색

구현:
- 경로 폴리라인 → Catmull-Rom 스무딩 → 등간격 리샘플 → 리본 메시 생성
- 커스텀 `ShaderMaterial`: `uv.x` = 누적거리/쉐브론 주기, `uv.y` = 리본 횡방향
  - 쉐브론: `fract(uv.x - time*speed)` 와 `abs(uv.y*2-1)` 조합으로 V자 생성
  - 트림: `uv.x < progress` 이면 `discard`
  - 끝단 소프트 페이드 + 약한 additive glow (자율주행차 경로 표시 느낌)
- 목표 슬롯은 같은 색으로 테두리 펄스 애니메이션

### 색상 배정 (`viewer/js/palette.js`)
- 동시 안내 차량(보통 ≤12대)에 golden-angle 색상환 분배, 명도 L 60~75%로 제한해 어두운 아스팔트 위 대비 확보
- 사용 중인 색은 차량이 주차 완료할 때까지 재사용 금지. 색이 모자라면 화면상 가장 멀리 있는 차량의 색만 재사용
- 보조 구분: 쉐브론 밀도/굵기도 함께 변주 (색약 대응 겸용)

### 차량 3D + STL (`viewer/js/vehicles.js`, `viewer/models/models.json`)
STL 크기를 모르는 문제를 설정으로 흡수:
```json
{ "sedan_a": {
    "file": "sedan_a.stl",
    "autoFitLength": 4.7,          // 바운딩박스를 재서 전장 4.7m 에 자동 맞춤
    "upAxis": "Z",                 // STL 이 Z-up 인지 Y-up 인지
    "headingOffsetDeg": 90,        // 모델이 바라보는 방향 보정
    "pivot": "rear_axle",          // 메시 중심 → 뒷축 중심으로 원점 이동
    "scaleOverride": null,         // 자동 맞춤을 무시하고 직접 지정할 때
    "offset": [0, 0, 0]
}}
```
- 로드 시 바운딩박스 계산 → `autoFitLength` 기준 스케일 산출 → 축/피벗 보정.
- **STL 파일이 없으면 저폴리 박스 차량으로 폴백** → 지금 당장 개발이 막히지 않는다.
- 뷰어에 실시간 스케일/회전 조정 슬라이더를 둬서, 모델을 받은 뒤 눈으로 맞추고 그 값을 `models.json` 에 저장할 수 있게 한다.

### 나머지 뷰어 요소
- 바닥은 **2D 평면**: 주차선·통로 화살표·구역 문자를 캔버스 텍스처로 굽고 지면 메시에 매핑
- 카메라 프리셋: 탑다운 / 비스듬한 조감 / 특정 차량 추적
- HUD: 실시간 KPI, 이벤트 로그(강탈 발생 시 강조), 재탐색 카운터
- 컨트롤: 재생/일시정지/배속, 도착률·비협조 확률 슬라이더(라이브 모드), trace 스크러버(녹화 모드)

---

## 구현 순서

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **0** ✅ | **git init + `.gitignore` + 문서 4종 + GitHub private 저장소 생성·푸시** | `git ls-files` 에 토큰류 0건, 다른 세션이 클론 가능 |
| 1 ✅ | 스캐폴딩, 도면 생성기, 레인 그래프, **경계 테스트** | `pytest tests/test_layer_isolation.py` 통과, 120면 도면 JSON 생성 |
| 2 ✅ | 자전거 모델 + Pure Pursuit + 후진 주차 | 헤드리스로 차 1대가 지정 슬롯에 정상 주차 |
| 3 ✅ | 관제 기본: A\*, `greedy_nearest`, 센서 연결 | 유도선 커맨드가 생성되고 차량이 따라감 |
| 4 ✅ | 뷰어 1차: 바닥 + 박스 차량 + **유도선 셰이더** + WebSocket | 브라우저에서 실시간 관찰 가능 |
| 5 ✅ | 차량 성향 + 강탈 시나리오 + 강탈 감지 | 강탈이 발생하고 관제가 센서만으로 인지 |
| 6 ✅ | 복구 전략 6종 + 플러그인 구조 (R2 우선) | 시나리오 YAML로 전략 교체 가능 |
| 7 ✅ | 베이스라인 무안내 모드 | 동일 시드로 두 모드 실행 가능 |
| 8 ✅ | 실험 하네스 + 비교표/그래프 | 시드 30개 × 전략 매트릭스 → 표·박스플롯 |
| 9 ✅ | STL 로더 + 자동 스케일 + 조정 UI | STL 넣으면 즉시 반영 |
| 10 ✅ | 발표 마감: 카메라 프리셋, 스크러버, 이벤트 하이라이트 | 녹화본 단독 재생 가능 |

---

## 검증 방법

**저장소 안전성 (푸시 직후 즉시)**
```bash
git ls-files                         # 올라간 전체 파일 목록을 눈으로 확인
git ls-files | grep -Ei "token|secret|credential|\.env|\.key|\.pem"   # 결과가 비어야 함
gh repo view --json visibility       # "PRIVATE" 확인
```

**계층 분리 (가장 중요)**
```bash
pytest tests/test_layer_isolation.py -v
```
`control` 이 `agents`/`world` 를 참조하면 실패. `agents` 가 `control` 을 참조해도 실패.
추가로 `grep -r "compliance" sim/control/` 이 **아무것도 찾지 못해야** 한다.

**단위 테스트**
```bash
pytest tests/ -v
```
- `test_routing.py` — 일방통행 위반 경로가 나오지 않는지, 회전 페널티가 반영되는지
- `test_bicycle_model.py` — 최소 회전반경 준수, 후진 주차가 슬롯 내부에 안착
- `test_recovery_*.py` — 강탈 주입 시 각 전략이 피해 차량에게 유효한 대체 슬롯을 주는지, 무한 재할당 루프에 빠지지 않는지(최대 재시도 N회 후 폴백)

**헤드리스 시뮬레이션 + 실험**
```bash
python -m sim.experiments.run_matrix --scenario scenarios/rush_hour.yaml --seeds 30
python -m sim.experiments.report runs/<run_id>          # 비교표 + 그래프
```
결과로 전략별 평균 주차시간·p95·총 주행거리·재탐색 횟수 분포를 확인한다.

**라이브 뷰어**
```bash
uvicorn server.app:app --reload      # http://localhost:8000
```
브라우저에서: 차량이 자기 색 유도선을 따라가는지, 지나온 구간이 지워지는지, 강탈 발생 순간 피해 차량의 유도선이 새 슬롯으로 즉시 갈아타는지, HUD 재탐색 카운터가 증가하는지를 눈으로 확인.

**녹화본 재생**
```bash
python -m sim.experiments.run_matrix ... --trace runs/demo/trace.jsonl
# 뷰어에서 trace 선택 → 서버 없이도 동일 화면 재생
```

---

## 이번 범위에서 명시적으로 제외

- 보상 정책(주차 할인 등) 산정 로직 — 데이터만 적재
- 실제 번호판 인식(ANPR) 영상처리 — 시뮬레이션에서는 완벽 인식으로 가정
- 실제 빔 프로젝터 캘리브레이션·왜곡 보정 — 뷰어는 이상적 투사를 가정
- 비협조 행동 중 배회·통로 정차·이중주차 — 이번엔 "자리 강탈" 하나로 한정 (구조는 확장 가능하게 둠)
