/**
 * 차량별 고유색과 유도선 차선 배정.
 *
 * 색은 지하철 역사의 바닥 유도선을 참고했다 (docs/DECISIONS.md D-006).
 * 형광색이 아니라 **톤다운된 원색**이다. 이유:
 *   - 채도가 너무 높으면 여러 줄이 나란히 있을 때 서로 번져 보인다
 *   - 실제 도색/투사물의 색감에 가깝다
 *
 * 차선(lane)은 여러 유도선이 같은 통로를 지날 때 겹치지 않도록 나란히 벌리는 값이다.
 * 지하철 유도선이 여러 갈래를 나란히 그리는 것과 같은 방식이다.
 */

/** 톤다운된 원색 10종. 색상환을 고르게 돌면서 명도·채도를 맞춰 골랐다. */
const BASE = [
  { hex: 0xc4453e, name: "빨강" },
  { hex: 0xd2762f, name: "주황" },
  { hex: 0xe0b138, name: "노랑" },
  { hex: 0x7fa33c, name: "연두" },
  { hex: 0x3e8b57, name: "초록" },
  { hex: 0x2e8a8a, name: "청록" },
  { hex: 0x4a92c4, name: "하늘" },
  { hex: 0x35509e, name: "파랑" },
  { hex: 0x7a4e9c, name: "보라" },
  { hex: 0xc55f86, name: "분홍" },
];

/**
 * 나란히 놓을 수 있는 최대 줄 수.
 *
 * 유도선은 양 끝까지 같은 간격으로 벌어진 채 간다 — 예전처럼 끝에서 0 으로
 * 수렴시키면 그 구간이 사선이 되기 때문이다 (D-023). 그래서 가장 바깥 줄이
 * **주차면 안까지** 그만큼 치우친 채 들어간다. 바깥 줄의 치우침
 * (MAX_LANES-1)/2 × LANE_PITCH = 1.44m 는 주차면 반폭 2.1m 안에 들어가야 한다.
 */
export const MAX_LANES = 5;

/** 줄 사이 간격 (m). 띠 폭(0.55m)보다 넉넉히 커야 서로 붙어 보이지 않는다. */
export const LANE_PITCH = 0.72;

export class Palette {
  constructor() {
    this.assigned = new Map();   // plate → { color, name, lane }
    this.usedColors = new Set();
    this.usedLanes = new Set();
  }

  /**
   * 이 차량의 유도선 스타일. 없으면 사용 중이 아닌 색과 차선을 새로 배정한다.
   * @returns {{color: number, name: string, lane: number}}
   */
  acquire(plate) {
    const existing = this.assigned.get(plate);
    if (existing) return existing;

    const entry =
      BASE.find((c) => !this.usedColors.has(c.hex)) ??
      BASE[this.assigned.size % BASE.length];

    let lane = 0;
    while (lane < MAX_LANES && this.usedLanes.has(lane)) lane++;
    if (lane >= MAX_LANES) lane = this.assigned.size % MAX_LANES;

    const style = { color: entry.hex, name: entry.name, lane };
    this.assigned.set(plate, style);
    this.usedColors.add(entry.hex);
    this.usedLanes.add(lane);
    return style;
  }

  /** 주차 완료/출차. 색과 차선을 다음 차량이 쓸 수 있게 반납한다. */
  release(plate) {
    const s = this.assigned.get(plate);
    if (!s) return;
    this.assigned.delete(plate);
    this.usedColors.delete(s.color);
    this.usedLanes.delete(s.lane);
  }

  /** 차선 번호 → 통로 중심선에서의 횡방향 오프셋 (m). 가운데를 기준으로 벌린다. */
  static laneOffset(lane) {
    const half = (MAX_LANES - 1) / 2;
    return (lane - half) * LANE_PITCH;
  }

  static css(color) {
    return "#" + color.toString(16).padStart(6, "0");
  }
}
