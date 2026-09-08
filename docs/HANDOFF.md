# 인수인계 (2026-09-09 갱신 · 4단계까지)

> **다음 세션은 이 문서 → `CLAUDE.md` → `docs/DECISIONS.md` 순서로 읽으면 됩니다.**
> `docs/PLAN.md` 는 전체 로드맵이고, 이 문서는 "지금 어디까지 왔고 다음에 뭘 하나"입니다.

---

## 30초 요약

빔 프로젝터로 주차장 바닥에 **차량별 색 유도선**을 그려 빈자리로 안내하는 시스템의
개념검증 시뮬레이터. 진짜 질문은 경로 탐색이 아니라 이것입니다:

> **비협조 운전자가 남에게 배정된 자리를 강탈했을 때, 관제가 얼마나 매끄럽게 복구하는가?**

그래서 **관제 알고리즘은 운전자의 성향(`compliance`)을 절대 볼 수 없게** 코드를
물리적으로 분리했고, 그 경계를 테스트로 강제합니다. 이게 무너지면 연구가 무의미해집니다.

**0~4단계 완료. 브라우저에서 실시간으로 돌아갑니다. 다음은 5단계(강탈 시나리오).**

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
pytest tests/ -v                                        # 100개 통과해야 정상 (약 90초)
uvicorn server.app:app --reload                         # 브라우저 → localhost:8000
python -m sim.world.simulation --duration 300 --arrival-rate 0.15    # 헤드리스
python -m sim.metrics.trace_writer --duration 240 --out runs/demo    # 녹화
```

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
| 4 | **뷰어 연동 — WebSocket 라이브 + 녹화본 재생** | ✅ |
| 5 | **강탈 시나리오 — 다음 작업** | ⬜ |
| 6~10 | 복구 6종, 베이스라인, 실험, STL, 발표 마감 | ⬜ |

### 3단계 실측 (시드 6개 × 1200초, 도착률 0.15/초)

```
주차 완료      82 ~ 100대        평균 소요      38 ~ 45초
주행차 정지비율 4.6 ~ 9.4%        최장 정지      11 ~ 15초
주차면 이탈    0대                센서 오탐 강탈  548건 중 1건 (0.2%)
```

### 4단계 실측

```
녹화본 크기    240초 / 초당 3.3프레임 → 1.6 MB   (좌표 cm 반올림 + 폴리라인 델타)
라이브 스트림  초당 10프레임, 배속 0.5~8×, 일시정지·도착률 변경 지원
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
                 `run(duration, stride=n)` — 물리는 그대로 두고 관측만 솎아낸다

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

sim/metrics/
  trace_writer.py  프레임을 runs/<run_id>/trace.jsonl 로 녹화 (발표장의 보험)

server/app.py    /ws 에서 시뮬레이션을 굴려 프레임을 밀어 넣는다. 연결마다 독립 세션

viewer/js/       three.js 뷰어 (빌드 스텝 없음)
  source.js      라이브(WebSocket)와 녹화본을 같은 얼굴로 감싼다. 라이브 실패 시 자동 전환
  main.js        프레임을 받아 그리기만 한다 — 아무것도 결정하지 않는다

tests/
  test_layer_isolation.py  ★ 이 저장소에서 가장 중요한 테스트
  test_traffic.py          ★ 두 번째로 중요 — 교착이 없는가
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

## 다음 작업 — 5단계 (강탈 시나리오)

**이 프로젝트가 답하려던 질문이 드디어 시작되는 단계입니다.** 지금까지는 전원이
안내를 따르는 세계였습니다. 이제 일부가 배신합니다.

배관은 이미 다 깔려 있습니다. 관제가 센서만으로 강탈을 알아채는 경로는 완성됐고
`tests/test_control.py` 가 검증합니다. 남은 것은 **차가 실제로 이탈하게 만드는 것**뿐입니다.

### 만들 것

| 파일 | 내용 |
|---|---|
| `sim/agents/driver.py` | 이탈 판단 — 눈에 보이는 빈 자리가 더 좋으면 `compliance` 확률로 그리로 간다 |
| `sim/world/simulation.py` | `vision_enabled=True` 로 켜고, 성향 분포를 시나리오에서 받는다 |
| `sim/scenarios/*.yaml` | 도착률·성향 분포·시드·전략 이름 |

### 이미 준비된 것 (다시 만들지 마세요)

- `DriverProfile.compliance` — 자리는 잡혀 있고 현재 전원 1.0 입니다
- `SimConfig.vision_enabled` — 켜면 `Perception.visible_slots` 가 채워집니다.
  배선은 끝나 있고 기본값만 꺼져 있습니다
- `agents/perception.py` 의 `VisionModel` — 반경 25m · 시야각 · 통로 접면 규칙
- `VisibleSlot.looks_free` — **예약 여부는 들어 있지 않습니다.** 운전자 눈에는
  예약된 자리도 그냥 빈 자리로 보입니다. 강탈이 일어나는 근본 이유이므로
  여기에 예약 정보를 넣지 마세요
- 관제 쪽 전 경로: `SlotStolen` 판정 → `GuidanceReason.REROUTE` → `reroute_count`
  → 프레임 `events` → 뷰어 이벤트 로그. 강탈이 발생하면 화면에 바로 뜹니다

### 이탈 판단 (docs/PLAN.md 7)

발견한 빈 슬롯이 (a) 목적지보다 남은 주행거리를 D 이상 줄이고 (b) 도보거리도
나쁘지 않으면 → `compliance` 에 따른 확률로 이탈해 그 자리를 차지합니다.

> ⚠️ **먼저 미해결 이슈 2번(센서 오탐)을 0 으로 만드세요.** 강탈 횟수가 이 연구의
> 핵심 지표인데 바닥 노이즈가 0.2% 섞여 있으면 결과를 방어하기 어렵습니다.

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
