/**
 * 임시 데모 데이터 생성기.
 *
 * ⚠️ 이 파일은 4단계에서 실제 시뮬레이션 스트림(`source.js`)으로 대체된다.
 * 지금 존재하는 이유는 단 하나 — 바닥 렌더링과 유도선 셰이더가 제대로 보이는지
 * 눈으로 확인하기 위해서다. 여기 있는 경로 탐색은 관제 알고리즘이 아니며,
 * 진짜 알고리즘은 `sim/control/` 에 Python 으로 구현된다.
 *
 * 따라서 여기서는 정확성보다 "그림이 나오는가"만 본다.
 */

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

/** 노드 경로 → 주차장 좌표 폴리라인. 마지막에 주차면 진입 구간을 붙인다. */
export function pathToPolyline(lot, nodePath, slot) {
  const pos = new Map(lot.nodes.map((n) => [n.id, n.pos]));
  const pts = nodePath.map((id) => pos.get(id));

  if (slot) {
    const [cx, cy] = slot.center;
    const nose = slot.heading;
    // 주차면 입구 → 주차면 중앙. 후진 주차이므로 유도선은 입구까지만 그린다.
    const entry = [cx + Math.cos(nose) * (slot.length / 2), cy + Math.sin(nose) * (slot.length / 2)];
    pts.push(entry);
    pts.push([cx + Math.cos(nose) * 0.6, cy + Math.sin(nose) * 0.6]);
  }
  return pts;
}

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

/**
 * 미리보기 장면을 구성한다.
 *   - 일부 주차면을 이미 주차된 차량으로 채우고
 *   - 몇 대는 유도선을 따라 자기 자리로 이동시킨다
 */
export function buildDemoScenario(lot, { occupancy = 0.52, guided = 5, seed = 7 } = {}) {
  const rng = makeRng(seed);
  const slots = lot.slots.slice();

  // 출입구에서 가까운 자리가 먼저 찬다 — 실제 주차장이 그렇다
  const gate = lot.pedestrian_gates[0] ?? [0, 0];
  const walk = (s) => Math.hypot(s.center[0] - gate[0], s.center[1] - gate[1]);
  slots.sort((a, b) => walk(a) - walk(b));

  const parked = [];
  const taken = new Set();
  const target = Math.floor(slots.length * occupancy);
  for (let i = 0; i < slots.length && parked.length < target; i++) {
    // 앞쪽일수록 높은 확률로 차 있음
    const p = 1.0 - (i / slots.length) * 0.75;
    if (rng() < p) {
      parked.push(slots[i]);
      taken.add(slots[i].id);
    }
  }

  // 안내 대상: 아직 빈 자리 중 출입구에서 적당히 가까운 것들
  const free = slots.filter((s) => !taken.has(s.id));
  const picks = [];
  const stride = Math.max(1, Math.floor(free.length / (guided + 1)));
  for (let i = 0; i < guided && i * stride < free.length; i++) {
    picks.push(free[i * stride]);
  }

  const entry = lot.entry_nodes[0];
  const guidedCars = [];
  for (const slot of picks) {
    const nodePath = shortestPath(lot, entry, slot.access_node);
    if (!nodePath) continue;
    guidedCars.push({
      plate: fakePlate(rng),
      slot,
      polyline: pathToPolyline(lot, nodePath, slot),
      speed: 3.6 + rng() * 1.8,
      startDelay: guidedCars.length * 2.6 + rng(),
      colorSeed: rng(),
    });
  }

  return { parked, guided: guidedCars };
}

const HANGUL = "가나다라마바사아자차카타파하";

function fakePlate(rng) {
  const two = String(Math.floor(10 + rng() * 89));
  const ch = HANGUL[Math.floor(rng() * HANGUL.length)];
  const four = String(Math.floor(1000 + rng() * 8999));
  return `${two}${ch} ${four}`;
}
