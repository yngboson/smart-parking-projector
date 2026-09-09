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
| 평균 주차소요 | 53.7초 | **28.4초** | −47% |
| p95 주차소요 | 115.7초 | **47.9초** | −59% |
| 평균 우회거리 | 52.6m | **2.0m** | −96% |
| p95 우회거리 | 241.3m | **8.4m** | −97% |
| 주차 완료 | 118.2대 | **179.0대** | +51% |

**꼬리가 더 극적입니다.** 평균은 절반으로 줄지만 p95 우회거리는 **30분의 1**이
됩니다 — 유도선이 없을 때 진짜 손해를 보는 사람은 평균적인 운전자가 아니라
**끝까지 자리를 못 찾고 통로를 도는 사람**입니다.

### ② 복구 전략 6종 (`rush_hour`, 900초, 비협조 35%)

| 전략 | 평균 소요 | p95 | 주차 완료 | 피해 차량 | 휘말린 재배치 |
|---|---|---|---|---|---|
| `reserve_pool` | **34.5초** | **63.7초** | 169.6 | **5.2** | 0 |
| `chain_shift` | 38.3초 | 78.6초 | 178.6 | 5.8 | 2.8 |
| `local_reassign` | 38.7초 | 80.6초 | **181.0** | 6.1 | 0 |
| `reputation_aware` | 38.7초 | 80.6초 | 181.0 | 6.1 | 0 |
| `fairness_weighted` | 38.8초 | 81.3초 | 180.7 | 6.1 | 0 |
| **`global_rematch`** (기본) | 42.1초 | 91.5초 | 171.1 | **23.0** | **294.6** |

**기본 전략이 가장 나쁩니다. 그리고 그 이유가 이 연구의 이야기입니다.**
`global_rematch` 는 사고가 날 때마다 모두를 다시 매칭합니다 — "모두가 조금씩 양보"
(D-009). 그런데 그 재배치가 **예약된 빈 자리를 계속 새로 만듭니다.** 비협조
운전자에게는 그것이 곧 기회이고, 그래서 피해자가 **4배**로 늘어납니다. 휘말린
재배치 294건이 그 대가의 크기입니다.

`reserve_pool` 이 소요시간에서 이긴 이유는 정반대입니다 — 예비석을 빼두므로 사고가
나도 **남의 예약을 건드리지 않습니다.** 대신 그 예비석만큼 처리량을 잃습니다
(169.6 vs 181.0). 효율과 안정의 상충이 숫자로 나옵니다.

> ⚠️ **셋이 사실상 같은 숫자인 것은 버그가 아니라 조건 때문입니다.** 재 봤더니
> 신뢰도가 떨어진 번호판이 220개 중 7개, **두 번 재배정된 차량은 0대**였습니다 —
> 평판을 쌓을 재료도 우선권을 줄 상습 피해자도 없습니다. `fairness_weighted` 는
> 10개 시드 중 **9개가 `local_reassign` 과 완전히 동일**하고, 평균이 0.1초 움직인
> 것은 시드 하나 때문입니다.
>
> 그래서 `repeat_offenders` 시나리오를 따로 만들었습니다 — 아래 ④ 입니다.

### ③ 할당 전략 4종 (`rush_hour`, 900초, 복구는 `global_rematch` 고정)

| 전략 | 평균 소요 | p95 | 주차 완료 | 피해 차량 |
|---|---|---|---|---|
| `congestion_aware` | **39.3초** | **77.4초** | **177.3** | 25.1 |
| `greedy_nearest` | 42.1초 | 91.5초 | 171.1 | **23.0** |
| `hungarian_batch` | 42.2초 | 92.0초 | 171.4 | 23.3 |
| `zone_late_binding` | 45.8초 | 107.9초 | 165.4 | **34.4** |

- **`hungarian_batch` 가 `greedy_nearest` 와 같은 것은 의도한 결과입니다** — 이
  주차장에서는 배치가 한 대짜리로만 생깁니다 (D-027).
- **`zone_late_binding` 은 가설과 반대로 나왔습니다.** 계획서는 늦은 확정이 강탈에
  강할 것이라 봤는데(PLAN 4절), 오히려 피해자가 가장 많습니다(34.4). **재확정 자체가
  예약을 흔들기 때문**이고, `global_rematch` 가 나쁜 이유와 정확히 같습니다.
  이 시뮬레이터가 준 가장 뚜렷한 결론이 이것입니다 — **예약을 흔드는 모든 것이
  강탈의 기회를 만든다.** 복구 쪽의 `global_rematch` 와 할당 쪽의
  `zone_late_binding` 이 서로 다른 이유로 같은 결론에 도달합니다.

### ④ 평판이 쌓이는 조건 (`repeat_offenders`, 1800초, 재방문 85%, 비협조 50%)

같은 번호판이 하루에 대여섯 번 드나드는 조건입니다. 여기서 비로소 셋이 갈립니다.

| 전략 | 평균 소요 | p95 | 주차 완료 | 피해 차량 | 휘말린 재배치 |
|---|---|---|---|---|---|
| `fairness_weighted` | 32.8초 | 75.7초 | 425.0 | **16.6** | 0 |
| `reputation_aware` | 32.6초 | 73.7초 | 426.0 | 17.0 | 0 |
| `local_reassign` | **32.3초** | 73.3초 | 427.1 | 18.2 | 0 |
| `reserve_pool` | 32.3초 | **69.0초** | **430.9** | 18.6 | 0 |
| **`global_rematch`** (기본) | 35.6초 | 85.5초 | 425.1 | **43.3** | **1121.4** |

**"평판 기반 전략은 평판이 쌓일 조건에서만 의미가 있다"** — 이것이 발표에서 할 수
있는 가장 정직한 말입니다. `rush_hour` 에서 아무 차이가 없던 두 전략이, 단골이
많아지자 10개 시드 중 8~9개에서 갈라지고 피해 차량을 줄입니다 —
`reputation_aware` 가 −7%, `fairness_weighted` 가 **−9%**. 대신 평균 소요를
0.3~0.5초 내줍니다 —
**효율을 조금 팔아 최악을 줄이는 거래**이고, 그것이 이 두 전략의 설계 의도입니다.

`global_rematch` 는 여기서 더 나빠집니다. 피해 차량이 **2.4배**, 휘말린 재배치가
1121건입니다. 사고가 잦아질수록 "모두가 조금씩 양보"의 대가가 초선형으로 커집니다.

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

## ⚠️ 측정 타당성 — 혼잡을 재기 전에 고쳐야 할 것

`sim/world/simulation.py:418` 의 `max_guided` 가 **주차하지 않은 모든 차량**을 세며,
**무안내 베이스라인에도 똑같이 적용**됩니다 (`baseline.yaml` 도 `max_guided: 20`).

이 값 20 은 유도선 **색 구분의 한계**에서 나온 숫자입니다 (D-006). 무안내 모드에는
유도선이 없으므로 물리적 근거가 없는데도 입구를 막고 있습니다. 그래서 지금
"무안내가 118 대밖에 처리 못 했다"는 결과가 **물리적 혼잡 때문인지 색 팔레트
한계 때문인지 구분되지 않습니다.**

두 상한을 분리해야 합니다 — `max_guided`(안내 모드만, 색 한계) 와
`max_circulating`(양쪽 모두, 통로 총 길이 ÷ 차량 점유 길이로 도면에서 계산).

자세한 내용과 실험 설계는 [`ALLOCATION_MODEL.md`](ALLOCATION_MODEL.md) 7절.

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
- **`start-windows.bat` 을 LF 줄바꿈으로 저장하지 마세요.** `cmd.exe` 는 배치 파일에
  CRLF 를 요구합니다. LF 면 줄을 나누지 못해 주석과 명령이 뒤엉키고 괄호 블록이
  깨지며, `chcp 65001` 도 먹지 않아 한글까지 깨집니다. 실제로 이것 때문에 Windows
  런처가 열리지 않았습니다 (2026-09-09). `.gitattributes` 의 `*.bat text eol=crlf`
  가 지켜주지만, 에디터 설정으로 덮어쓰지 않도록 주의하세요.
  확인: `git ls-files --eol | grep bat` → `w/crlf` 여야 정상입니다.
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
