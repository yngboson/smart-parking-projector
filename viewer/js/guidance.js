/**
 * 유도선 — 경로 중앙의 굵은 리본 + 진행 방향으로 흐르는 쉐브론.
 *
 * 설계 근거는 docs/DECISIONS.md D-006.
 *   - 차선처럼 양쪽에 두 줄을 그리지 않는다. 여러 대가 겹치면 구분이 안 된다.
 *   - 한국 고속도로 색상 안내선처럼 중앙에 한 줄, 진행 방향 쉐브론을 흘린다.
 *   - **지나온 구간은 지운다.** 바닥에 남는 선의 총량이 줄어 혼란이 감소한다.
 *
 * 가산 혼합(additive)으로 그리는 이유: 빔 프로젝터는 빛을 더하는 장치이지
 * 페인트를 칠하는 장치가 아니다. 어두운 아스팔트 위에서 이 편이 훨씬 사실적이다.
 */

const VERT = /* glsl */ `
  attribute float aArc;    // 경로 시작점에서의 누적 거리 (m)
  attribute float aSide;   // 리본 횡방향 -1 ‥ +1
  varying float vArc;
  varying float vSide;
  void main() {
    vArc = aArc;
    vSide = aSide;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const FRAG = /* glsl */ `
  precision highp float;

  uniform vec3  uColor;
  uniform float uTime;
  uniform float uProgress;   // 0‥1, 차량이 지나온 비율
  uniform float uTotal;      // 경로 전체 길이 (m)
  uniform float uPeriod;     // 쉐브론 주기 (m)
  uniform float uSpeed;      // 쉐브론이 흐르는 속도 (m/s)
  uniform float uSkew;       // 쉐브론이 벌어지는 정도
  uniform float uDuty;       // 쉐브론 두께 비율
  uniform float uGlow;

  varying float vArc;
  varying float vSide;

  void main() {
    // ── 지나온 구간 소거 ──
    float head = uProgress * uTotal;
    if (vArc < head) discard;

    // 차량 바로 앞에서 선이 갑자기 시작하지 않도록 부드럽게 살린다
    float headFade = smoothstep(0.0, 2.5, vArc - head);
    // 목표 주차면에 가까워지면 살짝 밝아진다
    float tailBoost = 1.0 + 0.5 * smoothstep(6.0, 0.0, uTotal - vArc);

    // ── 쉐브론 ──
    // 중앙이 가장자리보다 앞서게 위상을 밀면 진행 방향을 가리키는 V 가 된다
    float s = (vArc - uTime * uSpeed) / uPeriod + abs(vSide) * uSkew;
    float p = fract(s);
    float band = smoothstep(0.0, 0.07, p) * (1.0 - smoothstep(uDuty - 0.07, uDuty, p));

    // ── 리본 가장자리 감쇠 ──
    float edge = 1.0 - smoothstep(0.55, 1.0, abs(vSide));

    // 은은한 바탕 + 밝은 쉐브론
    float base = 0.20 * edge;
    float lum = (base + band * edge * 1.15) * headFade * tailBoost * uGlow;

    // 가산 혼합에서 밝기를 그대로 올리면 색이 흰색으로 포화된다.
    // 차량별 색 구분이 이 시스템의 핵심이므로, 밝기는 알파로만 올리고
    // 색상은 원래 색조를 유지시킨다.
    float a = clamp(lum, 0.0, 1.0);
    vec3 col = mix(uColor, vec3(1.0), band * 0.16);
    gl_FragColor = vec4(col * min(lum, 1.35), a);
    if (a < 0.004) discard;
  }
`;

/** 폴리라인을 리본 메시로. 정점 간격이 균일해야 쉐브론이 일그러지지 않는다. */
function buildRibbonGeometry(THREE, points, halfWidth) {
  const n = points.length;
  const pos = new Float32Array(n * 2 * 3);
  const arc = new Float32Array(n * 2);
  const side = new Float32Array(n * 2);
  const idx = [];

  let acc = 0;
  for (let i = 0; i < n; i++) {
    const p = points[i];
    const prev = points[Math.max(0, i - 1)];
    const next = points[Math.min(n - 1, i + 1)];
    if (i > 0) acc += p.distanceTo(prev);

    // XZ 평면상의 접선과 그 수직
    let tx = next.x - prev.x;
    let tz = next.z - prev.z;
    const tl = Math.hypot(tx, tz) || 1;
    tx /= tl; tz /= tl;
    const nx = -tz, nz = tx;

    for (const sgn of [-1, 1]) {
      const k = i * 2 + (sgn < 0 ? 0 : 1);
      pos[k * 3] = p.x + nx * halfWidth * sgn;
      pos[k * 3 + 1] = p.y;
      pos[k * 3 + 2] = p.z + nz * halfWidth * sgn;
      arc[k] = acc;
      side[k] = sgn;
    }

    if (i < n - 1) {
      const a = i * 2, b = i * 2 + 1, c = (i + 1) * 2, d = (i + 1) * 2 + 1;
      idx.push(a, c, b, b, c, d);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aArc", new THREE.BufferAttribute(arc, 1));
  geo.setAttribute("aSide", new THREE.BufferAttribute(side, 1));
  geo.setIndex(idx);
  geo.computeBoundingSphere();
  return { geometry: geo, total: acc };
}

/** 각진 통로 경로를 부드럽게 만든다. centripetal 은 직각 코너에서 튀지 않는다. */
function smoothPath(THREE, points, spacing = 0.45) {
  if (points.length < 3) return points;
  const curve = new THREE.CatmullRomCurve3(points, false, "centripetal", 0.5);
  const n = Math.max(8, Math.ceil(curve.getLength() / spacing));
  return curve.getSpacedPoints(n);
}

export class GuidanceLine {
  /**
   * @param polyline 주차장 좌표 [[x, y], …]
   * @param color    이 차량에 배정된 고유색 (0xRRGGBB)
   */
  constructor(THREE, polyline, color, { width = 0.62, height = 0.035 } = {}) {
    this.THREE = THREE;
    const raw = polyline.map(([x, y]) => new THREE.Vector3(x, height, -y));
    const pts = smoothPath(THREE, raw);
    const { geometry, total } = buildRibbonGeometry(THREE, pts, width / 2);

    this.uniforms = {
      uColor: { value: new THREE.Color(color) },
      uTime: { value: 0 },
      uProgress: { value: 0 },
      uTotal: { value: total },
      uPeriod: { value: 2.6 },
      uSpeed: { value: 3.4 },
      uSkew: { value: 0.16 },
      uDuty: { value: 0.42 },
      uGlow: { value: 1.0 },
    };

    this.mesh = new THREE.Mesh(
      geometry,
      new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: FRAG,
        uniforms: this.uniforms,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        side: THREE.DoubleSide,
      })
    );
    this.mesh.renderOrder = 5;
    this.total = total;
    this.points = pts;
  }

  /** 차량이 지나온 비율 (0‥1). 이 앞쪽만 남기고 지운다. */
  setProgress(t) {
    this.uniforms.uProgress.value = Math.max(0, Math.min(1, t));
  }

  setGlow(g) {
    this.uniforms.uGlow.value = g;
  }

  update(dt) {
    this.uniforms.uTime.value += dt;
  }

  /** 경로 시작점에서 d 미터 지점의 위치와 진행 방향. */
  sample(d) {
    const pts = this.points;
    let acc = 0;
    for (let i = 0; i < pts.length - 1; i++) {
      const seg = pts[i].distanceTo(pts[i + 1]);
      if (acc + seg >= d) {
        const t = seg === 0 ? 0 : (d - acc) / seg;
        const p = pts[i].clone().lerp(pts[i + 1], t);
        const dir = pts[i + 1].clone().sub(pts[i]).normalize();
        return { position: p, heading: Math.atan2(-dir.z, dir.x) };
      }
      acc += seg;
    }
    const last = pts[pts.length - 1];
    const dir = last.clone().sub(pts[pts.length - 2]).normalize();
    return { position: last.clone(), heading: Math.atan2(-dir.z, dir.x) };
  }

  dispose() {
    this.mesh.geometry.dispose();
    this.mesh.material.dispose();
  }
}

/**
 * 목표 주차면 표식 — 같은 색으로 맥동한다.
 * 유도선 끝이 어디인지 한눈에 보여준다.
 */
export class TargetMarker {
  constructor(THREE, slot, color) {
    const g = new THREE.PlaneGeometry(slot.width * 0.94, slot.length * 0.94);
    this.material = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.3,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    this.mesh = new THREE.Mesh(g, this.material);
    this.mesh.rotation.x = -Math.PI / 2;
    this.mesh.position.set(slot.center[0], 0.028, -slot.center[1]);
    this.mesh.renderOrder = 4;
    this.t = Math.random() * 10;
  }

  update(dt) {
    this.t += dt;
    this.material.opacity = 0.18 + 0.20 * (0.5 + 0.5 * Math.sin(this.t * 2.4));
  }

  dispose() {
    this.mesh.geometry.dispose();
    this.material.dispose();
  }
}
