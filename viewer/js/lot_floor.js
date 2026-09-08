/**
 * 주차장 바닥 — 절차적으로 굽는 아스팔트 텍스처.
 *
 * 이 프로젝트 산출물의 절반은 발표 화면이다. 유도선이 아무리 잘 나와도 바닥이
 * 밋밋한 회색 평면이면 "실제 주차장"으로 읽히지 않는다 (docs/DECISIONS.md D-011).
 *
 * 텍스처를 이미지 파일로 받아오는 대신 캔버스에 그리는 이유:
 *   - 저장소에 대용량 이미지를 넣지 않는다
 *   - 도면 규모를 바꿔도 자동으로 따라온다
 *
 * 그리는 순서 (아래에서 위로 쌓인다):
 *   1) 아스팔트 바탕 — 얼룩 + 자갈 그레인
 *   2) 포장 이음새
 *   3) 타이어 자국 · 기름 얼룩
 *   4) 노면 도색 — 주차선, 화살표, 구역 문자 (별도 레이어에 그린 뒤 마모시켜 합성)
 */

const PX_PER_M = 34;      // 텍스처 해상도
const MAX_PX = 4096;      // GPU 텍스처 한계 여유

const PAINT = "#d8d4c8";  // 마모된 오프화이트 — 순백은 쓰지 않는다
const PAINT_BLUE = "#2f6fb5";
const PAINT_GREEN = "#2f8f5b";

/** 결정론적 난수 — 새로고침해도 같은 얼룩이 나오게 한다. */
function makeRng(seed = 20260908) {
  let s = seed >>> 0;
  return () => {
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5;  s >>>= 0;
    return s / 4294967296;
  };
}

/** 값 노이즈를 작은 캔버스에 그린다. 확대해서 쓰면 부드러운 얼룩이 된다. */
function noiseCanvas(w, h, rng, lo = 0, hi = 255) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const ctx = c.getContext("2d");
  const img = ctx.createImageData(w, h);
  for (let i = 0; i < w * h; i++) {
    const v = lo + rng() * (hi - lo);
    img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = v;
    img.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

/** 자갈 그레인 타일. 전체 화면에 per-pixel 노이즈를 돌리면 느리므로 타일을 반복한다. */
function grainTile(rng, size = 256) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const img = ctx.createImageData(size, size);
  for (let i = 0; i < size * size; i++) {
    const v = rng();
    const shade = v < 0.5 ? 0 : 255;
    img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = shade;
    img.data[i * 4 + 3] = Math.floor(Math.abs(v - 0.5) * 54);
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

/**
 * 도면 좌표(미터) → 텍스처 픽셀.
 * 주차장 좌표는 +y 가 북쪽이고, 캔버스는 y 가 아래로 증가하므로 뒤집는다.
 */
function makeProjector(bounds, canvas, pad) {
  const [minX, minY, maxX, maxY] = bounds;
  const w = maxX - minX + pad * 2;
  const h = maxY - minY + pad * 2;
  const sx = canvas.width / w;
  const sy = canvas.height / h;
  return {
    x: (mx) => (mx - minX + pad) * sx,
    y: (my) => (maxY + pad - my) * sy,
    s: (m) => m * sx,
    width: w,
    height: h,
  };
}

export function buildFloorTexture(lot, THREE) {
  const pad = 4;
  const [minX, minY, maxX, maxY] = lot.bounds;
  const wM = maxX - minX + pad * 2;
  const hM = maxY - minY + pad * 2;

  const scale = Math.min(PX_PER_M, MAX_PX / Math.max(wM, hM));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(wM * scale);
  canvas.height = Math.round(hM * scale);

  const ctx = canvas.getContext("2d");
  const rng = makeRng();
  const P = makeProjector(lot.bounds, canvas, pad);

  paintAsphalt(ctx, canvas, rng);
  paintJoints(ctx, P, lot, rng);
  paintGrime(ctx, P, lot, rng);
  paintMarkings(ctx, canvas, P, lot, rng);

  const tex = new THREE.CanvasTexture(canvas);
  tex.anisotropy = 16;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.needsUpdate = true;
  return { texture: tex, widthM: wM, heightM: hM, centerX: (minX + maxX) / 2, centerY: (minY + maxY) / 2 };
}

// ── 1) 아스팔트 바탕 ──────────────────────────────────────────────────

function paintAsphalt(ctx, canvas, rng) {
  const { width: W, height: H } = canvas;

  ctx.fillStyle = "#4a4e55";
  ctx.fillRect(0, 0, W, H);

  // 큰 얼룩 — 포장 시공 이음새와 보수 자국처럼 보이는 넓은 명도 변화
  ctx.save();
  ctx.globalCompositeOperation = "overlay";
  ctx.globalAlpha = 0.5;
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(noiseCanvas(24, 18, rng, 70, 190), 0, 0, W, H);
  ctx.globalAlpha = 0.32;
  ctx.drawImage(noiseCanvas(96, 72, rng, 90, 175), 0, 0, W, H);
  ctx.restore();

  // 자갈 그레인
  ctx.save();
  const pat = ctx.createPattern(grainTile(rng), "repeat");
  ctx.fillStyle = pat;
  ctx.globalAlpha = 0.85;
  ctx.fillRect(0, 0, W, H);
  ctx.restore();

  // 가장자리 비네팅 — 포장 끝이 자연스럽게 어두워진다
  const g = ctx.createRadialGradient(
    W / 2, H / 2, Math.min(W, H) * 0.32,
    W / 2, H / 2, Math.max(W, H) * 0.72
  );
  g.addColorStop(0, "rgba(0,0,0,0)");
  g.addColorStop(1, "rgba(0,0,0,0.20)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
}

// ── 2) 포장 이음새 ────────────────────────────────────────────────────

function paintJoints(ctx, P, lot, rng) {
  const [minX, minY, maxX, maxY] = lot.bounds;
  ctx.save();
  ctx.strokeStyle = "rgba(0,0,0,0.22)";
  ctx.lineWidth = Math.max(1, P.s(0.05));

  for (let x = minX; x <= maxX + 0.01; x += 6) {
    ctx.beginPath();
    ctx.moveTo(P.x(x) + (rng() - 0.5) * 2, P.y(maxY));
    ctx.lineTo(P.x(x) + (rng() - 0.5) * 2, P.y(minY));
    ctx.stroke();
  }
  for (let y = minY; y <= maxY + 0.01; y += 6) {
    ctx.beginPath();
    ctx.moveTo(P.x(minX), P.y(y) + (rng() - 0.5) * 2);
    ctx.lineTo(P.x(maxX), P.y(y) + (rng() - 0.5) * 2);
    ctx.stroke();
  }
  ctx.restore();
}

// ── 3) 타이어 자국과 기름 얼룩 ────────────────────────────────────────

function paintGrime(ctx, P, lot, rng) {
  ctx.save();

  // 통로 중앙을 따라 차량 궤적이 남는다 — 양쪽 바퀴 자국 두 줄
  for (const a of lot.aisles) {
    const track = 0.85;
    for (const off of [-track, track]) {
      ctx.beginPath();
      if (a.axis === "h") {
        ctx.moveTo(P.x(a.start[0]), P.y(a.start[1] + off));
        ctx.lineTo(P.x(a.end[0]), P.y(a.end[1] + off));
      } else {
        ctx.moveTo(P.x(a.start[0] + off), P.y(a.start[1]));
        ctx.lineTo(P.x(a.end[0] + off), P.y(a.end[1]));
      }
      ctx.strokeStyle = "rgba(18,18,20,0.16)";
      ctx.lineWidth = P.s(0.55);
      ctx.lineCap = "round";
      ctx.stroke();
    }
  }

  // 주차면마다 낮은 확률로 기름 얼룩
  for (const s of lot.slots) {
    if (rng() > 0.28) continue;
    const cx = P.x(s.center[0] + (rng() - 0.5) * 0.8);
    const cy = P.y(s.center[1] + (rng() - 0.5) * 1.4);
    const r = P.s(0.25 + rng() * 0.4);
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    g.addColorStop(0, "rgba(10,10,12,0.4)");
    g.addColorStop(1, "rgba(10,10,12,0)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
}

// ── 4) 노면 도색 ──────────────────────────────────────────────────────

function paintMarkings(ctx, canvas, P, lot, rng) {
  // 도색은 별도 레이어에 그린다. 그래야 마모(부분 지우기)를 아스팔트를 건드리지 않고
  // 적용할 수 있다.
  const layer = document.createElement("canvas");
  layer.width = canvas.width;
  layer.height = canvas.height;
  const g = layer.getContext("2d");

  paintSlots(g, P, lot, rng);
  paintAisleArrows(g, P, lot);
  paintCrosswalk(g, P, lot);
  paintGateMarkings(g, P, lot);
  paintZoneLetters(g, P, lot);
  paintIslands(g, P, lot);
  applyWear(g, layer, rng);

  ctx.save();
  ctx.globalAlpha = 0.93;
  ctx.drawImage(layer, 0, 0);
  ctx.restore();
}

function paintSlots(g, P, lot, rng) {
  const lw = P.s(0.12);

  for (const s of lot.slots) {
    const [cx, cy] = s.center;
    const half = s.length / 2;
    const halfW = s.width / 2;
    // heading 은 주차 후 차량 앞머리 방향 = 통로 쪽. 그 반대가 주차면 안쪽이다.
    const openNorth = Math.sin(s.heading) > 0;
    const yOpen = openNorth ? cy + half : cy - half;
    const yBack = openNorth ? cy - half : cy + half;

    // 특수 주차면은 바닥 전체를 도색한다
    if (s.type === "disabled" || s.type === "ev") {
      g.save();
      g.globalAlpha = 0.5;
      g.fillStyle = s.type === "disabled" ? PAINT_BLUE : PAINT_GREEN;
      g.fillRect(P.x(cx - halfW), P.y(Math.max(yOpen, yBack)), P.s(s.width), P.s(s.length));
      g.restore();
    }

    g.strokeStyle = PAINT;
    g.lineWidth = lw;
    g.lineCap = "butt";
    g.beginPath();
    // 양 옆 실선 + 안쪽 마감선 (통로 쪽은 열어둔다)
    g.moveTo(P.x(cx - halfW), P.y(yOpen));
    g.lineTo(P.x(cx - halfW), P.y(yBack));
    g.lineTo(P.x(cx + halfW), P.y(yBack));
    g.lineTo(P.x(cx + halfW), P.y(yOpen));
    g.stroke();

    // 주차면 번호 — 안쪽 마감선 근처에 작게
    g.save();
    g.fillStyle = PAINT;
    g.globalAlpha = 0.62;
    g.font = `600 ${P.s(0.55)}px system-ui, sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    const ty = yBack + (openNorth ? 0.62 : -0.62);
    g.translate(P.x(cx), P.y(ty));
    if (!openNorth) g.rotate(Math.PI);
    g.fillText(s.id, 0, 0);
    g.restore();

    if (s.type === "disabled") drawWheelchair(g, P, cx, cy, openNorth);
    if (s.type === "ev") drawBolt(g, P, cx, cy, openNorth);
  }
}

function drawWheelchair(g, P, cx, cy, openNorth) {
  g.save();
  g.translate(P.x(cx), P.y(cy));
  if (!openNorth) g.rotate(Math.PI);
  g.fillStyle = PAINT;
  const u = P.s(1);
  // 머리
  g.beginPath(); g.arc(0, -0.62 * u, 0.19 * u, 0, Math.PI * 2); g.fill();
  // 바퀴
  g.lineWidth = 0.11 * u;
  g.strokeStyle = PAINT;
  g.beginPath(); g.arc(0.03 * u, 0.34 * u, 0.5 * u, 0, Math.PI * 2); g.stroke();
  // 몸통과 다리
  g.lineWidth = 0.17 * u;
  g.lineCap = "round";
  g.beginPath();
  g.moveTo(-0.12 * u, -0.32 * u);
  g.lineTo(-0.02 * u, 0.16 * u);
  g.lineTo(0.42 * u, 0.2 * u);
  g.moveTo(-0.02 * u, 0.16 * u);
  g.lineTo(0.02 * u, 0.62 * u);
  g.lineTo(0.5 * u, 0.66 * u);
  g.stroke();
  g.restore();
}

function drawBolt(g, P, cx, cy, openNorth) {
  g.save();
  g.translate(P.x(cx), P.y(cy));
  if (!openNorth) g.rotate(Math.PI);
  const u = P.s(1);
  g.fillStyle = PAINT;
  g.beginPath();
  g.moveTo(0.16 * u, -0.85 * u);
  g.lineTo(-0.34 * u, 0.1 * u);
  g.lineTo(-0.02 * u, 0.1 * u);
  g.lineTo(-0.16 * u, 0.85 * u);
  g.lineTo(0.34 * u, -0.12 * u);
  g.lineTo(0.02 * u, -0.12 * u);
  g.closePath();
  g.fill();
  g.restore();
}

function paintAisleArrows(g, P, lot) {
  for (const a of lot.aisles) {
    if (!a.one_way || a.heading === null) continue;

    const [x0, y0] = a.start;
    const [x1, y1] = a.end;
    const len = Math.hypot(x1 - x0, y1 - y0);
    if (len < 6) continue;

    const step = 13;
    const n = Math.max(1, Math.floor(len / step));
    for (let i = 0; i < n; i++) {
      const t = (i + 0.5) / n;
      const mx = x0 + (x1 - x0) * t;
      const my = y0 + (y1 - y0) * t;
      drawArrow(g, P, mx, my, a.heading);
    }
  }
}

/** 진행 방향 화살표. 캔버스는 y 가 아래로 증가하므로 각도 부호를 뒤집는다. */
function drawArrow(g, P, mx, my, heading) {
  g.save();
  g.translate(P.x(mx), P.y(my));
  g.rotate(-heading);
  g.fillStyle = PAINT;
  g.globalAlpha = 0.82;
  const u = P.s(1);
  // 자루
  g.fillRect(-1.5 * u, -0.16 * u, 2.0 * u, 0.32 * u);
  // 촉
  g.beginPath();
  g.moveTo(1.5 * u, 0);
  g.lineTo(0.35 * u, -0.62 * u);
  g.lineTo(0.35 * u, 0.62 * u);
  g.closePath();
  g.fill();
  g.restore();
}

/** 건물 출입구로 이어지는 횡단보도. 인기 자리가 왜 인기인지 시각적으로 설명해준다. */
function paintCrosswalk(g, P, lot) {
  const gate = lot.pedestrian_gates[0];
  if (!gate) return;
  const [gx, gy] = gate;
  const top = lot.bounds[3];

  g.save();
  g.fillStyle = PAINT;
  g.globalAlpha = 0.85;
  const stripeW = 0.45, gapW = 0.4;
  const span = 4.0;
  for (let x = gx - span; x < gx + span; x += stripeW + gapW) {
    g.fillRect(P.x(x), P.y(gy - 0.6), P.s(stripeW), P.s(gy - top - 1.2));
  }
  g.restore();
}

/** 입구/출구 정지선과 문자 도색. */
function paintGateMarkings(g, P, lot) {
  const label = (nodeId, text) => {
    const node = lot.nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const [x, y] = node.pos;

    g.save();
    g.fillStyle = PAINT;
    // 정지선
    g.globalAlpha = 0.9;
    g.fillRect(P.x(x - 2.6), P.y(y + 1.4), P.s(5.2), P.s(0.4));
    // 문자
    g.globalAlpha = 0.72;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.font = `700 ${P.s(1.5)}px system-ui, sans-serif`;
    g.translate(P.x(x), P.y(y - 1.2));
    g.fillText(text, 0, 0);
    g.restore();
  };
  label(lot.entry_nodes[0], "IN");
  label(lot.exit_nodes[0], "OUT");
}

function paintZoneLetters(g, P, lot) {
  const rows = new Map();
  for (const s of lot.slots) {
    const r = rows.get(s.row) ?? { minX: Infinity, maxX: -Infinity, y: s.center[1] };
    r.minX = Math.min(r.minX, s.center[0]);
    r.maxX = Math.max(r.maxX, s.center[0]);
    rows.set(s.row, r);
  }

  g.save();
  g.fillStyle = PAINT;
  g.globalAlpha = 0.5;
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.font = `700 ${P.s(3.0)}px system-ui, sans-serif`;
  for (const [label, r] of rows) {
    g.fillText(label, P.x(r.minX - 5.0), P.y(r.y));
    g.fillText(label, P.x(r.maxX + 5.0), P.y(r.y));
  }
  g.restore();
}

function paintIslands(g, P, lot) {
  for (const is of lot.islands) {
    const [cx, cy] = is.center;
    const x = P.x(cx - is.width / 2);
    const y = P.y(cy + is.length / 2);
    const w = P.s(is.width);
    const h = P.s(is.length);

    g.save();
    g.fillStyle = "#6e7379";              // 연석
    g.fillRect(x, y, w, h);
    g.fillStyle = "#39482f";              // 식재 (채도를 낮춰 주차면과 헷갈리지 않게)
    g.fillRect(x + P.s(0.2), y + P.s(0.2), w - P.s(0.4), h - P.s(0.4));
    g.restore();
  }
}

/** 도색 레이어를 부분적으로 지워 마모를 만든다. */
function applyWear(g, layer, rng) {
  g.save();
  g.globalCompositeOperation = "destination-out";
  const n = Math.floor((layer.width * layer.height) / 9000);
  for (let i = 0; i < n; i++) {
    const x = rng() * layer.width;
    const y = rng() * layer.height;
    const r = 1 + rng() * 7;
    g.globalAlpha = 0.1 + rng() * 0.45;
    g.beginPath();
    g.arc(x, y, r, 0, Math.PI * 2);
    g.fill();
  }
  g.restore();
}

// ── 바닥 메시 구성 ────────────────────────────────────────────────────

/**
 * 아스팔트 평면 + 주변 지면 + 연석 입체 + 건물 출입구 표식을 만든다.
 * @returns {THREE.Group}
 */
export function buildFloor(THREE, lot) {
  const group = new THREE.Group();
  group.name = "floor";

  const baked = buildFloorTexture(lot, THREE);

  // 주변 지면 — 이게 없으면 주차장이 허공에 떠 보인다
  const surround = new THREE.Mesh(
    new THREE.PlaneGeometry(baked.widthM * 4, baked.heightM * 4),
    new THREE.MeshStandardMaterial({ color: 0x1a1f1c, roughness: 1.0 })
  );
  surround.rotation.x = -Math.PI / 2;
  surround.position.set(baked.centerX, -0.06, -baked.centerY);
  surround.receiveShadow = true;
  group.add(surround);

  // 아스팔트
  const asphalt = new THREE.Mesh(
    new THREE.PlaneGeometry(baked.widthM, baked.heightM),
    new THREE.MeshStandardMaterial({
      map: baked.texture,
      roughness: 0.94,
      metalness: 0.0,
    })
  );
  asphalt.rotation.x = -Math.PI / 2;
  asphalt.position.set(baked.centerX, 0, -baked.centerY);
  asphalt.receiveShadow = true;
  group.add(asphalt);

  // 연석은 살짝 돌출시켜 그림자를 받게 한다
  const curbMat = new THREE.MeshStandardMaterial({ color: 0x7a7f85, roughness: 0.85 });
  const plantMat = new THREE.MeshStandardMaterial({ color: 0x39482f, roughness: 1.0 });
  for (const is of lot.islands) {
    const curb = new THREE.Mesh(
      new THREE.BoxGeometry(is.width, 0.16, is.length), curbMat
    );
    curb.position.set(is.center[0], 0.08, -is.center[1]);
    curb.castShadow = true;
    curb.receiveShadow = true;
    group.add(curb);

    const plant = new THREE.Mesh(
      new THREE.BoxGeometry(is.width - 0.36, 0.26, is.length - 0.36), plantMat
    );
    plant.position.set(is.center[0], 0.13, -is.center[1]);
    plant.castShadow = true;
    group.add(plant);
  }

  group.add(buildGate(THREE, lot));
  group.add(buildBarriers(THREE, lot));
  return group;
}

/**
 * 입출구 차단기와 번호판 인식(ANPR) 카메라.
 *
 * 순수하게 장식이 아니다. 이 시스템의 출발점이 "입구에서 번호판을 읽는다" 이므로,
 * 화면에 카메라 기둥이 서 있어야 개념이 한눈에 읽힌다.
 */
function buildBarriers(THREE, lot) {
  const g = new THREE.Group();
  const postMat = new THREE.MeshStandardMaterial({ color: 0x2c3138, roughness: 0.6, metalness: 0.4 });
  const armMat = new THREE.MeshStandardMaterial({ color: 0xe8e2d8, roughness: 0.55 });
  const bandMat = new THREE.MeshStandardMaterial({ color: 0xc0392b, roughness: 0.6 });
  const camMat = new THREE.MeshStandardMaterial({ color: 0x1c2026, roughness: 0.45, metalness: 0.5 });

  const nodeById = new Map(lot.nodes.map((n) => [n.id, n]));

  const build = (nodeId, withCamera) => {
    const node = nodeById.get(nodeId);
    if (!node) return;
    const [x, y] = node.pos;
    const b = new THREE.Group();

    const cabinet = new THREE.Mesh(new THREE.BoxGeometry(0.42, 1.1, 0.42), postMat);
    cabinet.position.set(-3.1, 0.55, 0);
    cabinet.castShadow = true;
    b.add(cabinet);

    // 차단봉 — 열린 상태로 비스듬히 세워둔다
    const arm = new THREE.Group();
    const bar = new THREE.Mesh(new THREE.BoxGeometry(3.4, 0.12, 0.12), armMat);
    bar.position.x = 1.7;
    bar.castShadow = true;
    arm.add(bar);
    for (const dx of [0.7, 1.7, 2.7]) {
      const band = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.13, 0.13), bandMat);
      band.position.x = dx;
      arm.add(band);
    }
    arm.position.set(-3.1, 1.05, 0);
    arm.rotation.z = 0.95;
    b.add(arm);

    if (withCamera) {
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.11, 4.2, 8), postMat);
      pole.position.set(3.1, 2.1, 0);
      pole.castShadow = true;
      b.add(pole);

      const boom = new THREE.Mesh(new THREE.BoxGeometry(2.6, 0.12, 0.12), postMat);
      boom.position.set(1.9, 4.15, 0);
      b.add(boom);

      const cam = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.3, 0.28), camMat);
      cam.position.set(0.75, 3.95, 0);
      cam.rotation.z = -0.35;
      cam.castShadow = true;
      b.add(cam);

      // 인식 순간을 알리는 표시등
      const led = new THREE.Mesh(
        new THREE.BoxGeometry(0.1, 0.16, 0.2),
        new THREE.MeshBasicMaterial({ color: 0x63ffa5 })
      );
      led.position.set(0.52, 3.9, 0);
      b.add(led);
    }

    b.position.set(x, 0, -y);
    g.add(b);
  };

  build(lot.entry_nodes[0], true);
  build(lot.exit_nodes[0], false);
  return g;
}

/** 건물 출입구 — 인기 자리가 왜 인기인지 한눈에 보이게 한다. */
function buildGate(THREE, lot) {
  const g = new THREE.Group();
  if (!lot.pedestrian_gates.length) return g;

  for (const [gx, gy] of lot.pedestrian_gates) {
    const canopy = new THREE.Mesh(
      new THREE.BoxGeometry(14, 0.4, 3),
      new THREE.MeshStandardMaterial({ color: 0x2a3038, roughness: 0.7 })
    );
    canopy.position.set(gx, 3.6, -gy);
    canopy.castShadow = true;
    g.add(canopy);

    const wall = new THREE.Mesh(
      new THREE.BoxGeometry(20, 7, 1.2),
      new THREE.MeshStandardMaterial({ color: 0x232830, roughness: 0.9 })
    );
    wall.position.set(gx, 3.5, -(gy + 2.2));
    wall.castShadow = true;
    wall.receiveShadow = true;
    g.add(wall);

    // 출입구 불빛 — 야간 프리셋에서 존재감이 생긴다
    const glow = new THREE.Mesh(
      new THREE.BoxGeometry(9, 2.6, 0.15),
      new THREE.MeshBasicMaterial({ color: 0xffd9a0 })
    );
    glow.position.set(gx, 2.0, -(gy + 1.55));
    g.add(glow);

    const light = new THREE.PointLight(0xffd9a0, 40, 40, 2);
    light.position.set(gx, 3.2, -(gy - 1));
    g.add(light);
  }
  return g;
}
