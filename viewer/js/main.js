/**
 * 뷰어 부트스트랩.
 *
 * 현재 단계(도면 미리보기)에서는 `demo.js` 가 만든 예시 데이터를 그린다.
 * 4단계에서 이 자리에 실제 시뮬레이션 프레임 스트림이 들어온다. 프레임 포맷은
 * 라이브(WebSocket)와 녹화본(trace.jsonl)이 동일하므로 뷰어 코드는 한 벌만 둔다
 * (docs/DECISIONS.md D-005).
 */

import { Stage, THREE } from "/js/scene.js";
import { buildFloor } from "/js/lot_floor.js";
import { GuidanceLine, TargetMarker } from "/js/guidance.js";
import { Palette } from "/js/palette.js";
import { VehicleModels, buildContactShadow, pickBodyColor } from "/js/vehicles.js";
import { buildDemoScenario } from "/js/demo.js";

const $ = (id) => document.getElementById(id);

/** 입구에서 안내를 받고 출발하기까지 정차하는 시간 (초). */
const GATE_PAUSE = 3.4;

export async function boot() {
  const lot = await fetch("/api/layout").then((r) => r.json());

  const stage = new Stage($("stage"));
  stage.fitTo(lot);
  stage.scene.add(buildFloor(THREE, lot));
  stage.addLampPosts(lampPositions(lot));

  const models = new VehicleModels(THREE);
  await models.load();

  const palette = new Palette();
  const world = new App(stage, lot, models, palette);
  await world.buildDemo();

  wireControls(stage, world);
  updateStats(lot, world);

  $("lot-name").textContent =
    `${lot.name} · ${lot.slots.length}면 · ${Math.round(lot.bounds[2] - lot.bounds[0])} × ` +
    `${Math.round(lot.bounds[3] - lot.bounds[1])} m`;

  connectStatus();
  $("loading").classList.add("gone");

  // 브라우저 콘솔에서 장면을 들여다볼 수 있게 해둔다 (디버깅용)
  globalThis.__sim = { stage, world, lot };

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
  constructor(stage, lot, models, palette) {
    this.stage = stage;
    this.lot = lot;
    this.models = models;
    this.palette = palette;
    this.cars = [];
    this.guided = [];
    this.glow = 1.0;

    this.bubbles = new BubbleLayer(stage, lot);
    this.graphGroup = buildGraphOverlay(THREE, lot);
    this.graphGroup.visible = false;
    stage.scene.add(this.graphGroup);
  }

  async buildDemo() {
    const scenario = buildDemoScenario(this.lot);
    const modelNames = this.models.names;

    // 이미 주차된 차량들
    for (const slot of scenario.parked) {
      const seed = hash(slot.id);
      const car = await this.models.create(pickModel(modelNames, seed), {
        length: 4.6, width: 1.85, color: pickBodyColor(seed),
      });
      placeInSlot(car, slot);
      car.add(buildContactShadow(THREE, 4.6, 1.85));
      this.stage.scene.add(car);
      this.cars.push(car);
    }

    // 유도선을 따라 이동 중인 차량들.
    // 차선(lane)을 서로 다르게 배정해 같은 통로에서도 나란히 놓이게 한다.
    for (const g of scenario.guided) {
      const style = this.palette.acquire(g.plate);
      const line = new GuidanceLine(THREE, g.polyline, style.color, {
        laneOffset: Palette.laneOffset(style.lane),
      });
      const marker = new TargetMarker(THREE, g.slot, style.color);
      this.stage.scene.add(line.mesh, marker.mesh);

      const car = await this.models.create(pickModel(modelNames, g.colorSeed), {
        length: 4.7, width: 1.85, color: pickBodyColor(g.colorSeed),
      });
      car.add(buildContactShadow(THREE, 4.7, 1.85));
      car.visible = false;
      this.stage.scene.add(car);

      this.guided.push({
        ...g, style, line, marker, car,
        travelled: 0, t: 0, phase: "waiting", bubble: null,
      });
    }

    renderLegend(this.guided);
  }

  setGlow(g) {
    this.glow = g;
    for (const v of this.guided) v.line.setGlow(g);
  }

  update(dt) {
    for (const v of this.guided) {
      v.t += dt;
      v.line.update(dt);
      v.marker.update(dt);

      if (v.phase === "waiting") {
        v.car.visible = false;
        if (v.t >= v.startDelay) this.enterGate(v);
        else continue;
      }

      if (v.phase === "gate") {
        // 입구에서 정차한 채 안내를 받는다
        v.car.visible = true;
        if (v.t >= v.startDelay + GATE_PAUSE) {
          this.bubbles.remove(v);
          v.phase = "driving";
        }
      } else if (v.phase === "driving") {
        v.travelled += v.speed * dt;
        if (v.travelled >= v.line.total) {
          // 미리보기용 루프 — 실제 시뮬레이션에서는 여기서 주차가 끝난다
          v.travelled = 0;
          v.t = 0;
          v.phase = "waiting";
          v.line.setProgress(0);
          v.car.visible = false;
          continue;
        }
      }

      const { position, heading } = v.line.sample(v.travelled);
      v.car.position.copy(position);
      v.car.position.y = 0;
      v.car.rotation.y = heading;
      v.line.setProgress(v.travelled / v.line.total);
    }

    this.bubbles.update();
  }

  enterGate(v) {
    v.phase = "gate";
    v.travelled = 0;
    v.line.setProgress(0);
    this.bubbles.show(v);
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

  show(v) {
    const css = Palette.css(v.style.color);
    const walk = this.gate
      ? Math.round(Math.hypot(v.slot.center[0] - this.gate[0], v.slot.center[1] - this.gate[1]))
      : 0;

    const el = document.createElement("div");
    el.className = "bubble";
    el.innerHTML =
      `<div class="tag">번호판 인식</div>` +
      `<div class="plate">${v.plate}</div>` +
      `<div class="msg">` +
      `<span class="swatch" style="background:${css}"></span>` +
      `<b style="color:${css}">${v.style.name}</b>색 선을 따라가 주시기 바랍니다` +
      `</div>` +
      `<div class="dest">배정 주차면 <b>${v.slot.id}</b> · 출입구까지 도보 <b>${walk}m</b></div>`;

    this.root.appendChild(el);
    v.bubble = el;
    this.active.push(v);
  }

  remove(v) {
    if (!v.bubble) return;
    const el = v.bubble;
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 360);
    v.bubble = null;
    this.active = this.active.filter((x) => x !== v);
  }

  /** 3D 앵커 위치를 화면 좌표로 투영해 말풍선을 붙인다. */
  update() {
    if (!this.active.length) return;

    const p = this.anchor.clone().project(this.stage.camera);
    const behind = p.z > 1;
    const x = (p.x * 0.5 + 0.5) * innerWidth;
    const y = (-p.y * 0.5 + 0.5) * innerHeight;

    // 여러 대가 동시에 들어오면 위로 쌓는다
    this.active.forEach((v, i) => {
      if (!v.bubble) return;
      v.bubble.style.display = behind ? "none" : "block";
      v.bubble.style.left = `${x}px`;
      v.bubble.style.top = `${y - i * 132}px`;
    });
  }
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

function pickModel(names, seed) {
  if (!names.length) return null;
  return names[Math.abs(Math.floor(seed * 7919)) % names.length];
}

function placeInSlot(car, slot) {
  // 후진 주차이므로 차량 앞머리는 통로를 향한다. 원점이 뒷축이라 살짝 밀어 넣는다.
  const back = 1.35;
  car.position.set(
    slot.center[0] - Math.cos(slot.heading) * back,
    0,
    -(slot.center[1] - Math.sin(slot.heading) * back)
  );
  car.rotation.y = slot.heading;
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

function renderLegend(guided) {
  const box = $("legend-items");
  box.innerHTML = "";
  for (const v of guided) {
    const css = Palette.css(v.style.color);
    const el = document.createElement("div");
    el.className = "item";
    el.innerHTML =
      `<span class="chip" style="background:${css}"></span>` +
      `<span>${v.plate}</span>` +
      `<span class="slot">${v.style.name}색 → ${v.slot.id}</span>`;
    box.appendChild(el);
  }
}

function updateStats(lot, world) {
  const occupied = world.cars.length;
  $("s-slots").textContent = lot.slots.length;
  $("s-occ").innerHTML =
    `${Math.round((occupied / lot.slots.length) * 100)}<small>%</small>`;
  $("s-guided").textContent = world.guided.length;
  $("s-reroute").textContent = "0";
}

function wireControls(stage, world) {
  const seg = (id, initial, onPick) => {
    const box = $(id);
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

  glowSlider.addEventListener("input", () => {
    const g = Number(glowSlider.value) / 100;
    $("v-glow").textContent = g.toFixed(2);
    world.setGlow(g);
  });

  $("c-graph").addEventListener("change", (e) => {
    world.graphGroup.visible = e.target.checked;
  });
}

function connectStatus() {
  const el = $("conn");
  const text = $("conn-text");
  try {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "hello") {
        el.classList.add("on");
        text.textContent = msg.live ? "라이브 스트리밍" : "서버 연결됨 · 시뮬레이션 대기";
      }
    };
    ws.onclose = () => {
      el.classList.remove("on");
      text.textContent = "서버 연결 끊김";
    };
    ws.onerror = () => {
      el.classList.remove("on");
      text.textContent = "서버에 연결할 수 없음";
    };
  } catch {
    text.textContent = "서버 없음 (정적 미리보기)";
  }
}

function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}
