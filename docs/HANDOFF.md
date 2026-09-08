# 인수인계 (2026-09-08 작성)

> **다음 세션은 이 문서 → `CLAUDE.md` → `docs/DECISIONS.md` 순서로 읽으면 됩니다.**
> `docs/PLAN.md` 는 전체 로드맵이고, 이 문서는 "지금 어디까지 왔고 다음에 뭘 하나"입니다.

---

## 30초 요약

빔 프로젝터로 주차장 바닥에 **차량별 색 유도선**을 그려 빈자리로 안내하는 시스템의
개념검증 시뮬레이터. 진짜 질문은 경로 탐색이 아니라 이것입니다:

> **비협조 운전자가 남에게 배정된 자리를 강탈했을 때, 관제가 얼마나 매끄럽게 복구하는가?**

그래서 **관제 알고리즘은 운전자의 성향(`compliance`)을 절대 볼 수 없게** 코드를
물리적으로 분리했고, 그 경계를 테스트로 강제합니다. 이게 무너지면 연구가 무의미해집니다.

**0~2단계 완료. 다음은 3단계(관제 알고리즘).**

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
처음 한 번만 1~2분 걸립니다.

```bash
pytest tests/ -v          # 35개 통과해야 정상
```

---

## 지금까지 한 것

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | Git/GitHub, 문서 4종, `.gitignore` | ✅ |
| 1 | 공용 값 타입, 도면 생성기, **계층 경계 테스트** | ✅ |
| 2 | 자전거 모델, Pure Pursuit, 후진 주차 | ✅ |
| — | 웹 뷰어(바닥·유도선·말풍선·주차/출차 생애주기) | ✅ *(임시 데모 데이터)* |
| 3 | **관제 알고리즘 — 다음 작업** | ⬜ |
| 4~10 | 뷰어 연동, 강탈, 복구 6종, 베이스라인, 실험 | ⬜ |

### 도면 (`layouts/mid_grid_120.json`)

120면 · 72 × 54 m · 통로 노드 71 · 방향성 엣지 80 · 구역 A~F

수평 통로 3개가 **일방통행으로 엇갈립니다** (H0 동→, H1 ←서, H2 동→).
수직 통로 3개는 양방향. 통로 안에서 U턴이 불가능해야 자리를 빼앗겼을 때
되돌아가는 비용이 **실제로 발생**하고, 그래야 복구 전략을 비교하는 의미가 생깁니다.
일방통행은 지켜야 할 규칙이 아니라 그래프상 아예 불가능하게 만들어 두었습니다.

### 2단계 실측

```
최소 회전반경 4.31m · 주차 위치 오차 0.155m · 각도 오차 0.29° · 후진 주차 13.8초
통로 방향(동/서) × 주차면 방향(위/아래) 네 조합 전부 성립
```

---

## 저장소 지도

```
sim/common/      세 계층이 공유하는 불변 값 타입만
  geometry.py    Vec2/Pose, 폴리라인 리샘플·회전량
  ids.py         PlateId/SlotId/NodeId (NewType), 차종·주차면 종류
  messages.py    ★ 센서 이벤트 4종 (관제가 세상을 아는 유일한 창구)
  lotmap.py      도면 + 렌더링용 정보(통로 폴리곤, 조경섬, 구역 라벨)
  vehicle.py     VehicleSpec / ControlInput / SelfState
  maneuver.py    후진 주차 궤적 기하 (순수 기하)

sim/world/       주차장 환경 — 계층 배선을 담당하는 유일한 곳
  lot_builder.py 격자 도면 생성기 (좌표 하드코딩 없음)
  physics.py     자전거 모델 적분
  sensors.py     ⬜ 미작성
  projector.py   ⬜ 미작성
  simulation.py  ⬜ 미작성 — 계층 배선은 여기 한 곳에서만

sim/control/     ⬜ 전부 미작성 (3단계에서 채움)
sim/agents/
  driving.py     Pure Pursuit + 종방향 제어 (운전 기술)
  driver.py      ⬜ 미작성 — DriverProfile(compliance)이 들어갈 곳
  perception.py  ⬜ 미작성

viewer/js/       three.js 뷰어 (빌드 스텝 없음)
  lot_floor.js   절차적 아스팔트 텍스처
  guidance.js    유도선 리본 셰이더
  track.js       궤적 스무딩·샘플링 (유도선과 출차 차량이 공용)
  demo.js        ⚠️ 임시 진행자 — 3~4단계에서 Python 스트림으로 대체
  main.js        부트스트랩

tests/
  test_layer_isolation.py  ★ 이 저장소에서 가장 중요한 테스트
  test_lotmap.py
  test_driving.py
```

---

## 절대 지켜야 할 것 (자세한 내용은 `CLAUDE.md`)

1. **`sim/control/` 은 `sim/agents/`, `sim/world/` 를 import 하면 안 됩니다.**
2. **`sim/control/` 안에 `compliance` 라는 단어가 등장하면 안 됩니다.**
   간접 조회·문자열 키·주석까지 전부 위반으로 잡힙니다.
3. 관제에 전달되는 것은 `SensorEvent` 뿐입니다. 차량 객체 참조 금지.
4. 커밋 전:
   ```bash
   pytest tests/test_layer_isolation.py -v
   grep -rn "compliance" sim/control/     # 결과가 비어야 함
   ```

**허용되는 것**: 번호판별 **과거 관측 이력**을 관제가 집계·학습하는 것.
성향을 읽는 것과 다릅니다 (`docs/DECISIONS.md` D-003). 복구 전략
`reputation_aware` 가 이걸 씁니다.

---

## 다음 작업 — 3단계 (관제 알고리즘)

### 만들 것

| 파일 | 내용 |
|---|---|
| `sim/control/api.py` | `ControlSystem` 프로토콜 — `on_events(list[SensorEvent]) -> list[ProjectorCommand]` |
| `sim/control/state.py` | 센서로만 갱신하는 세계 모델. 주차면 상태·예약·차량 최종 목격 위치 |
| `sim/control/routing.py` | 레인 그래프 A* + 회전 페널티 (좌회전에 더 큰 페널티) |
| `sim/control/cost.py` | 주차면 비용 = w₁주행거리 + w₂회전수 + w₃통로혼잡도 + w₄도보거리 + w₅차종제약 |
| `sim/control/allocators/greedy_nearest.py` | 첫 할당 전략 (기준선) |
| `sim/world/sensors.py` | ANPR · 슬롯 점유 센서 · 통로 검지기 |
| `sim/world/projector.py` | 유도선 상태 보관 |
| `sim/world/simulation.py` | **계층 배선** — 고정 timestep 루프 |

### 이미 준비된 것 (다시 만들지 마세요)

- `LotMap.successors(node)` — 일방통행이 이미 반영된 인접 리스트
- `LotMap.walk_distance(slot)` — 주차면 → 건물 출입구 도보 거리
- `sim.common.messages` — 센서 이벤트 4종, `GuidanceCommand`, `SlotStolen`, `RouteDeviation`
- `sim.common.maneuver.plan_reverse_parking()` — 후진 주차 궤적
- `sim.agents.driving.PathFollower` — 경로 추종. `control(state, speed_limit=…)` 로
  **속도 제한을 외부에서 주입할 수 있게** 이미 열려 있습니다
- `viewer/js/demo.js` 의 `DemoDirector` — 주차면 생애주기 상태 기계의 참고 모델
  (`free → reserved → occupied → free`)

### 프레임 포맷 (뷰어와 이미 합의된 형태)

```json
{"t": 12.34,
 "vehicles": [{"id":"12가3456","pose":[x,y,theta],"state":"driving","color":3,"model":"sedan_a"}],
 "guidance": [{"id":"12가3456","color":3,"polyline":[[x,y],…],"progress":0.42,"target":"C-14"}],
 "slots":    [{"id":"C-14","status":"reserved"}],
 "events":   [{"type":"slot_stolen","victim":"…","taker":"…","slot":"C-14"}],
 "kpi":      {"occupancy":0.62,"avg_park_time":41.2,"reroutes":3}}
```

---

## 미해결 이슈 3개 ⚠️

### 1. 차량이 서로 겹쳐서 지나갑니다

**현재 상태**: 뷰어의 차량은 궤적을 따라 미끄러질 뿐 서로를 인식하지 않습니다.
Python 쪽에도 아직 차간거리 로직이 없습니다.

**어디서 풀어야 하나 — 계층상 답은 정해져 있습니다.**
"앞차를 보고 멈추는 것"은 시뮬레이터의 기능이 아니라 **운전자의 행동**입니다.
그러니 world 가 차를 강제로 멈추면 안 되고, 이렇게 흘러야 합니다:

```
world   앞차까지의 여유 거리를 계산해서 각 차량에게 준다
          → Perception.pose_forward_clearance   (messages.py 에 이미 필드가 있습니다)
agents  그 값을 보고 스스로 감속한다
          → PathFollower.control(state, speed_limit=…)  (이미 인자가 열려 있습니다)
world   그 결과 제어 입력을 적분한다
```

`sim/world/physics.py` 의 `gap_to()` 가 여유 거리 계산의 출발점입니다. 아직 아무도
호출하지 않습니다. 3단계에서 `simulation.py` 를 만들 때 배선하면 됩니다.

교차로에서의 상호 양보는 별도 문제입니다. 아래 2번과 함께 보세요.

### 2. 교착(deadlock) 위험 — 아직 아무 대책 없음

차간거리를 넣는 순간 반드시 터집니다. 예상 시나리오:

| 상황 | 왜 막히나 |
|---|---|
| 양방향 수직 통로에서 마주 오는 두 대 | 서로 비켜줄 공간이 없음. 폭 6m 로는 교행이 아슬아슬 |
| 후진 주차 중인 차가 통로를 막음 | 13.8초 동안 뒤차 전체가 정지. 실제 주차장에서도 일어나는 일이라 **모델링해야 맞습니다** |
| 재할당이 여러 차를 같은 지점으로 몰아넣음 | 복구 전략이 만든 교착 — 이건 **연구 결과 그 자체**입니다 |

**대응 후보 (아직 결정 안 됨 — 결정하면 `docs/DECISIONS.md` 에 D-012 로 남기세요)**

1. **수직 통로도 일방통행으로** — 도면 파라미터만 바꾸면 됨. 교착을 원천 차단하지만
   우회 거리가 늘어남. ⚠️ `test_every_slot_is_reachable_from_the_entrance` 와
   `test_every_slot_can_reach_the_exit` 가 반드시 통과해야 합니다 (연결이 끊기기 쉬움)
2. **수직 통로 구간 상호배제** — 한 번에 한 방향만 진입. 세마포어. 기아 상태 주의
3. **시공간 예약** — 관제가 통로 엣지를 시간창 단위로 예약. 정석이지만 구현 비용이 큼
4. **교착 감지 + 양보 규칙** — 일정 시간 정지가 지속되면 한 대를 후진·우회시킴

**추천 순서**: 우선 4(안전망)를 먼저 넣어 시뮬레이션이 멈추지 않게 하고,
2를 기본 정책으로, 1은 도면 옵션으로 두어 실험에서 비교하는 것을 권합니다.
3은 여력이 있을 때.

> 교착이 **얼마나 자주 나는가**도 복구 전략 비교의 KPI 로 삼을 만합니다.
> "전체 재최적화가 교착을 더 많이 만든다" 같은 결과가 나오면 발표에서 강한 소재입니다.

### 3. 뷰어의 임시 데모 계층을 걷어내야 합니다

`viewer/js/demo.js` 는 **관제 알고리즘이 아닙니다.** 화면이 움직이는지 보려고 만든
최소한의 진행 로직입니다. 4단계에서 Python 스트림이 붙으면 통째로 삭제하세요.

같이 사라져야 할 것:
- `main.js` 의 `PARK_MANEUVER = 4.0` — 후진 주차를 4초 타이머로 때우고 있습니다.
  실제로는 `maneuver.py` 가 만든 궤적을 따라간 자세를 스트림으로 받아 그려야 합니다.
- `demo.js` 의 `_pickSlot()` — 진짜 비용 함수는 `sim/control/cost.py` 로 갑니다.

---

## 하지 말 것 (함정)

- **보상 정책(주차 할인 등)을 구현하지 마세요.** 재탐색 횟수와 우회 거리를 기록만
  합니다. 정책 설계는 사용자가 추후 논의하기로 한 사항입니다 (D-008).
- **기본 복구 전략(`global_rematch`)을 다른 것으로 바꾸지 마세요.** 6종 비교 대상입니다 (D-009).
- **비협조 행동을 늘리지 마세요.** 이번엔 "자리 강탈" 하나로 한정했습니다.
  배회·통로 정차·이중주차는 구조만 확장 가능하게 두고 구현하지 않습니다.
- **도면 좌표를 코드에 하드코딩하지 마세요.** `layouts/*.json` 이 진실이고,
  생성기는 `sim/world/lot_builder.py` 입니다. 커밋된 도면과 생성기 출력이
  어긋나면 `test_committed_layout_matches_the_generator` 가 실패합니다.
- **three.js 를 CDN 으로 바꾸지 마세요.** 발표장에 인터넷이 없을 수 있어
  `viewer/vendor/` 에 넣어두었습니다.

---

## 커밋 규칙

- 구현 순서의 단계마다 커밋 — 다른 세션이 히스토리로 진행 상황을 파악합니다
- **토큰·자격증명 절대 커밋 금지**:
  ```bash
  git ls-files | grep -Ei "token|secret|credential|\.env|\.key|\.pem"   # 비어야 함
  ```
- 설계 결정을 바꿔야 하면 `docs/DECISIONS.md` 에 새 항목을 추가하세요.
  기존 결정을 조용히 뒤집지 마세요.
