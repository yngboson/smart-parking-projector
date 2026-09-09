/**
 * 뷰어 부트스트랩.
 *
 * 화면에 보이는 모든 것은 **Python 시뮬레이션이 보낸 프레임**이다. 뷰어는 아무것도
 * 결정하지 않는다 — 어느 자리를 줄지, 누가 언제 나갈지, 차가 어떻게 꺾는지는 전부
 * 저쪽에서 이미 풀린 문제다. 여기서는 그리기만 한다.
 *
 * 이 분리가 중요한 이유: 뷰어가 조금이라도 시뮬레이션을 흉내 내기 시작하면
 * "화면에서는 잘 되는데 실험 결과와 다른" 상황이 생긴다. 발표에서 보여주는 화면과
 * 표에 적는 숫자가 같은 실행에서 나와야 한다.
 *
 * 라이브(WebSocket)와 녹화본(trace.jsonl)은 같은 포맷이므로 이 파일은 둘을
 * 구분하지 않는다 (docs/DECISIONS.md D-005).
 */

import { Stage, THREE } from "/js/scene.js";
import { buildFloor } from "/js/lot_floor.js";
import { GuidanceLine, TargetMarker } from "/js/guidance.js";
import { EventFlash } from "/js/highlight.js";
import { Palette } from "/js/palette.js";
import { VehicleModels, buildContactShadow, pickBodyColor } from "/js/vehicles.js";
import { TraceSource, connectBestSource, listTraces } from "/js/source.js";
import { ModelTuner } from "/js/model_tuner.js";

const $ = (id) => document.getElementById(id);

/**
 * 프레임 사이를 메우는 보간의 세기 (1/초).
 *
 * 시뮬레이션은 초당 10프레임(녹화본은 5)을 보내는데 화면은 60fps 다. 그대로 찍으면
 * 차가 뚝뚝 끊겨 보인다. 클수록 프레임에 빨리 붙고 작을수록 부드럽지만 뒤처진다.
 */
const SMOOTHING = 14.0;

/** 입구 안내 말풍선을 띄워두는 시간 (초). */
const BUBBLE_HOLD = 4.5;

/** 이벤트 로그에 남기는 최대 줄 수. */
const EVENT_LOG = 6;

/**
 * 모델 이름별 전장·전폭 (m). STL 이 없을 때 그릴 박스 크기다.
 *
 * `sim/common/ids.py` 의 `VehicleClass.footprint` 와 같은 값이며, 프레임에 크기를
 * 싣지 않기 위해 여기 둔다 — 매 프레임 모든 차량의 제원을 보내는 것은 낭비다.
 */
const MODEL_SIZE = {
  hatch_a: [3.6, 1.6],
  sedan_a: [4.7, 1.85],
  suv_a: [4.9, 1.9],
  van_a: [5.2, 1.95],
};
const DEFAULT_SIZE = [4.7, 1.85];

export async function boot() {
  const lot = await fetch("/api/layout").then((r) => r.json());

  const stage = new Stage($("stage"));
  stage.fitTo(lot);
  stage.scene.add(buildFloor(THREE, lot));
  stage.addLampPosts(lampPositions(lot));

  const models = new VehicleModels(THREE);
  await models.load();

  const world = new App(stage, lot, models);

  $("lot-name").textContent =
    `${lot.name} · ${lot.slots.length}면 · ${Math.round(lot.bounds[2] - lot.bounds[0])} × ` +
    `${Math.round(lot.bounds[3] - lot.bounds[1])} m`;

  world.source = await connectBestSource(
    (frame, opts) => world.applyFrame(frame, opts),
    (s) => showStatus(s)
  );

  wireControls(stage, world);

  // STL 조정판. 모델이 하나도 없으면 스스로 숨는다 (9단계).
  const tuner = new ModelTuner(THREE, stage, models, lot, $("tuner"));
  if (tuner.mount()) $("tuner-panel").style.display = "";

  $("loading").classList.add("gone");

  // 브라우저 콘솔에서 장면을 들여다볼 수 있게 해둔다 (디버깅용)
  globalThis.__sim = { stage, world, lot, models, tuner };

  let last = performance.now();
  const loop = (now) => {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    stage.update(dt);
    world.update(dt);
    stage.render();
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

class App {
  constructor(stage, lot, models) {
    this.stage = stage;
    this.lot = lot;
    this.models = models;
    this.source = null;

    this.slotById = new Map(lot.slots.map((s) => [s.id, s]));
    this.palette = new Palette();
    this.bubbles = new BubbleLayer(stage, lot);

    this.cars = new Map();        // 번호판 → { group, target, shown, size }
    this.lines = new Map();       // 번호판 → { line, marker, style, revision }
    this.slotStatus = new Map();
    this.kpi = {};
    this.events = [];
    this.glow = 1.0;
    this.t = 0;

    this.flashes = [];
    this.following = null;        // 카메라가 따라가는 번호판

    this.graphGroup = buildGraphOverlay(THREE, lot);
    this.graphGroup.visible = false;
    stage.scene.add(this.graphGroup);
  }

  // ── 프레임 수신 ───────────────────────────────────────────────

  applyFrame(frame, { seek = false } = {}) {
    if (seek) this.clear();
    // 건너뛴 직후에는 입구 말풍선을 띄우지 않는다. 되살아난 유도선이 열 개면
    // 말풍선도 열 개가 한꺼번에 뜬다 — 이미 지나간 순간의 안내다.
    this._silent = seek;
    this.t = frame.t;
    this.syncVehicles(frame.vehicles ?? []);
    this.syncGuidance(frame.guidance ?? []);
    this._silent = false;

    for (const s of frame.slots ?? []) this.slotStatus.set(s.id, s.status);
    // 지나간 사건은 로그에만 넣는다 — 고리를 띄우면 되감을 때마다 화면이 번쩍인다
    for (const e of frame.history ?? []) this.logEvent(e, { quiet: true });
    for (const e of frame.events ?? []) this.logEvent(e);

    this.kpi = frame.kpi ?? {};
    this.refreshStats();
  }

  /**
   * 화면에 있는 것을 전부 지운다. 타임라인을 건너뛴 직후에만 부른다.
   *
   * 지우지 않으면 건너뛰기 전의 차량과 유도선이 그대로 남는다 — 프레임은
   * "지금 있는 것"만 싣기 때문에, 되감기로 아직 안 들어온 차를 지울 방법이
   * 그쪽에는 없다.
   */
  clear() {
    for (const car of this.cars.values()) {
      if (car.group) this.stage.scene.remove(car.group);
    }
    this.cars.clear();
    for (const plate of [...this.lines.keys()]) this.disposeLine(plate);
    for (const f of this.flashes) this.stage.scene.remove(f.group);
    this.flashes.length = 0;
    this.events.length = 0;
    renderEvents(this.events, this);
    // **추적은 끊지 않는다.** 한 칸 넘기는 것만으로 따라가던 차를 놓치면,
    // 사건 순간을 한 프레임씩 짚어 보는 동안 카메라가 계속 풀린다.
    // 그 차가 정말 사라졌다면 `Stage.follow` 가 스스로 놓는다.
  }

  /**
   * 차량 목록을 프레임에 맞춘다.
   *
   * 프레임에서 사라진 번호판은 주차장을 떠난 것이다 — 화면에서도 지운다.
   * 새로 나타난 번호판은 입구를 통과한 것이다.
   */
  syncVehicles(rows) {
    const seen = new Set();

    for (const row of rows) {
      seen.add(row.id);
      let car = this.cars.get(row.id);
      if (!car) {
        car = { group: null, target: null, shown: null, size: sizeOf(row.model) };
        this.cars.set(row.id, car);
        this.spawn(row, car);
      }
      car.state = row.state;
      car.target = { x: row.pose[0], y: row.pose[1], theta: row.pose[2] };
      if (!car.shown) car.shown = { ...car.target };
    }

    for (const [plate, car] of [...this.cars]) {
      if (seen.has(plate)) continue;
      if (car.group) this.stage.scene.remove(car.group);
      this.cars.delete(plate);
    }
  }

  async spawn(row, car) {
    const [length, width] = car.size;
    const group = await this.models.create(pickModel(this.models.names, hash(row.id)), {
      length, width, color: pickBodyColor(hash(row.id)),
    });
    group.add(buildContactShadow(THREE, length, width));

    // 생성이 끝나기 전에 차가 떠났을 수도 있다
    if (!this.cars.has(row.id)) return;
    car.group = group;
    if (car.shown) {
      group.position.set(car.shown.x, 0, -car.shown.y);
      group.rotation.y = car.shown.theta;
    }
    this.stage.scene.add(group);
  }

  /**
   * 유도선을 프레임에 맞춘다.
   *
   * 폴리라인은 **개정될 때만** 실려 온다 (자리를 빼앗겨 다시 안내받는 순간 등).
   * 나머지 프레임은 진행률만 온다 — 그래서 trace 파일이 한 자릿수 작아진다.
   */
  syncGuidance(rows) {
    const seen = new Set();

    for (const row of rows) {
      seen.add(row.id);
      let g = this.lines.get(row.id);

      if (row.polyline && (!g || g.revision !== row.revision)) {
        const style = g?.style ?? this.palette.acquire(row.id);
        if (g) this.disposeLine(row.id, { keepColor: true });
        g = this.buildLine(row, style);
      }
      if (g) {
        g.line.setProgress(row.progress ?? 0);
        g.slotId = row.target;
      }
    }

    for (const plate of [...this.lines.keys()]) {
      if (!seen.has(plate)) this.disposeLine(plate);
    }
  }

  buildLine(row, style) {
    const line = new GuidanceLine(THREE, row.polyline, style.color, {
      laneOffset: Palette.laneOffset(style.lane),
    });
    line.setGlow(this.glow);
    this.stage.scene.add(line.mesh);

    const slot = this.slotById.get(row.target);
    const marker = slot ? new TargetMarker(THREE, slot, style.color) : null;
    if (marker) this.stage.scene.add(marker.mesh);

    const g = {
      plate: row.id, style, line, marker,
      revision: row.revision ?? 0, slotId: row.target, slot,
    };
    this.lines.set(row.id, g);
    if (!this._silent) this.bubbles.show(g, this.t);
    return g;
  }

  disposeLine(plate, { keepColor = false } = {}) {
    const g = this.lines.get(plate);
    if (!g) return;
    g.line.dispose();
    g.marker?.dispose();
    this.stage.scene.remove(g.line.mesh);
    if (g.marker) this.stage.scene.remove(g.marker.mesh);
    this.lines.delete(plate);
    this.bubbles.remove(g);
    if (!keepColor) this.palette.release(plate);
  }

  logEvent(e, { quiet = false } = {}) {
    const text =
      e.type === "slot_stolen"
        ? `<b>자리 강탈</b> ${e.taker} 가 ${e.slot} 을 차지 — ${e.victim} 재배정`
        : `경로 이탈 ${e.plate} (${e.node})`;
    const at = e.t ?? this.t;
    this.events.unshift({ t: at, text, kind: e.type, slot: e.slot, plate: e.victim ?? e.plate });
    this.events.length = Math.min(this.events.length, EVENT_LOG);
    renderEvents(this.events, this);

    if (!quiet && e.type === "slot_stolen") this.flashSlot(e.slot, e.victim);
  }

  /**
   * 강탈이 일어난 자리를 화면에서 짚는다.
   *
   * 이벤트 로그에 한 줄 뜨는 것만으로는 청중이 차 서른 대 사이에서 그 자리를
   * 찾지 못한다. 발표에서 가장 중요한 순간이 가장 안 보이는 순간이 된다.
   */
  flashSlot(slotId, victim) {
    const slot = this.slotById.get(slotId);
    if (!slot) return;
    const color = this.lines.get(victim)?.style.color ?? 0xff6b5a;
    const flash = new EventFlash(THREE, slot, color);
    this.stage.scene.add(flash.group);
    this.flashes.push(flash);
  }

  // ── 카메라 추적 ───────────────────────────────────────────────

  followPlate(plate) {
    if (this.following === plate) {
      this.stopFollowing();
      return;
    }
    this.following = plate;
    this.stage.follow(() => {
      const car = this.cars.get(plate);
      return car?.shown ?? null;      // 차가 떠나면 null → Stage 가 스스로 놓는다
    });
    renderLegend([...this.lines.values()], this);
  }

  stopFollowing() {
    if (!this.following) return;
    this.following = null;
    this.stage.unfollow();
    renderLegend([...this.lines.values()], this);
  }

  // ── 매 화면 프레임 ────────────────────────────────────────────

  update(dt) {
    // 시뮬레이션 프레임은 초당 5~10장, 화면은 60장. 그 사이를 메운다.
    const k = 1 - Math.exp(-SMOOTHING * dt);
    for (const car of this.cars.values()) {
      if (!car.group || !car.target) continue;
      const s = car.shown;
      s.x += (car.target.x - s.x) * k;
      s.y += (car.target.y - s.y) * k;
      s.theta += wrapAngle(car.target.theta - s.theta) * k;
      car.group.position.set(s.x, 0, -s.y);
      car.group.rotation.y = s.theta;
    }

    for (const g of this.lines.values()) {
      g.line.update(dt);
      g.marker?.update(dt);
    }

    for (const f of [...this.flashes]) {
      f.update(dt);
      if (!f.done) continue;
      this.stage.scene.remove(f.group);
      f.dispose();
      this.flashes.splice(this.flashes.indexOf(f), 1);
    }

    // 따라가던 차가 사라지면 Stage 가 놓는다 — 표시도 같이 정리한다
    if (this.following && !this.stage.following) this.stopFollowing();

    this.bubbles.update(this.t);
  }

  setGlow(g) {
    this.glow = g;
    for (const l of this.lines.values()) l.line.setGlow(g);
  }

  refreshStats() {
    const total = this.lot.slots.length;
    $("s-slots").textContent = total;
    $("s-occ").innerHTML =
      `${Math.round((this.kpi.occupancy ?? 0) * 100)}<small>%</small>`;
    $("s-guided").textContent = this.lines.size;
    $("s-reroute").textContent = this.kpi.reroutes ?? 0;
    renderLegend([...this.lines.values()], this);
  }
}

/**
 * 입구 안내 말풍선.
 *
 * 차단기가 번호판을 읽고 운전자에게 "당신 선은 이 색"이라고 알려주는 순간이다.
 * 이 시스템의 사용자 경험 전체가 이 한 문장에 압축되어 있으므로 화면에 보여야 한다.
 *
 * 3D 스프라이트가 아니라 HTML 오버레이로 그린다. 한글이 또렷하게 나오고
 * 카메라를 당겨도 글자가 깨지지 않는다.
 */
class BubbleLayer {
  constructor(stage, lot) {
    this.stage = stage;
    this.root = $("bubbles");
    this.active = [];
    this.gate = lot.pedestrian_gates[0] ?? null;

    const entry = lot.nodes.find((n) => n.id === lot.entry_nodes[0]);
    this.anchor = entry
      ? new THREE.Vector3(entry.pos[0], 5.6, -entry.pos[1])
      : new THREE.Vector3(0, 5, 0);
  }

  show(g, t) {
    if (!g.slot || g.bubble) return;
    const css = Palette.css(g.style.color);
    const walk = this.gate
      ? Math.round(Math.hypot(g.slot.center[0] - this.gate[0], g.slot.center[1] - this.gate[1]))
      : 0;

    const el = document.createElement("div");
    el.className = "bubble";
    el.innerHTML =
      `<div class="tag">번호판 인식</div>` +
      `<div class="plate">${g.plate}</div>` +
      `<div class="msg">` +
      `<span class="swatch" style="background:${css}"></span>` +
      `<b style="color:${css}">${g.style.name}</b>색 선을 따라가 주시기 바랍니다` +
      `</div>` +
      `<div class="dest">배정 주차면 <b>${g.slot.id}</b> · 출입구까지 도보 <b>${walk}m</b></div>`;

    this.root.appendChild(el);
    g.bubble = el;
    g.bubbleUntil = t + BUBBLE_HOLD;
    this.active.push(g);
  }

  remove(g) {
    if (!g.bubble) return;
    const el = g.bubble;
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 360);
    g.bubble = null;
    this.active = this.active.filter((x) => x !== g);
  }

  /** 3D 앵커 위치를 화면 좌표로 투영해 말풍선을 붙인다. */
  update(t) {
    for (const g of [...this.active]) {
      if (t >= g.bubbleUntil) this.remove(g);
    }
    if (!this.active.length) return;

    const p = this.anchor.clone().project(this.stage.camera);
    const behind = p.z > 1;
    const x = (p.x * 0.5 + 0.5) * innerWidth;
    const y = (-p.y * 0.5 + 0.5) * innerHeight;

    // 여러 대가 동시에 들어오면 위로 쌓는다
    this.active.forEach((g, i) => {
      if (!g.bubble) return;
      g.bubble.style.display = behind ? "none" : "block";
      g.bubble.style.left = `${x}px`;
      g.bubble.style.top = `${y - i * 132}px`;
    });
  }
}

// ── 화면 조각 ─────────────────────────────────────────────────────

/**
 * 안내 중인 차량 목록. **한 줄을 누르면 카메라가 그 차를 따라간다.**
 *
 * 발표에서 "이 차가 지금 무엇을 겪고 있는지 보시죠"로 넘어가는 통로다. 전체 화면만
 * 보여 주면 차 서른 대가 동시에 움직여서 아무 이야기도 전달되지 않는다.
 */
function renderLegend(lines, world) {
  const box = $("legend-items");
  box.innerHTML = "";
  if (!lines.length) {
    box.innerHTML = `<div class="item"><span class="slot">안내 중인 차량 없음</span></div>`;
    return;
  }
  for (const g of lines) {
    const css = Palette.css(g.style.color);
    const el = document.createElement("div");
    el.className = "item clickable" + (world?.following === g.plate ? " tracking" : "");
    el.title = "누르면 카메라가 이 차를 따라갑니다";
    el.innerHTML =
      `<span class="chip" style="background:${css}"></span>` +
      `<span>${g.plate}</span>` +
      `<span class="slot">${g.style.name}색 → ${g.slotId}</span>`;
    el.addEventListener("click", () => world?.followPlate(g.plate));
    box.appendChild(el);
  }
}

/** 이벤트 로그. 강탈 줄을 누르면 카메라가 **그 자리**를 비춘다. */
function renderEvents(events, world) {
  const box = $("event-items");
  if (!box) return;
  box.innerHTML = "";
  if (!events.length) {
    box.innerHTML = `<div class="item"><span class="slot">아직 없음</span></div>`;
    return;
  }
  for (const e of events) {
    const el = document.createElement("div");
    const jumpable = Boolean(world && e.slot && world.slotById.get(e.slot));
    el.className = `item ${e.kind}` + (jumpable ? " clickable" : "");
    if (jumpable) el.title = "누르면 그 자리를 비춥니다";
    el.innerHTML =
      `<span class="slot">${e.t.toFixed(0)}s</span><span>${e.text}</span>`;
    if (jumpable) {
      el.addEventListener("click", () => {
        const slot = world.slotById.get(e.slot);
        world.stage.lookAtPoint(slot.center[0], slot.center[1]);
        world.flashSlot(e.slot, e.plate);
      });
    }
    box.appendChild(el);
  }
}

function showStatus({ connected, live, text }) {
  const el = $("conn");
  el.classList.toggle("on", !!connected);
  el.classList.toggle("replay", connected && !live);
  $("conn-text").textContent = text;
}

/** 가로등 자리 — 조경섬 위에 세운다. 실제 주차장도 섬에 등을 박는다. */
function lampPositions(lot) {
  const bySide = new Map();
  for (const is of lot.islands) {
    const key = is.id.slice(-1);          // W 또는 E
    if (!bySide.has(key)) bySide.set(key, []);
    bySide.get(key).push(is);
  }
  const out = [];
  for (const list of bySide.values()) {
    list.sort((a, b) => a.center[1] - b.center[1]);
    for (let i = 0; i < list.length; i += 2) {
      out.push([list[i].center[0], list[i].center[1]]);
    }
  }
  return out;
}

function sizeOf(model) {
  return MODEL_SIZE[model] ?? DEFAULT_SIZE;
}

function pickModel(names, seed) {
  if (!names.length) return null;
  return names[Math.abs(Math.floor(seed * 7919)) % names.length];
}

function wrapAngle(a) {
  while (a > Math.PI) a -= Math.PI * 2;
  while (a < -Math.PI) a += Math.PI * 2;
  return a;
}

function buildGraphOverlay(THREE, lot) {
  const g = new THREE.Group();
  const pos = new Map(lot.nodes.map((n) => [n.id, n.pos]));

  const pts = [];
  for (const e of lot.edges) {
    const a = pos.get(e.src);
    const b = pos.get(e.dst);
    if (!a || !b) continue;
    pts.push(new THREE.Vector3(a[0], 0.4, -a[1]), new THREE.Vector3(b[0], 0.4, -b[1]));
  }
  g.add(new THREE.LineSegments(
    new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: 0x63b3ff, transparent: true, opacity: 0.45 })
  ));

  const dot = new THREE.SphereGeometry(0.34, 8, 6);
  const dotMat = new THREE.MeshBasicMaterial({ color: 0x9fd2ff });
  for (const n of lot.nodes) {
    const m = new THREE.Mesh(dot, dotMat);
    m.position.set(n.pos[0], 0.4, -n.pos[1]);
    g.add(m);
  }
  return g;
}

function wireControls(stage, world) {
  const seg = (id, initial, onPick) => {
    const box = $(id);
    if (!box) return;
    const apply = (v) => {
      for (const b of box.querySelectorAll("button")) {
        b.setAttribute("aria-pressed", String(b.dataset.v === v));
      }
      onPick(v);
    };
    box.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (b) apply(b.dataset.v);
    });
    apply(initial);
  };

  const glowSlider = $("r-glow");
  const applyPreset = (name) => {
    const p = stage.applyPreset(name);
    glowSlider.value = String(Math.round(p.glow * 100));
    glowSlider.dispatchEvent(new Event("input"));
  };

  seg("seg-light", "dusk", applyPreset);
  seg("seg-cam", "bird", (v) => stage.setCamera(v));
  seg("seg-speed", "1", (v) => world.source?.setSpeed(Number(v)));

  glowSlider.addEventListener("input", () => {
    const g = Number(glowSlider.value) / 100;
    $("v-glow").textContent = g.toFixed(2);
    world.setGlow(g);
  });

  $("c-graph").addEventListener("change", (e) => {
    world.graphGroup.visible = e.target.checked;
  });

  const pause = $("btn-pause");
  if (pause) {
    let paused = false;
    pause.addEventListener("click", () => {
      paused = !paused;
      paused ? world.source?.pause() : world.source?.resume();
      pause.textContent = paused ? "재생" : "일시정지";
      pause.setAttribute("aria-pressed", String(paused));
    });
  }

  wireScenario(world);
  wireTimeline(world);
}

/**
 * 녹화본 타임라인 — 스크러버와 녹화본 선택.
 *
 * **발표에서 이것이 없으면 사고가 난다.** 강탈은 900초짜리 녹화본 중 어느 한
 * 순간에 일어나고, 그 순간을 다시 보여 달라는 질문이 반드시 나온다. 처음부터
 * 다시 트는 것 말고 방법이 없으면 그 자리에서 답을 못 한다.
 *
 * 라이브에서는 숨긴다 — 아직 오지 않은 시각으로 끌 수는 없다.
 */
function wireTimeline(world) {
  const box = $("timeline");
  const bar = $("r-seek");
  const label = $("v-seek");
  const picker = $("sel-trace");
  if (!box || !bar) return;

  const src = world.source;
  if (!src?.seekable) {
    // 라이브다 — 아직 오지 않은 시각으로 끌 수는 없다
    box.style.display = "none";
    picker?.closest(".field")?.style.setProperty("display", "none");
    return;
  }
  box.style.display = "";

  let dragging = false;
  src.onProgress(({ index, count, t, total }) => {
    if (!dragging) bar.value = String(count > 1 ? (index / (count - 1)) * 1000 : 0);
    label.textContent = `${t.toFixed(0)} / ${total.toFixed(0)}초`;
  });

  // 끄는 동안에도 화면이 따라와야 어디를 짚었는지 알 수 있다
  bar.addEventListener("input", () => {
    dragging = true;
    src.seekFraction(Number(bar.value) / 1000);
  });
  bar.addEventListener("change", () => { dragging = false; });

  $("btn-back")?.addEventListener("click", () => src.step(-1));
  $("btn-fwd")?.addEventListener("click", () => src.step(+1));

  fillTracePicker(picker, src);
}

async function fillTracePicker(picker, src) {
  if (!picker) return;
  const traces = await listTraces();
  if (traces.length < 2) {
    picker.closest(".field")?.style.setProperty("display", "none");
    return;
  }
  picker.innerHTML = traces
    .map((t) => `<option value="${t.path}">${t.id ?? t.path}</option>`)
    .join("");
  picker.value = src.url;
  picker.addEventListener("change", () => {
    // 페이지를 다시 여는 편이 가장 확실하다 — 장면을 통째로 새로 짓는다
    location.search = `?trace=${encodeURIComponent(picker.value)}`;
  });
}

/**
 * 시나리오와 조건 슬라이더.
 *
 * 라이브에서만 의미가 있다 — 녹화본은 이미 벌어진 일이라 조건을 바꿀 수 없다.
 * 조건을 바꾸면 시뮬레이션이 처음부터 다시 돈다. 발표에서 "비협조 비율을 0 으로
 * 두면 이런 일이 안 일어납니다" 를 그 자리에서 보여주기 위한 장치다.
 */
async function wireScenario(world) {
  const live = world.source?.live === true;
  const select = $("sel-scenario");
  const arrival = $("r-arrival");
  const defect = $("r-defect");
  const note = $("scenario-note");

  for (const el of [select, arrival, defect]) if (el) el.disabled = !live;

  const showArrival = () =>
    ($("v-arrival").textContent = `${(Number(arrival.value) / 10).toFixed(1)}대/분`);
  const showDefect = () =>
    ($("v-defect").textContent = `${defect.value}%`);

  arrival?.addEventListener("input", showArrival);
  defect?.addEventListener("input", showDefect);
  arrival?.addEventListener("change", () =>
    world.source?.reset?.({ arrival_rate: Number(arrival.value) / 1000 })
  );
  defect?.addEventListener("change", () =>
    world.source?.reset?.({ noncompliant_share: Number(defect.value) / 100 })
  );

  if (!live) {
    if (note) note.textContent = "녹화본을 재생 중입니다. 조건은 바꿀 수 없습니다.";
    showArrival();
    showDefect();
    return;
  }

  let body;
  try {
    body = await fetch("/api/scenarios").then((r) => r.json());
  } catch {
    return;
  }

  const current = world.source?.hello?.scenario ?? body.default;
  const byName = new Map(body.scenarios.map((s) => [s.name, s]));

  select.innerHTML = body.scenarios
    .map((s) => `<option value="${s.name}">${s.name}</option>`)
    .join("");
  select.value = current;

  const sync = (name) => {
    const s = byName.get(name);
    if (!s) return;
    if (note) note.textContent = s.description;
    arrival.value = String(Math.round(s.arrival_rate * 1000));
    defect.value = String(Math.round(s.noncompliant_share * 100));
    showArrival();
    showDefect();
  };

  select.addEventListener("change", () => {
    world.source?.send?.({ cmd: "scenario", name: select.value });
    sync(select.value);
  });
  sync(current);
}

function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}
