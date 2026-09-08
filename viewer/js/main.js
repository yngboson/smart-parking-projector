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

    this.slotById = new Map(lot.slots.map((s) => [s.id, s]));
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

    // 유도선을 따라 이동 중인 차량들
    for (const g of scenario.guided) {
      const color = this.palette.acquire(g.plate);
      const line = new GuidanceLine(THREE, g.polyline, color);
      const marker = new TargetMarker(THREE, g.slot, color);
      this.stage.scene.add(line.mesh, marker.mesh);

      const car = await this.models.create(pickModel(modelNames, g.colorSeed), {
        length: 4.7, width: 1.85, color: pickBodyColor(g.colorSeed),
      });
      car.add(buildContactShadow(THREE, 4.7, 1.85));
      this.stage.scene.add(car);

      this.guided.push({ ...g, color, line, marker, car, travelled: 0, t: 0 });
    }

    renderLegend(this.guided, this.palette);
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

      if (v.t < v.startDelay) {
        v.car.visible = false;
        continue;
      }
      v.car.visible = true;
      v.travelled += v.speed * dt;

      // 끝까지 가면 처음으로 되감는다 (미리보기용 루프)
      if (v.travelled > v.line.total) {
        v.travelled = 0;
        v.t = 0;
      }

      const { position, heading } = v.line.sample(v.travelled);
      v.car.position.copy(position);
      v.car.position.y = 0;
      v.car.rotation.y = heading;
      v.line.setProgress(v.travelled / v.line.total);
    }
  }
}

/** 가로등 자리 — 조경섬 위에 세운다. 실제 주차장도 섬에 등을 박는다. */
function lampPositions(lot) {
  const byRow = new Map();
  for (const is of lot.islands) {
    const key = is.id.slice(-1);          // W 또는 E
    if (!byRow.has(key)) byRow.set(key, []);
    byRow.get(key).push(is);
  }
  const out = [];
  for (const list of byRow.values()) {
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

function renderLegend(guided, palette) {
  const box = $("legend-items");
  box.innerHTML = "";
  for (const v of guided) {
    const css = Palette.css(v.color);
    const el = document.createElement("div");
    el.className = "item";
    el.innerHTML =
      `<span class="chip" style="background:${css};color:${css}"></span>` +
      `<span>${v.plate}</span><span class="slot">→ ${v.slot.id}</span>`;
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
