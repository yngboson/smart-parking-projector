/**
 * 유도선 — 연속된 굵은 색 띠 + 흰색 쉐브론 포인트.
 *
 * 지하철 역사의 바닥 유도선을 참고한 디자인이다 (docs/DECISIONS.md D-006).
 *   - 차선처럼 양쪽에 두 줄을 그리지 않는다. 여러 대가 겹치면 구분이 안 된다.
 *   - **끊기지 않는 한 줄의 띠**로 그린다. 점선보다 따라가기 쉽다.
 *   - 흰색 쉐브론을 일정 간격으로 얹어 진행 방향을 알린다.
 *   - **지나온 구간은 지운다.** 바닥에 남는 선의 총량이 줄어 혼란이 감소한다.
 *   - 여러 차량의 선이 같은 통로를 지나면 **나란히 벌려서** 겹치지 않게 한다.
 *
 * 색 띠는 가산 혼합이 아니라 일반 혼합으로 그린다. 가산으로 그리면 밝기를 올릴 때
 * 색이 흰색으로 포화되어 차량별 색 구분이 사라지는데, 그 구분이 이 시스템의 핵심이다.
 * 대신 밝기는 색 자체를 밝히는 방식으로 조절한다 — 어두울수록 투사물이 밝게 보이는
 * 실제 프로젝터의 거동과도 맞는다.
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
  uniform float uPeriod;     // 쉐브론 간격 (m)
  uniform float uSpeed;      // 쉐브론이 흐르는 속도 (m/s)
  uniform float uSkew;       // 쉐브론이 벌어지는 각도
  uniform float uThick;      // 쉐브론 두께 (주기 대비 비율)
  uniform float uGlow;

  varying float vArc;
  varying float vSide;

  void main() {
    // ── 지나온 구간 소거 ──
    float head = uProgress * uTotal;
    if (vArc < head) discard;

    // 차량 바로 앞에서 띠가 뚝 시작하지 않도록 부드럽게 살린다
    float headFade = smoothstep(0.0, 2.0, vArc - head);

    float edgeDist = abs(vSide);

    // ── 띠 본체 ──
    // 가장자리를 아주 살짝만 부드럽게 해서 도색한 띠처럼 각을 살린다
    float body = 1.0 - smoothstep(0.86, 1.0, edgeDist);

    // ── 흰색 쉐브론 ──
    // 중앙이 가장자리보다 앞서게 위상을 밀면 진행 방향을 가리키는 ∧ 가 된다
    float s = (vArc - uTime * uSpeed) / uPeriod + edgeDist * uSkew;
    float d = abs(fract(s) - 0.5);
    float chev = 1.0 - smoothstep(uThick, uThick + 0.035, d);
    chev *= body;

    // ── 가장자리 테두리 ──
    // 여러 줄이 나란히 놓일 때 서로 번지지 않게 경계를 살짝 눌러준다
    float rim = smoothstep(0.72, 0.94, edgeDist) * (1.0 - smoothstep(0.94, 1.0, edgeDist));

    vec3 col = mix(uColor, vec3(1.0), chev * 0.94);
    col = mix(col, uColor * 0.55, rim * 0.55);

    // 어두운 환경일수록 투사물이 밝게 보인다
    col *= 0.62 + 0.72 * uGlow;

    float alpha = body * headFade * 0.94;
    if (alpha < 0.01) discard;
    gl_FragColor = vec4(col, alpha);
  }
`;

/**
 * 폴리라인을 리본 메시로 만든다.
 *
 * @param offset 통로 중심선에서 옆으로 밀 거리 (m). 여러 유도선을 나란히 놓을 때 쓴다.
 *   시작과 끝에서는 0 으로 수렴시킨다 — 입구와 주차면 진입은 중앙으로 들어와야 하고,
 *   그래야 차량이 주차면 한가운데로 향한다.
 */
function buildRibbonGeometry(THREE, points, halfWidth, offset) {
  const n = points.length;
  const pos = new Float32Array(n * 2 * 3);
  const arc = new Float32Array(n * 2);
  const side = new Float32Array(n * 2);
  const idx = [];

  // 누적 거리를 먼저 구해야 시작/끝 수렴 구간을 계산할 수 있다
  const cum = [0];
  for (let i = 1; i < n; i++) cum.push(cum[i - 1] + points[i].distanceTo(points[i - 1]));
  const total = cum[n - 1];

  const TAPER_IN = 7.0;
  const TAPER_OUT = 9.0;

  for (let i = 0; i < n; i++) {
    const p = points[i];
    const prev = points[Math.max(0, i - 1)];
    const next = points[Math.min(n - 1, i + 1)];

    // XZ 평면상의 접선과 그 수직
    let tx = next.x - prev.x;
    let tz = next.z - prev.z;
    const tl = Math.hypot(tx, tz) || 1;
    tx /= tl; tz /= tl;
    const nx = -tz, nz = tx;

    const fadeIn = Math.min(1, cum[i] / TAPER_IN);
    const fadeOut = Math.min(1, (total - cum[i]) / TAPER_OUT);
    const off = offset * Math.min(fadeIn, fadeOut);

    const cx = p.x + nx * off;
    const cz = p.z + nz * off;

    for (const sgn of [-1, 1]) {
      const k = i * 2 + (sgn < 0 ? 0 : 1);
      pos[k * 3] = cx + nx * halfWidth * sgn;
      pos[k * 3 + 1] = p.y;
      pos[k * 3 + 2] = cz + nz * halfWidth * sgn;
      arc[k] = cum[i];
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
  return { geometry: geo, total, centers: buildCenters(THREE, points, cum, total, offset) };
}

/** 차량이 실제로 따라갈 선 — 리본과 같은 오프셋을 적용한 중심선. */
function buildCenters(THREE, points, cum, total, offset) {
  const out = [];
  const TAPER_IN = 7.0;
  const TAPER_OUT = 9.0;
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    const prev = points[Math.max(0, i - 1)];
    const next = points[Math.min(points.length - 1, i + 1)];
    let tx = next.x - prev.x;
    let tz = next.z - prev.z;
    const tl = Math.hypot(tx, tz) || 1;
    tx /= tl; tz /= tl;
    const off =
      offset *
      Math.min(Math.min(1, cum[i] / TAPER_IN), Math.min(1, (total - cum[i]) / TAPER_OUT));
    out.push(new THREE.Vector3(p.x + -tz * off, p.y, p.z + tx * off));
  }
  return out;
}

/** 각진 통로 경로를 부드럽게 만든다. centripetal 은 직각 코너에서 튀지 않는다. */
function smoothPath(THREE, points, spacing = 0.4) {
  if (points.length < 3) return points;
  const curve = new THREE.CatmullRomCurve3(points, false, "centripetal", 0.5);
  const n = Math.max(8, Math.ceil(curve.getLength() / spacing));
  return curve.getSpacedPoints(n);
}

export class GuidanceLine {
  /**
   * @param polyline 주차장 좌표 [[x, y], …]
   * @param color    이 차량에 배정된 고유색 (0xRRGGBB)
   * @param opts.laneOffset 통로 중심선에서 옆으로 밀 거리 (m)
   */
  constructor(THREE, polyline, color, opts = {}) {
    const { width = 0.55, height = 0.035, laneOffset = 0 } = opts;
    this.THREE = THREE;

    const raw = polyline.map(([x, y]) => new THREE.Vector3(x, height, -y));
    const pts = smoothPath(THREE, raw);
    const { geometry, total, centers } = buildRibbonGeometry(
      THREE, pts, width / 2, laneOffset
    );

    this.uniforms = {
      uColor: { value: new THREE.Color(color) },
      uTime: { value: 0 },
      uProgress: { value: 0 },
      uTotal: { value: total },
      uPeriod: { value: 3.4 },
      uSpeed: { value: 2.2 },
      uSkew: { value: 0.085 },
      uThick: { value: 0.055 },
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
        side: THREE.DoubleSide,
      })
    );
    this.mesh.renderOrder = 5;
    this.total = total;
    this.points = centers;
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
    const g = new THREE.PlaneGeometry(slot.width * 0.9, slot.length * 0.9);
    this.material = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.3,
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
    this.material.opacity = 0.22 + 0.24 * (0.5 + 0.5 * Math.sin(this.t * 2.4));
  }

  dispose() {
    this.mesh.geometry.dispose();
    this.material.dispose();
  }
}
