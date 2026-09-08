# 인수인계 (2026-09-09 갱신 · 6단계까지)

> **다음 세션은 이 문서 → `CLAUDE.md` → `docs/DECISIONS.md` 순서로 읽으면 됩니다.**
> `docs/PLAN.md` 는 전체 로드맵이고, 이 문서는 "지금 어디까지 왔고 다음에 뭘 하나"입니다.

---

## 30초 요약

빔 프로젝터로 주차장 바닥에 **차량별 색 유도선**을 그려 빈자리로 안내하는 시스템의
개념검증 시뮬레이터. 진짜 질문은 경로 탐색이 아니라 이것입니다:

> **비협조 운전자가 남에게 배정된 자리를 강탈했을 때, 관제가 얼마나 매끄럽게 복구하는가?**

그래서 **관제 알고리즘은 운전자의 성향(`compliance`)을 절대 볼 수 없게** 코드를
물리적으로 분리했고, 그 경계를 테스트로 강제합니다. 이게 무너지면 연구가 무의미해집니다.

**0~6단계 완료. 복구 전략 6종이 플러그인으로 들어갔고 같은 시드로 비교됩니다.
다음은 7단계(무안내 베이스라인) — "왜 이 시스템이 필요한가"의 첫 슬라이드입니다.**

---

## 5분 안에 돌려보기

```bash
git clone https://github.com/yngboson/smart-parking-projector
cd smart-parking-projector

# Windows
start-windows.bat
# macOS / Linux
./start-macos.command
```

파이썬 확인 → venv → 의존성 → 도면 생성 → 서버 → 브라우저까지 자동입니다.

```bash
pytest tests/ -v                                        # 116개 통과해야 정상 (약 4분 30초)
uvicorn server.app:app --reload                         # 브라우저 → localhost:8000
python -m sim.world.simulation --duration 300 --arrival-rate 0.15    # 헤드리스
python -m sim.metrics.trace_writer --duration 240 --out runs/demo    # 녹화
```

라이브 뷰어에서 **시나리오**(light/busy/rush_hour)와 **비협조 운전자 비율**을
그 자리에서 바꿀 수 있습니다. 비율을 0 으로 두면 강탈이 사라지는 것이 보입니다 —
발표에서 문제의 원인을 보여주는 장면입니다.

브라우저를 열면 **Python 시뮬레이션이 실시간으로 보낸 프레임**이 그대로 보입니다.
서버가 없으면 뷰어가 알아서 `runs/demo` 녹화본으로 넘어갑니다 (D-005).

> ⚠️ **Python 3.11 이상이 필요합니다.** macOS 기본 파이썬은 3.9 라 설치가 실패합니다.
> `brew install python@3.14` 후 그 인터프리터로 venv 를 만드세요.

---

## 지금까지 한 것

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | Git/GitHub, 문서 4종, `.gitignore` | ✅ |
| 1 | 공용 값 타입, 도면 생성기, **계층 경계 테스트** | ✅ |
| 2 | 자전거 모델, Pure Pursuit, 후진 주차 | ✅ |
| 3 | 관제 알고리즘 + 센서 + 프로젝터 + 계층 배선 | ✅ |
| 4 | 뷰어 연동 — WebSocket 라이브 + 녹화본 재생 | ✅ |
| 5 | 차량 성향 · 이탈 판단 · 강탈 감지 · 시나리오 | ✅ |
| 6 | **복구 전략 6종 + 플러그인 구조** | ✅ |
| 7 | **무안내 베이스라인 — 다음 작업** | ⬜ |
| 8~10 | 실험 하네스, STL, 발표 마감 | ⬜ |

### 3단계 실측 (시드 6개 × 1200초, 도착률 0.15/초)

```
주차 완료      82 ~ 100대        평균 소요      38 ~ 45초
주행차 정지비율 4.6 ~ 9.4%        최장 정지      11 ~ 15초
주차면 이탈    0대                센서 오탐 강탈  548건 중 1건 (0.2%)
```

### 4단계 실측

```
녹화본 크기    240초 / 초당 3.3프레임 → 1.6 MB   (좌표 cm 반올림 + 폴리라인 델타)
라이브 스트림  초당 10프레임, 배속 0.5~8×, 일시정지·시나리오/도착률/성향 변경
```

### 5단계 실측 (900초 · 시드 0 · 비협조 35%)

| 시나리오 | 최대 점유율 | 주차 | 이탈 | 강탈 | 재탐색 |
|---|---|---|---|---|---|
| `light` | 25.8% | 120 | 36 | 5 | 5 |
| `busy` | 64.2% | 103 | 33 | 4 | 3 |
| `rush_hour` | 93.3% | 104 | 28 | **13** | 13 |
| `rush_hour` · 전원 협조 **(대조군)** | 90.8% | 89 | 0 | **0** | 0 |

마지막 줄이 이 표에서 가장 중요합니다. 같은 혼잡도에서 아무도 배신하지 않으면
강탈이 **0 건**입니다 — 측정에 바닥 노이즈가 없다는 뜻이고, 그래야 강탈 횟수를
지표로 쓸 수 있습니다. 여기까지 오는 데 센서를 두 번 고쳤습니다 (D-015).

### 6단계 실측 (rush_hour · 시드 0~1 · 300초 · 비협조 35%)

| 전략 | 주차 | 평균 | **p95** | 강탈 | 재탐색 | **연쇄** |
|---|---|---|---|---|---|---|
| `local_reassign` | 30 | 49.8 | 183.8 | 4.0 | 3.5 | 0 |
| `global_rematch` *(기본)* | 35 | 45.0 | **65.2** | 11.5 | 10.5 | **54** |
| `chain_shift` | 34 | 44.4 | **65.0** | 5.0 | 4.5 | **5** |
| `reserve_pool` | 35 | 45.5 | 80.0 | **1.5** | 1.5 | 0 |
| `reputation_aware` | 30 | 49.8 | 183.8 | 4.0 | 3.5 | 0 |
| `fairness_weighted` | 30 | 49.8 | 183.8 | 4.0 | 3.5 | 0 |

**읽는 법** — 이 표는 결론이 아니라 **다음 세션이 확인할 가설**입니다. 시드 2개짜리
예비 측정이고, 통계적 판단은 8단계 하네스(시드 30개)의 몫입니다.

- **`chain_shift` 가 흥미롭습니다.** `global_rematch` 와 사실상 같은 p95(65초)를
  내면서 휘말린 차량이 **54대 → 5대**입니다. "전체를 다시 푸는 것"의 이득 대부분이
  실은 **짧은 연쇄 하나**에서 나온다는 뜻일 수 있습니다. 사실이면 발표의 핵심 소재입니다.
- **`global_rematch` 가 강탈을 더 만듭니다** (11.5 vs 4.0). 예약이 계속 바뀌니
  누군가의 자리가 남의 눈앞에 놓이는 일이 잦아지는 것으로 보입니다. 확인이 필요합니다.
- **`reputation_aware` 와 `fairness_weighted` 가 `local_reassign` 과 숫자까지
  같습니다.** 버그가 아니라 **측정 구간이 짧아서**입니다. 두 전략은 과거 이력이
  쌓여야 작동하는데, 300초 안에는 재방문도 반복 피해자도 거의 없습니다.
  **8단계에서는 더 길게(2000초 이상) 돌려야 이 둘을 평가할 수 있습니다.**
  `SimConfig.returning_share`(기본 0.35)가 단골 비율입니다.

**교착 없음.** 3단계에서 차간거리를 배선하자 시뮬레이션이 통째로 굳었고, 원인을 찾는 데
네 번의 시도가 필요했습니다. 전말은 `docs/DECISIONS.md` D-012 와 `sim/world/traffic.py`
모듈 주석에 남겼습니다 — **같은 함정을 다시 밟지 않도록 반드시 읽으세요.**

---

## 저장소 지도

```
sim/common/      세 계층이 공유하는 불변 값 타입만
  geometry.py    Vec2/Pose, 폴리라인 리샘플·회전량
  ids.py         PlateId/SlotId/NodeId (NewType), 차종·주차면 종류
  messages.py    ★ 센서 이벤트 4종 (관제가 세상을 아는 유일한 창구)
  lotmap.py      도면 + 렌더링용 정보 + `shortest_path()` (거리만 보는 최단경로)
  vehicle.py     VehicleSpec / ControlInput / SelfState
  maneuver.py    후진 주차 궤적 기하 (순수 기하)

sim/world/       주차장 환경 — 계층 배선을 담당하는 유일한 곳
  lot_builder.py 격자 도면 생성기 (좌표 하드코딩 없음)
  physics.py     자전거 모델 적분 + `forward_clearance()` (조향각 원호를 따라 잼)
  sensors.py     ANPR · 슬롯 점유 센서 · 통로 검지기 + 공용 격자 색인
                 점유 판정에 확인 시간·정렬각·이력현상이 들어 있다 (D-015)
  traffic.py     통로 합류 양보 (D-012). 교착 조사 전말이 모듈 주석에 있음
  projector.py   유도선 상태 + 진행률(지나온 구간 소거)
  simulation.py  ★ 계층 배선. 고정 timestep 루프. `python -m` 으로 실행 가능
                 `run(duration, stride=n)` — 물리는 그대로 두고 관측만 솎아낸다

sim/control/     관제 — sim.common 외에는 아무것도 import 하지 않음
  api.py         ControlSystem 프로토콜 + NullControl(무안내 베이스라인 뼈대)
  system.py      ProjectorControl — 기본 관제 본체
  state.py       센서로만 갱신하는 세계 모델. 강탈·이탈 판정, 번호판별 관측 이력
                 '나가는 중'(departing) 판정이 여기 있다 — D-016
  routing.py     레인 그래프 A* + 회전 페널티 (좌회전이 더 비쌈)
  cost.py        주차면 비용 = 주행거리 + 회전 + 혼잡 + 도보거리 + 차종제약
  allocators/
    api.py       Allocator 프로토콜 + 레지스트리
    greedy_nearest.py   기준선 전략
  recovery/      ★ 6종 비교 대상 (D-009). 기본값 global_rematch 를 바꾸지 말 것
    api.py       recover / withhold / bias 세 훅 + 레지스트리
    local_reassign.py  global_rematch.py  chain_shift.py
    reserve_pool.py    reputation_aware.py  fairness_weighted.py

sim/agents/      차량 — sim.common 외에는 아무것도 import 하지 않음
  driving.py     Pure Pursuit + 종방향 제어 (운전 기술)
  driver.py      ★ DriverProfile(compliance·walk_preference) 와 주행 상태 기계
                 이탈 판단이 여기 있다 — 이 연구의 돌발 상황 그 자체
  perception.py  운전자 시야 규칙 (반경·시야각·통로 접면)

sim/scenarios/   실험 조건을 데이터로 (light / busy / rush_hour)
  loader.py      YAML → Scenario. 오타는 조용히 넘기지 않고 바로 실패시킨다

sim/metrics/
  trace_writer.py  프레임을 runs/<run_id>/trace.jsonl 로 녹화 (발표장의 보험)

server/app.py    /ws 에서 시뮬레이션을 굴려 프레임을 밀어 넣는다. 연결마다 독립 세션

viewer/js/       three.js 뷰어 (빌드 스텝 없음)
  source.js      라이브(WebSocket)와 녹화본을 같은 얼굴로 감싼다. 라이브 실패 시 자동 전환
  main.js        프레임을 받아 그리기만 한다 — 아무것도 결정하지 않는다

tests/
  test_layer_isolation.py  ★ 이 저장소에서 가장 중요한 테스트
  test_traffic.py          ★ 두 번째로 중요 — 교착이 없는가
  test_defection.py        ★ 이탈이 일어나는가 / 협조만 있으면 강탈이 0 인가
  test_routing.py, test_control.py, test_simulation.py, test_stream.py
  test_lotmap.py, test_driving.py
```

---

## 절대 지켜야 할 것 (자세한 내용은 `CLAUDE.md`)

1. **`sim/control/` 은 `sim/agents/`, `sim/world/` 를 import 하면 안 됩니다.**
2. **`sim/control/` 안에 `compliance` 라는 단어가 등장하면 안 됩니다.**
   간접 조회·문자열 키·주석까지 전부 위반으로 잡힙니다.
3. 관제에 전달되는 것은 `SensorEvent` 뿐입니다. 차량 객체 참조 금지.
   (`test_simulation.py::test_control_only_ever_sees_sensor_events` 가 런타임에도 감시합니다.)
4. 커밋 전:
   ```bash
   pytest tests/test_layer_isolation.py tests/test_traffic.py -v
   grep -rn "compliance" sim/control/     # 결과가 비어야 함
   ```

**허용되는 것**: 번호판별 **과거 관측 이력**을 관제가 집계·학습하는 것 (D-003).
`ControlState.log` 가 그것이고, 복구 전략 `reputation_aware` 가 씁니다.

---

## 다음 작업 — 7단계 (무안내 베이스라인)

**발표의 첫 슬라이드가 여기서 나옵니다.** "왜 이 시스템이 필요한가"는 유도선이
없을 때가 어떤지를 같은 조건에서 보여줘야 답이 됩니다.

유도선 없이 운전자가 통로를 돌며 육안으로 자리를 찾는 모드입니다. 그 결과를
안내 모드와 **같은 시드로** 나란히 놓으면 탐색 시간·주행거리·통로 혼잡도의 차이가
그대로 나옵니다 (D-010).

### 만들 것

| 파일 | 내용 |
|---|---|
| `sim/agents/driver.py` | 안내가 없을 때의 행동 — 통로를 돌며 눈에 띄는 빈자리로 |
| `sim/world/simulation.py` | `NullControl` 을 쓸 때 `vision_enabled` 를 강제로 켠다 |
| `sim/scenarios/*.yaml` | `control.allocator: none` 같은 표기로 베이스라인 지정 |

### 이미 준비된 것 (다시 만들지 마세요)

- **`sim.control.api.NullControl`** — 아무 안내도 하지 않는 관제. 이미 있습니다.
  `Simulation(lot, control=NullControl(), config=...)` 로 바로 돌아갑니다
- **`SimConfig.vision_enabled`** — 켜면 협조적인 운전자에게도 육안 관측이 갑니다.
  베이스라인에서는 **모두가** 눈으로 찾아야 하므로 켜야 합니다
- **`agents/perception.py`** 의 시야 규칙과 `Driver._consider_defection` 의 판단 —
  "눈에 보이는 자리 중 내 기준으로 가장 좋은 것"을 고르는 로직이 이미 있습니다.
  베이스라인은 그 판단을 **유도선 없이** 쓰는 것뿐입니다
- 프레임 포맷은 그대로입니다. `guidance` 가 빈 배열이 되고 뷰어는 바닥에 선을
  그리지 않습니다 — 코드를 고칠 필요가 없습니다

### 주의

운전자가 아무 자리도 못 찾으면 통로를 계속 돌아야 합니다. 지금 `Driver` 는 안내가
없으면 **그 자리에 섭니다**(`ARRIVING` 에서 정지). 베이스라인에서는 그러면 입구가
막히므로, 안내가 없을 때 "일단 통로를 따라 순회한다"는 행동이 필요합니다.

---

## 미해결 이슈 3개 ⚠️

> 4단계까지 남아 있던 '센서 오탐 0.2%' 는 해결됐습니다 — 만차 근처 전원 협조 조건에서
> 강탈 **0 건**입니다 (D-015). 강탈 횟수를 지표로 써도 됩니다.

### 1. `greedy_nearest` 가 모든 차를 한 구석으로 몰아넣습니다

**증상**: 도착 차량이 거의 전부 입구에서 가까운 A·E·F 구역 서쪽 끝에 배정됩니다.
그 결과 주차장 나머지 90% 가 비어 있는데 서쪽 통로만 붐빕니다.

**원인**: 비용 함수가 주행거리와 도보거리를 함께 보는데, 혼잡 항이 이걸 상쇄하지
못합니다. 혼잡도를 "병목 엣지" 방식으로 바꿔 흩어뜨려도 봤지만 **더 나빴습니다** —
통로를 가로지르는 장거리 이동이 늘어 정지 비율이 5% → 82% 로 뛰었습니다 (D-013).

**이건 버그가 아니라 기준선 전략의 알려진 약점입니다.** `congestion_aware` /
`hungarian_batch` / `zone_late_binding` 이 개선해야 할 지점이고, 비교 실험의 재료입니다.
고치려 하지 말고 **다른 전략을 추가**하세요 (`CLAUDE.md` 의 '전략을 추가하는 방법').

### 2. 만차에서 시뮬레이션이 느립니다 — 8단계 하네스의 발목을 잡습니다

`rush_hour`(차량 약 110대)에서 **실시간의 0.2배** 정도로 돕니다. 300초를 돌리는 데
25분쯤 걸립니다. 8단계의 "시드 30개 × 전략 6종" 매트릭스는 이 속도로는 며칠 걸립니다.

**어디가 무거운가** (측정하고 고치세요, 추측하지 말고):
- `Simulation._clearance` — 차량 쌍마다 꼭짓점 4개씩 O(n²). 격자 색인을 쓰면
  근처 차량만 보면 됩니다 (`sensors.grid_index` 가 이미 있습니다)
- `Simulation._visible_slots` — 차량마다 주차면 120개를 훑습니다. 거리 사전 검사가
  있지만 여전히 전수 순회입니다
- `AisleTraffic.update` — 차량마다 주차면 격자를 조회합니다

병렬화(시드별 프로세스)도 쉬운 승리입니다. `multiprocessing.Pool` 로 시드를 흩으면
코어 수만큼 빨라지고, 시뮬레이션이 이미 재현 가능하므로 결과가 달라지지 않습니다.

### 3. 같은 주차면에 두 대가 들어가는 일이 드물게 남아 있습니다

이 시뮬레이터는 **충돌을 모델링하지 않습니다.** 두 차가 같은 자리를 노리고 동시에
도착하면 그냥 겹칩니다. 방어를 세 겹 넣었습니다 — 눈에 보이는 점유 여부를 물리적
사실로 판정하고, 후진 직전에 한 번 더 확인하고, 그래도 겹치면 진 쪽이 포기하고
나갑니다 (`Simulation._update_schedule`).

만차 조건 100여 대 중 0~1건까지 줄었지만 0 은 아닙니다. 발표 화면에서 눈에 띌 수
있으니, 신경 쓰인다면 근본 해결은 **차량 간 충돌 판정**입니다 — 다만 그것을 넣으면
교착 대책(D-012)을 다시 손봐야 합니다.

---

## 하지 말 것 (함정)

- **보상 정책(주차 할인 등)을 구현하지 마세요.** 재탐색 횟수와 우회 거리를 기록만
  합니다 (D-008).
- **기본 복구 전략(`global_rematch`)을 다른 것으로 바꾸지 마세요.** 6종 비교 대상입니다 (D-009).
- **비협조 행동을 늘리지 마세요.** "자리 강탈" 하나로 한정입니다.
- **도면 좌표를 코드에 하드코딩하지 마세요.** `layouts/*.json` 이 진실입니다.
- **three.js 를 CDN 으로 바꾸지 마세요.** 발표장에 인터넷이 없을 수 있습니다.
- **교착 대책을 관제(`sim/control/`)에 넣지 마세요.** 복구 전략마다 교통 통제가
  달라지면 6종 비교가 오염됩니다 (D-012).

---

## 커밋 규칙

- 구현 순서의 단계마다 커밋 — 다른 세션이 히스토리로 진행 상황을 파악합니다
- **토큰·자격증명 절대 커밋 금지**:
  ```bash
  git ls-files | grep -Ei "token|secret|credential|\.env|\.key|\.pem"   # 비어야 함
  ```
- 설계 결정을 바꿔야 하면 `docs/DECISIONS.md` 에 새 항목을 추가하세요.
  기존 결정을 조용히 뒤집지 마세요.
