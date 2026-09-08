/**
 * 차량별 고유색 배정.
 *
 * 요구사항 (docs/DECISIONS.md D-006):
 *   - 동시에 안내 중인 차량끼리 확실히 구분될 것
 *   - 어두운 아스팔트 위에서 잘 보일 것
 *   - 주차를 마치기 전까지 색을 재사용하지 말 것
 *
 * 12색은 색상환을 고르게 돌면서 명도를 높게 유지하도록 손으로 고른 값이다.
 * 알고리즘으로 생성한 색보다 사람 눈에 구분이 잘 된다.
 */

const BASE = [
  0xff4d5a, 0xffa63d, 0xffe14d, 0x8ede3a,
  0x26d07c, 0x2fd6c3, 0x45c2ff, 0x5b7cff,
  0xa97bff, 0xf06bff, 0xff7aa8, 0xc9b08a,
];

/** 12색을 다 쓰면 황금각으로 이어서 만든다 (그래도 서로 벌어진다). */
function golden(i) {
  const h = ((i * 137.508) % 360) / 360;
  const l = i % 2 === 0 ? 0.68 : 0.60;
  return hslToHex(h, 0.82, l);
}

function hslToHex(h, s, l) {
  const f = (n) => {
    const k = (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
  };
  return (f(0) << 16) | (f(8) << 8) | f(4);
}

export class Palette {
  constructor() {
    this.assigned = new Map();  // plate → color
    this.inUse = new Set();     // color
    this.cursor = 0;
  }

  /** 이 차량의 색. 없으면 사용 중이 아닌 색 중에서 새로 준다. */
  acquire(plate) {
    const existing = this.assigned.get(plate);
    if (existing !== undefined) return existing;

    let color = BASE.find((c) => !this.inUse.has(c));
    if (color === undefined) {
      do {
        color = golden(this.cursor++);
      } while (this.inUse.has(color) && this.cursor < 512);
    }

    this.assigned.set(plate, color);
    this.inUse.add(color);
    return color;
  }

  /** 주차 완료/출차. 이제부터 다른 차가 이 색을 쓸 수 있다. */
  release(plate) {
    const c = this.assigned.get(plate);
    if (c === undefined) return;
    this.assigned.delete(plate);
    this.inUse.delete(c);
  }

  static css(color) {
    return "#" + color.toString(16).padStart(6, "0");
  }
}
