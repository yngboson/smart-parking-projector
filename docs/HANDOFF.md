# 인수인계 (2026-09-09 갱신)

> **다음 세션은 이 문서 → `CLAUDE.md` → `docs/DECISIONS.md` 순서로 읽으면 됩니다.**
> `docs/PLAN.md` 는 전체 로드맵이고, 이 문서는 "지금 어디까지 왔고 다음에 뭘 하나"입니다.

---

## 30초 요약

빔 프로젝터로 주차장 바닥에 **차량별 색 유도선**을 그려 빈자리로 안내하는 시스템의
개념검증 시뮬레이터. 진짜 질문은 경로 탐색이 아니라 이것입니다:

> **비협조 운전자가 남에게 배정된 자리를 강탈했을 때, 관제가 얼마나 매끄럽게 복구하는가?**

그래서 **관제 알고리즘은 운전자의 성향(`compliance`)을 절대 볼 수 없게** 코드를
물리적으로 분리했고, 그 경계를 테스트로 강제합니다. 이게 무너지면 연구가 무의미해집니다.

**0~3단계 완료. 헤드리스 시뮬레이션이 끝까지 돕니다. 다음은 4단계(뷰어 연동).**

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
pytest tests/ -v                      # 89개 통과해야 정상 (약 75초)
python -m sim.world.simulation --duration 300 --arrival-rate 0.15
```

마지막 명령이 헤드리스 시뮬레이션입니다. 3단계가 살아 있는지 30초 만에 확인할 수 있습니다.

> ⚠️ **Python 3.11 이상이 필요합니다.** macOS 기본 파이썬은 3.9 라 설치가 실패합니다.
> `brew install python@3.14` 후 그 인터프리터로 venv 를 만드세요.

---

## 지금까지 한 것

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | Git/GitHub, 문서 4종, `.gitignore` | ✅ |
| 1 | 공용 값 타입, 도면 생성기, **계층 경계 테스트** | ✅ |
| 2 | 자전거 모델, Pure Pursuit, 후진 주차 | ✅ |
| 3 | **관제 알고리즘 + 센서 + 프로젝터 + 계층 배선** | ✅ |
| — | 웹 뷰어(바닥·유도선·말풍선·주차/출차 생애주기) | ✅ *(아직 임시 데모 데이터)* |
| 4 | **뷰어 연동 — 다음 작업** | ⬜ |
| 5~10 | 강탈, 복구 6종, 베이스라인, 실험, STL | ⬜ |

### 3단계 실측 (시드 6개 × 1200초, 도착률 0.15/초)

```
주차 완료      82 ~ 100대        평균 소요      38 ~ 45초
주행차 정지비율 4.6 ~ 9.4%        최장 정지      11 ~ 15초
주차면 이탈    0대                센서 오탐 강탈  548건 중 1건 (0.2%)
```

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
  traffic.py     통로 합류 양보 (D-012). 교착 조사 전말이 모듈 주석에 있음
  projector.py   유도선 상태 + 진행률(지나온 구간 소거)
  simulation.py  ★ 계층 배선. 고정 timestep 루프. `python -m` 으로 실행 가능

sim/control/     관제 — sim.common 외에는 아무것도 import 하지 않음
  api.py         ControlSystem 프로토콜 + NullControl(무안내 베이스라인 뼈대)
  system.py      ProjectorControl — 기본 관제 본체
  state.py       센서로만 갱신하는 세계 모델. 강탈·이탈 판정, 번호판별 관측 이력
  routing.py     레인 그래프 A* + 회전 페널티 (좌회전이 더 비쌈)
  cost.py        주차면 비용 = 주행거리 + 회전 + 혼잡 + 도보거리 + 차종제약
  allocators/
    api.py       Allocator 프로토콜 + 레지스트리
    greedy_nearest.py   기준선 전략
  recovery/      ⬜ 미작성 (6단계)

sim/agents/      차량 — sim.common 외에는 아무것도 import 하지 않음
  driving.py     Pure Pursuit + 종방향 제어 (운전 기술)
  driver.py      ★ DriverProfile(compliance) 와 주행 상태 기계. 우측통행·비집고나가기
  perception.py  운전자 시야 규칙 (반경·시야각·통로 접면)

viewer/js/       three.js 뷰어 (빌드 스텝 없음)
  demo.js        ⚠️ 임시 진행자 — 4단계에서 Python 스트림으로 대체하고 삭제

tests/
  test_layer_isolation.py  ★ 이 저장소에서 가장 중요한 테스트
  test_traffic.py          ★ 두 번째로 중요 — 교착이 없는가
  test_routing.py, test_control.py, test_simulation.py
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

## 다음 작업 — 4단계 (뷰어 연동)

3단계가 이미 **뷰어와 합의된 프레임 포맷**을 만들어 냅니다. 남은 일은 배관입니다.

### 만들 것

| 파일 | 내용 |
|---|---|
| `server/app.py` | `/ws` 에서 `Simulation` 을 실제로 굴려 프레임을 흘려보낸다 (지금은 하트비트만) |
| `viewer/js/source.js` | WebSocket / trace 재생을 같은 인터페이스로 감싼다 (D-005) |
| `viewer/js/main.js` | `demo.js` 대신 스트림 프레임을 소비하도록 교체 |
| `sim/metrics/trace_writer.py` | 프레임을 `runs/<run_id>/trace.jsonl` 로 기록 |

### 이미 준비된 것 (다시 만들지 마세요)

- `Simulation.run(duration)` — 매 틱 `Frame` 을 내보내는 제너레이터
- `Frame.to_dict()` — 아래 포맷 그대로. **폴리라인 델타 압축이 이미 들어 있습니다**
  (개정된 유도선만 `polyline` 을 싣고, 나머지는 `progress` 만)
- 주차면 상태도 델타입니다 — **첫 프레임만 전수**, 이후엔 바뀐 것만
- `Projector.beams()` — 지금 바닥에 떠 있는 선 전부, 진행률 포함
- `sim.control.api.NullControl` — 무안내 베이스라인(D-010)의 뼈대

### 프레임 포맷 (실제 출력 그대로)

```json
{"t": 12.34,
 "vehicles": [{"id":"12가3456","pose":[x,y,theta],"state":"driving","color":3,"model":"sedan_a"}],
 "guidance": [{"id":"12가3456","color":3,"progress":0.42,"target":"C-14",
               "polyline":[[x,y]],"revision":0}],
 "slots":    [{"id":"C-14","status":"reserved"}],
 "events":   [{"type":"slot_stolen","victim":"…","taker":"…","slot":"C-14"}],
 "kpi":      {"occupancy":0.62,"avg_park_time":41.2,"reroutes":3,
              "active":8,"parked_total":57,"stolen":0,"forced_merges":12}}
```

- `pose` 는 **차체 중심** 기준입니다 (뒷축이 아님). 뷰어의 폴백 박스가 중심 정렬이라
  그렇게 맞췄습니다. STL 이 뒷축 원점이면 `models.json` 의 `pivot: "rear_axle"` 로 흡수합니다.
- `state` 는 `waiting / driving / parking / parked / leaving` 다섯 가지입니다.

### 같이 삭제할 것

- `viewer/js/demo.js` 통째로 — **관제 알고리즘이 아닙니다.** 화면이 움직이는지 보려고
  만든 최소 진행 로직입니다.
- `main.js` 의 `PARK_MANEUVER = 4.0` — 후진 주차를 4초 타이머로 때우고 있습니다.
  이제 실제 자세가 스트림으로 들어옵니다.

---

## 미해결 이슈 3개 ⚠️

### 1. `greedy_nearest` 가 모든 차를 한 구석으로 몰아넣습니다

**증상**: 도착 차량이 거의 전부 입구에서 가까운 A·E·F 구역 서쪽 끝에 배정됩니다.
그 결과 주차장 나머지 90% 가 비어 있는데 서쪽 통로만 붐빕니다.

**원인**: 비용 함수가 주행거리와 도보거리를 함께 보는데, 혼잡 항이 이걸 상쇄하지
못합니다. 혼잡도를 "병목 엣지" 방식으로 바꿔 흩어뜨려도 봤지만 **더 나빴습니다** —
통로를 가로지르는 장거리 이동이 늘어 정지 비율이 5% → 82% 로 뛰었습니다 (D-013).

**이건 버그가 아니라 기준선 전략의 알려진 약점입니다.** `congestion_aware` /
`hungarian_batch` / `zone_late_binding` 이 개선해야 할 지점이고, 비교 실험의 재료입니다.
고치려 하지 말고 **다른 전략을 추가**하세요 (`CLAUDE.md` 의 '전략을 추가하는 방법').

### 2. 슬롯 점유 센서 오탐이 0.2% 남아 있습니다

**증상**: 주차면에서 빠져나가던 차가 드물게 옆칸 센서를 울립니다. 그 칸이 다른
차에게 예약돼 있으면 관제가 **있지도 않은 강탈**을 보고합니다.

**현재 방어**: 속도 0.2 m/s 미만 + 주차면 방향과 25° 이내 정렬 + 길이 방향 ±1.2m +
횡방향 ±0.7m 를 모두 만족해야 점유로 판정합니다 (`sim/world/sensors.py`).
6시드 × 1200초에서 548건 중 1건까지 줄였습니다.

**왜 중요한가**: 강탈 횟수가 이 연구의 **핵심 지표**입니다. 5단계에서 진짜 강탈을
주입하기 전에 이 바닥 노이즈를 0 으로 만들어 두는 편이 좋습니다.
재현: `SimConfig(seed=4, arrival_rate=0.15)` 로 1200초.

**다음 수**: 시간 디바운스(같은 판정이 0.5초 이상 지속돼야 이벤트 발행)를 넣으면
기하 조건만으로 남은 경계 사례를 흡수할 수 있습니다.

### 3. `compliance` 는 아직 아무도 쓰지 않습니다 — 5단계의 본체

`DriverProfile.compliance` 필드는 `sim/agents/driver.py` 에 자리를 잡아 두었지만
**현재 전원 협조(1.0)** 입니다. 3단계는 관제가 정상 동작하는지부터 확인해야 했으니까요.

5단계에서 붙일 것:
- `SimConfig.vision_enabled = True` — 이미 배선돼 있습니다. 켜면 운전자가
  `Perception.visible_slots` 로 육안 관측을 받습니다 (`agents/perception.py` 의 시야 규칙).
- `Driver` 의 이탈 판단 — 발견한 빈 자리가 (a) 남은 주행거리를 D 이상 줄이고
  (b) 도보거리도 나쁘지 않으면, `compliance` 에 따른 확률로 이탈해 그 자리를 차지.
- 강탈이 발생하면 관제는 **센서로만** 알아챕니다. 그 경로는 이미 완성돼 있고
  `tests/test_control.py` 가 검증합니다.

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
