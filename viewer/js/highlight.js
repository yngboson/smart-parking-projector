/**
 * 사건 하이라이트 — 자리 강탈이 일어난 **그 자리**를 화면에서 짚어 준다.
 *
 * 발표에서 가장 어려운 순간이 여기다. 강탈은 1초 안에 끝나고 화면에는 차가
 * 서른 대 넘게 굴러다닌다. 이벤트 로그에 한 줄 뜬다고 청중이 그걸 찾지 못한다.
 * "지금 저기서 일어났습니다"를 눈으로 가리켜야 한다.
 *
 * 그래서 퍼져 나가는 고리(ring)를 그 자리 위에 띄운다. 세 번 퍼진 뒤 사라진다 —
 * 계속 남으면 화면이 지저분해지고, 한 번만 퍼지면 놓친다.
 *
 * **피해자의 색으로 그린다.** 그 색 유도선이 방금 다른 자리로 옮겨 갔으므로,
 * 청중이 두 사건을 같은 색으로 이어 볼 수 있다.
 */

const RINGS = 3;
const PERIOD = 1.1;
/** 고리 하나가 퍼지는 데 걸리는 시간(초). */

const GROW = 5.5;
/** 고리가 퍼져 나가는 반경(m). 주차면 하나를 충분히 감싸는 크기. */

export class EventFlash {
  /**
   * @param slot   도면의 주차면 (center, width, length, heading)
   * @param color  0xRRGGBB — 피해 차량의 유도선 색
   */
  constructor(THREE, slot, color) {
    this.THREE = THREE;
    this.group = new THREE.Group();
    this.age = 0;
    this.done = false;

    const inner = Math.max(slot.width, slot.length) * 0.34;
    this.rings = [];
    for (let i = 0; i < RINGS; i++) {
      const geo = new THREE.RingGeometry(inner, inner * 1.13, 48);
      const mat = new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0,
        depthWrite: false,
        side: THREE.DoubleSide,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(slot.center[0], 0.06, -slot.center[1]);
      mesh.renderOrder = 8;
      this.group.add(mesh);
      this.rings.push({ mesh, mat, delay: (i * PERIOD) / RINGS, base: inner });
    }

    // 가운데를 채워 두면 멀리서도 눈에 띈다. 고리보다 훨씬 옅게.
    const disc = new THREE.Mesh(
      new THREE.CircleGeometry(inner * 0.95, 40),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.0, depthWrite: false })
    );
    disc.rotation.x = -Math.PI / 2;
    disc.position.set(slot.center[0], 0.05, -slot.center[1]);
    disc.renderOrder = 7;
    this.group.add(disc);
    this.disc = disc;

    this.life = PERIOD * (RINGS + 1) / RINGS + PERIOD;
  }

  update(dt) {
    this.age += dt;
    if (this.age >= this.life) {
      this.done = true;
      return;
    }

    for (const r of this.rings) {
      const t = this.age - r.delay;
      if (t < 0 || t > PERIOD) {
        r.mat.opacity = 0;
        continue;
      }
      const u = t / PERIOD;
      const scale = 1 + (GROW / r.base) * u;
      r.mesh.scale.setScalar(scale);
      r.mat.opacity = 0.85 * (1 - u) ** 1.6;
    }

    const fade = 1 - this.age / this.life;
    this.disc.material.opacity = 0.28 * fade * (0.6 + 0.4 * Math.sin(this.age * 7.0));
  }

  dispose() {
    for (const r of this.rings) {
      r.mesh.geometry.dispose();
      r.mat.dispose();
    }
    this.disc.geometry.dispose();
    this.disc.material.dispose();
  }
}
