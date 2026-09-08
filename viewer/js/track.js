/**
 * 주행 궤적 — 폴리라인을 부드럽게 만들고 거리로 샘플링한다.
 *
 * 유도선(리본)과 출차 차량이 같은 코드를 쓴다. 출차 차량에는 유도선을 그리지 않지만
 * 궤적을 따라 움직이는 방식은 같기 때문이다.
 *
 * 차선 오프셋(laneOffset)은 여기서 적용한다. 여러 유도선이 같은 통로를 지날 때
 * 나란히 벌리기 위한 값이며, 시작과 끝에서는 0 으로 수렴시킨다 — 입구와 주차면
 * 진입은 중앙으로 들어와야 하기 때문이다.
 */

const TAPER_IN = 7.0;
const TAPER_OUT = 9.0;

/** 각진 통로 경로를 부드럽게. centripetal 은 직각 코너에서 튀지 않는다. */
function smooth(THREE, points, spacing) {
  if (points.length < 3) return points;
  const curve = new THREE.CatmullRomCurve3(points, false, "centripetal", 0.5);
  const n = Math.max(8, Math.ceil(curve.getLength() / spacing));
  return curve.getSpacedPoints(n);
}

/**
 * @param polyline 주차장 좌표 [[x, y], …]
 * @returns 거리로 샘플링할 수 있는 궤적
 */
export function makeTrack(THREE, polyline, opts = {}) {
  const { laneOffset = 0, spacing = 0.4, height = 0.035 } = opts;

  const raw = polyline.map(([x, y]) => new THREE.Vector3(x, height, -y));
  const base = smooth(THREE, raw, spacing);

  // 누적 거리를 먼저 구해야 시작/끝 수렴 구간을 계산할 수 있다
  const cum = [0];
  for (let i = 1; i < base.length; i++) {
    cum.push(cum[i - 1] + base[i].distanceTo(base[i - 1]));
  }
  const total = cum[cum.length - 1];

  const points = base.map((p, i) => {
    if (laneOffset === 0) return p.clone();
    const prev = base[Math.max(0, i - 1)];
    const next = base[Math.min(base.length - 1, i + 1)];
    let tx = next.x - prev.x;
    let tz = next.z - prev.z;
    const tl = Math.hypot(tx, tz) || 1;
    tx /= tl; tz /= tl;
    const fade = Math.min(
      Math.min(1, cum[i] / TAPER_IN),
      Math.min(1, (total - cum[i]) / TAPER_OUT)
    );
    const off = laneOffset * fade;
    return new THREE.Vector3(p.x + -tz * off, p.y, p.z + tx * off);
  });

  return {
    points,
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
