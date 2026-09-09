/**
 * 주행 궤적 — 폴리라인을 **꺾인 그대로** 들고 거리로 샘플링한다.
 *
 * 유도선(리본)이 이 위에 얹힌다.
 *
 * 예전에는 Catmull-Rom 으로 코너를 둥글렸다. 실제 차가 그리는 곡선에 가깝기
 * 때문인데, 바닥에 **그리는** 선으로는 틀린 선택이었다 — 운전하는 것은 사람이고,
 * 사람은 지하철 노선도처럼 직각으로 꺾인 선을 더 빨리 읽는다. 차가 코너를 둥글게
 * 도는 것은 차가 알아서 할 일이다 (docs/DECISIONS.md D-023).
 *
 * 차선 오프셋(laneOffset)은 여기서 적용한다. 여러 유도선이 같은 통로를 지날 때
 * 나란히 벌리기 위한 값이며, **처음부터 끝까지 같은 크기로** 민다. 양 끝에서
 * 0 으로 수렴시키면 그 구간이 사선이 되어, 직각으로 만든 보람이 사라진다.
 */

/** 꼭짓점을 밀어낼 때 허용하는 최대 배율. 되돌아가는 각에서 발산하는 것을 막는다. */
const MITER_LIMIT = 4.0;

/**
 * 꼭짓점마다 우측 법선과 마이터 배율을 구한다.
 *
 * 배율 1/cos(반각) 이 없으면 밀어낸 선이 원래 코너를 통과하지 못하고 안쪽으로
 * 잘린다 — 직각 코너가 사선 한 구간으로 변한다. `sim/common/geometry.py` 의
 * `offset_polyline` 과 같은 계산이다.
 */
export function joints(points) {
  const last = points.length - 1;
  return points.map((p, i) => {
    const a = i > 0 ? dir(points[i - 1], p) : null;
    const b = i < last ? dir(p, points[i + 1]) : null;

    if (!a || !b) {
      const d = a ?? b ?? { x: 1, z: 0 };
      return { nx: -d.z, nz: d.x, miter: 1 };
    }
    let mx = a.x + b.x, mz = a.z + b.z;
    const ml = Math.hypot(mx, mz);
    if (ml < 1e-6) return { nx: -a.z, nz: a.x, miter: 1 };   // 되돌아가는 꼭짓점
    mx /= ml; mz /= ml;
    const cosHalf = mx * a.x + mz * a.z;
    const miter = cosHalf > 1e-6 ? Math.min(MITER_LIMIT, 1 / cosHalf) : MITER_LIMIT;
    return { nx: -mz, nz: mx, miter };
  });
}

function dir(from, to) {
  const dx = to.x - from.x, dz = to.z - from.z;
  const l = Math.hypot(dx, dz) || 1;
  return { x: dx / l, z: dz / l };
}

/**
 * @param polyline 주차장 좌표 [[x, y], …]
 * @returns 거리로 샘플링할 수 있는 궤적
 */
export function makeTrack(THREE, polyline, opts = {}) {
  const { laneOffset = 0, height = 0.035 } = opts;

  // 같은 점이 연달아 오면 법선을 구할 수 없다
  const base = [];
  for (const [x, y] of polyline) {
    const p = new THREE.Vector3(x, height, -y);
    const prev = base[base.length - 1];
    if (!prev || prev.distanceTo(p) > 1e-4) base.push(p);
  }
  if (base.length === 1) base.push(base[0].clone());

  const frames = joints(base);
  const points =
    laneOffset === 0
      ? base
      : base.map((p, i) => {
          const f = frames[i];
          const off = laneOffset * f.miter;
          return new THREE.Vector3(p.x + f.nx * off, p.y, p.z + f.nz * off);
        });

  const cum = [0];
  for (let i = 1; i < points.length; i++) {
    cum.push(cum[i - 1] + points[i].distanceTo(points[i - 1]));
  }
  const total = cum[cum.length - 1];

  return {
    points,
    frames: joints(points),
    cum,
    total,

    /** 시작점에서 d 미터 지점의 위치와 진행 방향(주차장 좌표계 rad). */
    sample(d) {
      const pts = points;
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
    },
  };
}
