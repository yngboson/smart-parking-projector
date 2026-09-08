/**
 * 임시 데모 진행자 (Director).
 *
 * ⚠️ **이 파일은 3~4단계에서 Python 시뮬레이션 스트림으로 통째로 대체된다.**
 * 여기 있는 것은 관제 알고리즘이 아니다. 화면이 그럴듯하게 움직이는지 보기 위한
 * 최소한의 진행 로직일 뿐이다. 진짜 할당·경로·복구 알고리즘은 `sim/control/` 에
 * Python 으로 들어간다.
 *
 * 다만 **주차면 생애주기만큼은 실제 시뮬레이션과 같은 모양**으로 만들어 두었다.
 * 이후 Python 쪽을 만들 때 참고할 수 있게 하기 위해서다:
 *
 *     free ──배정──▶ reserved ──주차 완료──▶ occupied ──출차──▶ free
 *
 * 이 상태 기계가 있어야 "주차한 차가 자리를 계속 점유한다"와 "빈자리가 다시
 * 생긴다"가 성립하고, 그래야 강탈 시나리오를 얹을 수 있다.
 */

/** 결정론적 난수 — 새로고침해도 같은 장면이 나와야 비교가 된다. */
export function makeRng(seed = 7) {
  let s = seed >>> 0;
  return () => {
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5;  s >>>= 0;
    return s / 4294967296;
  };
}

/** 통로 그래프에서 최단 경로 (다익스트라). 일방통행은 엣지 방향에 이미 반영돼 있다. */
export function shortestPath(lot, from, to) {
  const succ = new Map();
  for (const e of lot.edges) {
    if (!succ.has(e.src)) succ.set(e.src, []);
    succ.get(e.src).push([e.dst, e.length]);
  }

  const dist = new Map([[from, 0]]);
  const prev = new Map();
  const seen = new Set();
  const queue = [[0, from]];

  while (queue.length) {
    queue.sort((a, b) => a[0] - b[0]);
    const [d, n] = queue.shift();
    if (seen.has(n)) continue;
    seen.add(n);
    if (n === to) break;

    for (const [m, w] of succ.get(n) ?? []) {
      const nd = d + w;
      if (nd < (dist.get(m) ?? Infinity)) {
        dist.set(m, nd);
        prev.set(m, n);
        queue.push([nd, m]);
      }
    }
  }

  if (!dist.has(to)) return null;
  const path = [to];
  while (prev.has(path[0])) path.unshift(prev.get(path[0]));
  return path;
}

const HANGUL = "가나다라마바사아자차카타파하";

function fakePlate(rng) {
  const two = String(Math.floor(10 + rng() * 89));
  const ch = HANGUL[Math.floor(rng() * HANGUL.length)];
  const four = String(Math.floor(1000 + rng() * 8999));
  return `${two}${ch} ${four}`;
}

/**
 * 주차장의 시간 흐름을 만드는 진행자.
 *
 * 도착 · 배정 · 주차 완료 · 체류 · 출차를 관리한다. 화면 요소는 전혀 건드리지 않고,
 * "이 차가 이 경로로 들어온다 / 나간다" 라는 지시만 내보낸다. 렌더링은 main.js 몫이다.
 */
export class DemoDirector {
  constructor(lot, opts = {}) {
    this.lot = lot;
    this.rng = makeRng(opts.seed ?? 7);

    this.maxGuided = opts.maxGuided ?? 5;
    this.arrivalGap = opts.arrivalGap ?? [3.0, 7.5];
    this.departGap = opts.departGap ?? [4.0, 10.0];
    this.minDwell = opts.minDwell ?? 30;

    this.slotById = new Map(lot.slots.map((s) => [s.id, s]));
    this.status = new Map(lot.slots.map((s) => [s.id, "free"]));
    this.parked = new Map();     // slotId → { plate, since }

    this.gate = lot.pedestrian_gates[0] ?? [0, 0];
    this.entry = lot.entry_nodes[0];
    this.exit = lot.exit_nodes[0];

    this.t = 0;
    this.guided = 0;
    this.nextArrival = 0.4;
    this.nextDepart = 14;
  }

  // ── 조회 ────────────────────────────────────────────────────────

  walk(slot) {
    return Math.hypot(slot.center[0] - this.gate[0], slot.center[1] - this.gate[1]);
  }

  get occupancy() {
    let n = 0;
    for (const st of this.status.values()) if (st === "occupied") n++;
    return n;
  }

  get reserved() {
    let n = 0;
    for (const st of this.status.values()) if (st === "reserved") n++;
    return n;
  }

  // ── 초기 점유 ───────────────────────────────────────────────────

  /** 시작 시점에 이미 세워져 있는 차량들. 출입구에서 가까운 자리부터 찬다. */
  seedParked(ratio = 0.45) {
    const slots = [...this.lot.slots].sort((a, b) => this.walk(a) - this.walk(b));
    const target = Math.floor(slots.length * ratio);
    const out = [];

    for (let i = 0; i < slots.length && out.length < target; i++) {
      const p = 1.0 - (i / slots.length) * 0.75;   // 앞쪽일수록 잘 찬다
      if (this.rng() >= p) continue;
      const slot = slots[i];
      this.status.set(slot.id, "occupied");
      // 이미 한참 서 있던 차로 취급해야 시작 직후에도 출차가 일어난다
      this.parked.set(slot.id, {
        plate: fakePlate(this.rng),
        since: -this.minDwell - this.rng() * 300,
      });
      out.push({ slot, plate: this.parked.get(slot.id).plate });
    }
    return out;
  }

  // ── 시간 진행 ───────────────────────────────────────────────────

  /** @returns {{arrivals: object[], departures: object[]}} 이번 스텝에 발생한 지시 */
  update(dt) {
    this.t += dt;
    const out = { arrivals: [], departures: [] };

    if (this.t >= this.nextArrival) {
      this.nextArrival = this.t + this._gap(this.arrivalGap);
      if (this.guided < this.maxGuided) {
        const job = this._planArrival();
        if (job) {
          this.guided++;
          out.arrivals.push(job);
        }
      }
    }

    if (this.t >= this.nextDepart) {
      this.nextDepart = this.t + this._gap(this.departGap);
      const job = this._planDeparture();
      if (job) out.departures.push(job);
    }

    return out;
  }

  _gap([lo, hi]) {
    return lo + this.rng() * (hi - lo);
  }

  // ── 도착 ────────────────────────────────────────────────────────

  _planArrival() {
    const slot = this._pickSlot();
    if (!slot) return null;

    const nodes = shortestPath(this.lot, this.entry, slot.access_node);
    if (!nodes) return null;

    this.status.set(slot.id, "reserved");
    return {
      kind: "arrival",
      plate: fakePlate(this.rng),
      slot,
      polyline: this._arrivalPolyline(nodes, slot),
      speed: 3.4 + this.rng() * 1.6,
      seed: this.rng(),
    };
  }

  /**
   * 배정 규칙 — 빈자리 중 출입구에서 가까운 쪽을 고르되 약간의 무작위를 섞는다.
   *
   * 이것은 관제 알고리즘이 **아니다.** 진짜 비용 함수(주행거리 · 회전수 · 통로
   * 혼잡도 · 도보거리)는 3단계에서 `sim/control/cost.py` 에 들어간다.
   */
  _pickSlot() {
    const free = this.lot.slots.filter((s) => this.status.get(s.id) === "free");
    if (!free.length) return null;

    free.sort((a, b) => this.walk(a) - this.walk(b));
    const window = Math.max(1, Math.min(free.length, 14));
    return free[Math.floor(this.rng() * window)];
  }

  _arrivalPolyline(nodes, slot) {
    const pos = new Map(this.lot.nodes.map((n) => [n.id, n.pos]));
    const pts = nodes.map((id) => pos.get(id));
    const nose = slot.heading;
    // 주차면 입구까지만 그린다. 후진 진입은 운전자 몫이다 (2단계 maneuver.py)
    pts.push([
      slot.center[0] + Math.cos(nose) * (slot.length / 2),
      slot.center[1] + Math.sin(nose) * (slot.length / 2),
    ]);
    pts.push([
      slot.center[0] + Math.cos(nose) * 0.5,
      slot.center[1] + Math.sin(nose) * 0.5,
    ]);
    return pts;
  }

  /** 차량이 실제로 주차를 마쳤다. 이제 이 자리는 점유 상태다. */
  notifyParked(plate, slotId) {
    this.status.set(slotId, "occupied");
    this.parked.set(slotId, { plate, since: this.t });
    this.guided = Math.max(0, this.guided - 1);
  }

  /** 안내를 포기했다 (경로 생성 실패 등). 예약을 풀어준다. */
  notifyAborted(slotId) {
    if (this.status.get(slotId) === "reserved") this.status.set(slotId, "free");
    this.guided = Math.max(0, this.guided - 1);
  }

  // ── 출차 ────────────────────────────────────────────────────────

  _planDeparture() {
    const ready = [...this.parked.entries()].filter(
      ([id, info]) => this.status.get(id) === "occupied" && this.t - info.since >= this.minDwell
    );
    if (!ready.length) return null;

    const [slotId, info] = ready[Math.floor(this.rng() * ready.length)];
    const slot = this.slotById.get(slotId);

    const nodes = shortestPath(this.lot, slot.access_node, this.exit);
    if (!nodes) return null;

    // 출차가 시작되면 즉시 자리를 비운다 — 뒤차가 이 자리를 배정받을 수 있어야 한다.
    // 실제 시스템에서도 출차 감지 즉시 가용 자리로 잡는 편이 회전율에 유리하다.
    this.status.set(slotId, "free");
    this.parked.delete(slotId);

    return {
      kind: "departure",
      plate: info.plate,
      slot,
      polyline: this._departurePolyline(nodes, slot),
      speed: 3.0 + this.rng() * 1.4,
    };
  }

  _departurePolyline(nodes, slot) {
    const pos = new Map(this.lot.nodes.map((n) => [n.id, n.pos]));
    const nose = slot.heading;
    // 주차된 차는 앞머리가 통로를 향하므로 전진으로 빠져나온다
    const pts = [
      [slot.center[0] + Math.cos(nose) * 0.5, slot.center[1] + Math.sin(nose) * 0.5],
      [
        slot.center[0] + Math.cos(nose) * (slot.length / 2 + 0.6),
        slot.center[1] + Math.sin(nose) * (slot.length / 2 + 0.6),
      ],
    ];
    for (const id of nodes) pts.push(pos.get(id));
    return pts;
  }
}
