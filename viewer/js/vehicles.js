/**
 * 차량 3D 표현.
 *
 * STL 모델을 나중에 받아 교체할 수 있게 만든다 (docs/DECISIONS.md D-007).
 * 파일의 단위·축 방향·원점 위치를 지금은 알 수 없으므로, 그 값들을 코드가 아니라
 * `viewer/models/models.json` 설정으로 흡수한다.
 *
 *   autoFitLength    바운딩 박스를 재서 실제 전장(m)에 맞춘다 — 단위를 몰라도 된다
 *   upAxis           STL 이 Z-up 인지 Y-up 인지
 *   headingOffsetDeg 모델이 바라보는 방향 보정
 *   pivot            메시 중심 → 뒷축 중심으로 원점 이동
 *
 * STL 이 없으면 저폴리 박스 차량으로 폴백한다. 모델을 기다리는 동안 개발이 막히면
 * 안 되기 때문이다.
 */

import { STLLoader } from "three/addons/STLLoader.js";

const DEFAULTS = {
  autoFitLength: 4.7,
  upAxis: "Y",
  headingOffsetDeg: 0,
  pivot: "center",
  scaleOverride: null,
  offset: [0, 0, 0],
};

/** 차체 도색 — 실제 주차장의 차량 색 분포에 가깝게 무채색 위주. */
const BODY_COLORS = [
  0xeceef1, 0xdcdee2, 0xc7cace, 0xa3a9b1, 0x767c85, 0x4a4f57,
  0x2b2f35, 0x1b1e22, 0x8d99ae, 0x3b4d73, 0x8c3540, 0x2a5c4c,
  0x2b4a7d, 0xc06a24, 0x5d4b7a, 0xa8b0a2,
];

export function pickBodyColor(seed) {
  return BODY_COLORS[Math.abs(Math.floor(seed * 9973)) % BODY_COLORS.length];
}

/**
 * 저폴리 박스 차량. 형태만으로도 승용차로 읽히도록 차체 + 캐빈 + 바퀴로 구성한다.
 * @returns {THREE.Group} 원점은 뒷축 중심, +x 가 진행 방향.
 */
export function buildBoxCar(THREE, { length = 4.7, width = 1.85, color = 0xd7d9dd } = {}) {
  const g = new THREE.Group();
  const h = 0.72;              // 차체 높이
  const clear = 0.26;          // 최저 지상고
  const wheelR = 0.33;

  const bodyMat = new THREE.MeshStandardMaterial({
    color, roughness: 0.42, metalness: 0.35,
  });
  const glassMat = new THREE.MeshStandardMaterial({
    color: 0x141a20, roughness: 0.12, metalness: 0.6,
  });
  const tireMat = new THREE.MeshStandardMaterial({ color: 0x14161a, roughness: 0.95 });

  const body = new THREE.Mesh(new THREE.BoxGeometry(length, h, width), bodyMat);
  body.position.set(0, clear + h / 2, 0);
  g.add(body);

  const cabin = new THREE.Mesh(
    new THREE.BoxGeometry(length * 0.46, 0.52, width * 0.86), glassMat
  );
  cabin.position.set(-length * 0.04, clear + h + 0.24, 0);
  g.add(cabin);

  // 앞뒤 등화 — 방향이 한눈에 보인다
  const head = new THREE.Mesh(
    new THREE.BoxGeometry(0.08, 0.16, width * 0.72),
    new THREE.MeshBasicMaterial({ color: 0xfff3d0 })
  );
  head.position.set(length / 2, clear + h * 0.62, 0);
  g.add(head);

  const tail = new THREE.Mesh(
    new THREE.BoxGeometry(0.08, 0.14, width * 0.74),
    new THREE.MeshBasicMaterial({ color: 0x8e1f22 })
  );
  tail.position.set(-length / 2, clear + h * 0.62, 0);
  g.add(tail);

  const wheelGeo = new THREE.CylinderGeometry(wheelR, wheelR, 0.22, 14);
  for (const dx of [length * 0.31, -length * 0.31]) {
    for (const dz of [width / 2 - 0.06, -(width / 2 - 0.06)]) {
      const w = new THREE.Mesh(wheelGeo, tireMat);
      w.rotation.x = Math.PI / 2;
      w.position.set(dx, wheelR, dz);
      g.add(w);
    }
  }

  for (const m of g.children) { m.castShadow = true; m.receiveShadow = true; }

  // 원점을 뒷축 중심으로 — 자전거 모델의 기준점과 맞춘다
  g.children.forEach((c) => { c.position.x -= -length * 0.31; });
  return g;
}

/** 차량이 바닥에 붙어 보이게 하는 접지 그림자. 이게 없으면 차가 떠 보인다. */
export function buildContactShadow(THREE, length, width) {
  const size = 128;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const grad = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  grad.addColorStop(0, "rgba(0,0,0,0.55)");
  grad.addColorStop(0.55, "rgba(0,0,0,0.28)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);

  const tex = new THREE.CanvasTexture(c);
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(length * 1.25, width * 1.75),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false })
  );
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = 0.015;
  mesh.renderOrder = 2;
  return mesh;
}

/**
 * STL 모델 저장소. 설정에 맞춰 스케일·축·피벗을 보정해서 캐시한다.
 * 파일이 없거나 로드에 실패하면 박스 차량으로 조용히 폴백한다.
 */
export class VehicleModels {
  constructor(THREE) {
    this.THREE = THREE;
    this.loader = new STLLoader();
    this.configs = {};
    this.cache = new Map();
    this.failed = new Set();
  }

  async load(url = "/api/models") {
    try {
      const res = await fetch(url);
      const data = await res.json();
      this.configs = data.models ?? {};
    } catch {
      this.configs = {};
    }
  }

  /** 등록된 STL 모델 이름들. 비어 있으면 전부 박스 폴백이다. */
  get names() {
    return Object.keys(this.configs);
  }

  async geometry(name) {
    if (this.cache.has(name)) return this.cache.get(name);
    if (this.failed.has(name)) return null;

    const cfg = { ...DEFAULTS, ...(this.configs[name] ?? {}) };
    if (!cfg.file) { this.failed.add(name); return null; }

    try {
      const geo = await this.loader.loadAsync(`/models/${cfg.file}`);
      applyModelTransform(this.THREE, geo, cfg);
      this.cache.set(name, geo);
      return geo;
    } catch {
      // STL 이 아직 없는 것은 정상이다. 조용히 박스로 간다.
      this.failed.add(name);
      return null;
    }
  }

  /**
   * 차량 하나를 만든다.
   * @returns {THREE.Group} 원점 = 뒷축 중심, +x = 진행 방향
   */
  async create(name, { length, width, color }) {
    const THREE = this.THREE;
    const geo = name ? await this.geometry(name) : null;
    if (!geo) return buildBoxCar(THREE, { length, width, color });

    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshStandardMaterial({ color, roughness: 0.42, metalness: 0.35 })
    );
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    const g = new THREE.Group();
    g.add(mesh);
    return g;
  }
}

/**
 * STL 의 단위·축·원점을 실제 차량 치수에 맞춘다.
 * 바운딩 박스를 재서 맞추므로 파일이 mm 단위든 inch 단위든 상관없다.
 */
export function applyModelTransform(THREE, geo, cfg) {
  geo.computeBoundingBox();

  // 1) 축 정렬 — 대부분의 CAD 출력 STL 은 Z-up 이다
  if (cfg.upAxis === "Z") {
    geo.rotateX(-Math.PI / 2);
    geo.computeBoundingBox();
  }

  // 2) 진행 방향 보정
  if (cfg.headingOffsetDeg) {
    geo.rotateY((cfg.headingOffsetDeg * Math.PI) / 180);
    geo.computeBoundingBox();
  }

  // 3) 스케일 — 전장을 실제 치수에 맞춘다
  const size = new THREE.Vector3();
  geo.boundingBox.getSize(size);
  const scale = cfg.scaleOverride ?? (size.x > 0 ? cfg.autoFitLength / size.x : 1);
  geo.scale(scale, scale, scale);
  geo.computeBoundingBox();

  // 4) 원점 — 바닥에 닿게 내리고, 필요하면 뒷축 중심으로 옮긴다
  const box = geo.boundingBox;
  const center = new THREE.Vector3();
  box.getCenter(center);
  const len = box.max.x - box.min.x;

  const dx = cfg.pivot === "rear_axle" ? -(box.min.x + len * 0.19) : -center.x;
  geo.translate(dx, -box.min.y, -center.z);
  geo.translate(cfg.offset[0], cfg.offset[1], cfg.offset[2]);
  geo.computeVertexNormals();
  geo.computeBoundingBox();
}
