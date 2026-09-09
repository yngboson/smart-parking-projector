# 인수인계 (2026-09-09 갱신 · **10단계 전부 완료**)

> **다음 세션은 이 문서 → `CLAUDE.md` → `docs/DECISIONS.md` 순서로 읽으면 됩니다.**
> `docs/PLAN.md` 는 전체 로드맵이고, 이 문서는 "지금 어디까지 왔고 다음에 뭘 하나"입니다.

---

## 30초 요약

빔 프로젝터로 주차장 바닥에 **차량별 색 유도선**을 그려 빈자리로 안내하는 시스템의
개념검증 시뮬레이터. 진짜 질문은 경로 탐색이 아니라 이것입니다:

> **비협조 운전자가 남에게 배정된 자리를 강탈했을 때, 관제가 얼마나 매끄럽게 복구하는가?**

그래서 **관제 알고리즘은 운전자의 성향(`compliance`)을 절대 볼 수 없게** 코드를
물리적으로 분리했고, 그 경계를 테스트로 강제합니다. 이게 무너지면 연구가 무의미해집니다.

**계획한 0~10단계가 전부 들어갔습니다.** 복구 전략 6종과 할당 전략 4종이 플러그인
으로 돌아가고, 실험 하네스가 시드별로 매트릭스를 돌려 비교표와 박스플롯을 뽑습니다.
뷰어에는 녹화본 타임라인·차량 추적 카메라·사건 하이라이트가 붙었습니다.

**남은 일은 만드는 것이 아니라 돌리는 것입니다.** 저장소의 결과는 시드 10개로
뽑은 것이고, 발표에 쓸 숫자는 `--seeds 30` 으로 다시 뽑는 편이 낫습니다 (아래
'실험을 어떻게 돌리는가').

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
pytest tests/                                           # 176개 통과해야 정상 (약 3분, 코어만큼 병렬)
uvicorn server.app:app --reload                         # 브라우저 → localhost:8000
python -m sim.world.simulation --duration 300 --arrival-rate 0.15    # 헤드리스
python -m sim.metrics.trace_writer --duration 300 --out runs/demo    # 녹화

# 실험 (아래 '실험을 어떻게 돌리는가' 참조)
python -m sim.experiments.run_matrix --scenario busy --compare-baseline --seeds 10
python -m sim.experiments.report runs/<run_id>
```

라이브 뷰어에서 **시나리오**와 **비협조 운전자 비율**을 그 자리에서 바꿀 수 있습니다.
비율을 0 으로 두면 강탈이 사라지는 것이 보입니다 — 발표에서 문제의 원인을 보여주는
장면입니다.

**녹화본에서는 타임라인을 끌 수 있습니다.** `?trace=/runs/demo/trace.jsonl` 로 특정
녹화본을 지목할 수도 있습니다. 강탈이 일어나면 그 자리에 고리가 퍼지고, 이벤트
목록의 줄을 누르면 카메라가 그 자리를 비춥니다. 안내 목록의 줄을 누르면 그 차를
따라갑니다 — "이 차가 지금 무엇을 겪고 있는지 보시죠"로 넘어가는 통로입니다.

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
| 7 | **무안내 베이스라인 + 주차 단순화 + 유도선 정렬** | ✅ |
| 8 | **실험 하네스 + 보상 원장 + 할당 전략 4종** | ✅ |
| 9 | STL 조정 UI — 넣으면 화면에서 맞추고 저장 | ✅ |
| 10 | 발표 마감 — 타임라인 스크러버 · 추적 카메라 · 사건 하이라이트 | ✅ |

---

## 실측 — 발표에 쓸 숫자

전부 **지금 코드로, 시드 10개씩** 뽑은 것입니다. 원본은 `runs/*/matrix.jsonl`,
표와 그래프는 `runs/*/report.md` 에 있습니다.

### ① 안내 vs 무안내 — 발표의 첫 슬라이드 (`busy`, 900초)

| 지표 | 무안내 | 안내 | |
|---|---|---|---|
| 평균 주차소요 | 71.9초 | **29.9초** | −58% |
| p95 주차소요 | 223.2초 | **55.2초** | −75% |
| 평균 우회거리 | 53.0m | **2.1m** | −96% |
| p95 우회거리 | 231.3m | **9.4m** | −96% |
| 주차 완료 | 90.1대 | **125.0대** | +39% |

**꼬리가 더 극적입니다.** 평균은 절반으로 줄지만 p95 는 4분의 1이 됩니다 — 유도선이
없을 때 진짜 손해를 보는 사람은 평균적인 운전자가 아니라 **끝까지 자리를 못 찾은
사람**입니다.

### ② 복구 전략 6종 (`rush_hour`, 900초, 비협조 35%)

| 전략 | 평균 소요 | p95 | 주차 완료 | 피해 차량 | 휘말린 재배치 |
|---|---|---|---|---|---|
| `reserve_pool` | **34.5초** | **64.3초** | 121.6 | **3.4** | 0 |
| `chain_shift` | 38.6초 | 79.6초 | 125.5 | 4.8 | 1.9 |
| `local_reassign` | 40.1초 | 83.8초 | **130.7** | 4.7 | 0 |
| `fairness_weighted` | 40.1초 | 83.8초 | 130.7 | 4.7 | 0 |
| `reputation_aware` | 40.1초 | 83.8초 | 130.7 | 4.7 | 0 |
| **`global_rematch`** (기본) | 44.3초 | 97.5초 | 121.4 | **16.7** | **214.1** |

**기본 전략이 가장 나쁩니다. 그리고 그 이유가 이 연구의 이야기입니다.**
`global_rematch` 는 사고가 날 때마다 모두를 다시 매칭한다 — "모두가 조금씩 양보"
(D-009). 그런데 그 재배치가 **예약된 빈 자리를 계속 새로 만듭니다.** 비협조
운전자에게는 그것이 곧 기회이고, 그래서 피해자가 3.5배로 늘어납니다.
휘말린 재배치 214건이 그 대가의 크기입니다.

`reserve_pool` 이 이긴 이유는 반대입니다 — 예비석을 빼두므로 사고가 나도 남의 예약을
건드리지 않습니다.

> ⚠️ **셋이 같은 숫자인 것은 버그가 아닙니다.** `local_reassign`·`fairness_weighted`·
> `reputation_aware` 는 이 조건에서 구분되지 않습니다. 재 봤더니 신뢰도가 떨어진
> 번호판이 220개 중 7개, **두 번 재배정된 차량은 0대**였습니다 — 평판을 쌓을 재료도
> 우선권을 줄 상습 피해자도 없습니다. 그래서 `repeat_offenders` 시나리오를 따로
> 만들었습니다 (같은 번호판이 대여섯 번 드나드는 조건). 발표에서는 둘을 나란히
> 놓고 "평판 기반 전략은 평판이 쌓일 조건에서만 의미가 있다"로 말하는 편이
> 정직합니다.

### ③ 할당 전략 4종 (`rush_hour`, 900초, 복구는 `global_rematch` 고정)

| 전략 | 평균 소요 | p95 | 주차 완료 | 피해 차량 |
|---|---|---|---|---|
| `congestion_aware` | **41.1초** | **78.2초** | **129.6** | 19.0 |
| `greedy_nearest` | 44.3초 | 97.5초 | 121.4 | 16.7 |
| `hungarian_batch` | 44.2초 | 97.5초 | 121.6 | 17.0 |
| `zone_late_binding` | 48.9초 | 116.4초 | 122.8 | **26.9** |

- **`hungarian_batch` 가 `greedy_nearest` 와 같은 것은 의도한 결과입니다** — 이
  주차장에서는 배치가 한 대짜리로만 생깁니다 (D-027).
- **`zone_late_binding` 은 가설과 반대로 나왔습니다.** 계획서는 늦은 확정이 강탈에
  강할 것이라 봤는데(PLAN 4절), 오히려 피해자가 가장 많습니다. **재확정 자체가
  예약을 흔들기 때문**이고, `global_rematch` 가 나쁜 이유와 같습니다.

---

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

## 실험을 어떻게 돌리는가

```bash
# 안내 vs 무안내 — 발표의 첫 슬라이드 (D-010)
python -m sim.experiments.run_matrix --scenario busy --compare-baseline --seeds 10

# 복구 전략 6종 비교 — 이 연구의 결론 (D-009)
python -m sim.experiments.run_matrix --scenario rush_hour --seeds 30

# 할당 전략 4종 비교
python -m sim.experiments.run_matrix --scenario rush_hour --seeds 30 \
    --allocator greedy_nearest,hungarian_batch,congestion_aware,zone_late_binding \
    --recovery global_rematch

python -m sim.experiments.report runs/<run_id>      # 비교표 + 박스플롯
```

한 칸 = (시나리오, 할당, 복구, 시드) 하나이고 칸끼리 완전히 독립이라 프로세스로
나눠 돌린다. 결과는 이렇게 쌓인다.

    runs/<run_id>/
      meta.json                무엇을 어떤 조건으로 돌렸는가
      matrix.jsonl             칸 하나당 한 줄  ← **시드별 원본을 반드시 보세요**
      ledger/<칸>/             번호판별 보상 원장 (D-008 — 기록만, 정책 없음)
      report.md                비교표 + 박스플롯

**시드별 원본을 펼쳐 보세요.** 평균만 보면 놓칩니다. 실제로 시드 10개 중 2개에서만
처리량이 3분의 1로 떨어지는 교착이 숨어 있었고, 평균으로는 "편차가 크다"로만 보였다
(D-026). 저장소에 커밋된 결과는 `runs/compare-baseline`, `runs/recovery-matrix`,
`runs/allocator-matrix` 셋이다 (원장은 크기 때문에 뺐다 — 같은 시드로 다시 돌리면
글자 그대로 같은 것이 나온다).

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

### 2. 만차에서는 여전히 느립니다 — 시드 30개는 시간을 잡아먹습니다

`rush_hour` 한 칸(900초)이 3~10분 걸립니다. 하네스가 코어 수만큼 병렬로 돌리므로
시드 10개 × 전략 6종이 8코어에서 한 시간쯤입니다. **시드 30개로 올리면 세 배**입니다.

병렬화는 이미 했습니다(`run_matrix --jobs`). 더 필요하면 단일 실행을 손봐야 합니다:

- `Simulation._visible_slots` — 차량마다 주차면 120개를 훑습니다. 거리 사전 검사가
  있지만 여전히 전수 순회입니다. `sensors.grid_index` 를 쓰면 근처만 보면 됩니다
- `AisleTraffic.update` — 차량마다 주차면 격자를 조회합니다

`Simulation._clearance` 의 O(n²) 는 거리 사전 검사로 이미 해결됐습니다.

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
- **비교표의 평균만 보고 결론 내지 마세요.** `matrix.jsonl` 의 시드별 원본을 펼쳐
  보세요. 교착은 시드 몇 개에만 나타나고 평균에서는 "편차"로 위장합니다 (D-026).

---

## 커밋 규칙

- 구현 순서의 단계마다 커밋 — 다른 세션이 히스토리로 진행 상황을 파악합니다
- **토큰·자격증명 절대 커밋 금지**:
  ```bash
  git ls-files | grep -Ei "token|secret|credential|\.env|\.key|\.pem"   # 비어야 함
  ```
- 설계 결정을 바꿔야 하면 `docs/DECISIONS.md` 에 새 항목을 추가하세요.
  기존 결정을 조용히 뒤집지 마세요.
