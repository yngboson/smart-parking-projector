/**
 * 렌더러 · 카메라 · 조명.
 *
 * 조명 프리셋에 "황혼/야간"을 넣은 것은 취향이 아니라 논지다 (D-011).
 * 빔 프로젝터 유도선은 어두울수록 잘 보이고, 그게 실제 운용 조건이기도 하다.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/OrbitControls.js";

export const PRESETS = {
  day: {
    label: "주간",
    sky: 0x8fb3d9,
    // 환경광은 배경색과 따로 둔다. 배경이 어둡다고 그늘까지 새까매지면 안 된다.
    hemiSky: 0xc3d9f2, hemiGround: 0x6d6b60, ambient: 1.15,
    sun: 0xfff4e2, sunIntensity: 2.5, sunAngle: [0.35, 1.0, 0.45],
    fog: 0xa8c0d8, fogDensity: 0.0016,
    exposure: 1.0, glow: 0.55, lamps: 0.0,
  },
  dusk: {
    label: "황혼",
    sky: 0x35405c,
    hemiSky: 0x93a4cc, hemiGround: 0x5a4d40, ambient: 1.35,
    sun: 0xffc093, sunIntensity: 2.9, sunAngle: [-0.9, 0.55, 0.2],
    fog: 0x2b3247, fogDensity: 0.0026,
    exposure: 1.12, glow: 1.0, lamps: 0.6,
  },
  night: {
    label: "야간",
    sky: 0x0a0f18,
    hemiSky: 0x46587d, hemiGround: 0x16191f, ambient: 0.62,
    sun: 0x9fb6d8, sunIntensity: 0.22, sunAngle: [-0.4, 0.9, -0.3],
    fog: 0x080b11, fogDensity: 0.0038,
    exposure: 1.28, glow: 1.55, lamps: 1.0,
  },
};

export class Stage {
  constructor(container) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.5, 900);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.075;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.04;
    this.controls.minDistance = 12;
    this.controls.maxDistance = 320;

    this.hemi = new THREE.HemisphereLight(0xffffff, 0x444444, 1);
    this.scene.add(this.hemi);

    this.sun = new THREE.DirectionalLight(0xffffff, 1);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0006;
    this.sun.shadow.normalBias = 0.02;
    this.scene.add(this.sun);
    this.scene.add(this.sun.target);

    this.lamps = [];

    this._onResize = () => this.resize();
    addEventListener("resize", this._onResize);
    this.resize();
  }

  /** 도면 크기에 맞춰 그림자 절두체와 카메라 사거리를 잡는다. */
  fitTo(lot) {
    const [minX, minY, maxX, maxY] = lot.bounds;
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const w = maxX - minX;
    const h = maxY - minY;
    // 도면 전체가 화면에 들어오도록 넉넉히 잡는다
    const r = Math.max(w, h) * 1.05;
    const shadowR = Math.max(w, h) * 0.62;

    this.center = new THREE.Vector3(cx, 0, -cy);
    this.radius = r;
    this.controls.target.copy(this.center);

    // 그림자 절두체는 따로 좁게 잡는다 — 넓히면 텍셀이 커져 그림자가 뭉갠다
    const s = this.sun.shadow.camera;
    s.left = -shadowR; s.right = shadowR; s.top = shadowR; s.bottom = -shadowR;
    s.near = 1; s.far = shadowR * 8;
    s.updateProjectionMatrix();
    this.sun.target.position.copy(this.center);

    this.setCamera("bird", true);
  }

  /**
   * 주차장 가로등. 야간 프리셋에서 조명 역할도 하지만, 주간에도 기둥이 서 있어야
   * 빈 아스팔트가 허전해 보이지 않는다.
   */
  addLampPosts(positions) {
    const poleMat = new THREE.MeshStandardMaterial({ color: 0x33383f, roughness: 0.7, metalness: 0.5 });
    const armGeo = new THREE.BoxGeometry(1.5, 0.16, 0.16);
    const poleGeo = new THREE.CylinderGeometry(0.11, 0.16, 8.0, 10);
    const bulbGeo = new THREE.BoxGeometry(1.0, 0.18, 0.5);

    for (const [x, y] of positions) {
      const g = new THREE.Group();
      const pole = new THREE.Mesh(poleGeo, poleMat);
      pole.position.y = 4.0;
      pole.castShadow = true;
      g.add(pole);

      const arm = new THREE.Mesh(armGeo, poleMat);
      arm.position.set(0.75, 7.95, 0);
      g.add(arm);

      const bulb = new THREE.Mesh(bulbGeo, new THREE.MeshBasicMaterial({ color: 0xffe9c4 }));
      bulb.position.set(1.4, 7.85, 0);
      g.add(bulb);

      const light = new THREE.PointLight(0xffe4b8, 0, 34, 2);
      light.position.set(1.4, 7.6, 0);
      g.add(light);

      g.position.set(x, 0, -y);
      this.scene.add(g);
      this.lamps.push({ group: g, light, bulb });
    }
  }

  setCamera(mode, instant = false) {
    const c = this.center ?? new THREE.Vector3();
    const r = this.radius ?? 60;
    const target =
      mode === "top"
        ? new THREE.Vector3(c.x, r * 1.25, c.z + 0.001)
        : new THREE.Vector3(c.x - r * 0.58, r * 0.86, c.z + r * 0.96);

    this.cameraMode = mode;
    if (instant) {
      this.camera.position.copy(target);
      this.controls.target.copy(c);
      this.controls.update();
    } else {
      this._camTween = { from: this.camera.position.clone(), to: target, t: 0 };
    }
  }

  applyPreset(name) {
    const p = PRESETS[name] ?? PRESETS.dusk;
    this.preset = p;
    this.presetName = name;

    this.scene.background = new THREE.Color(p.sky);
    this.scene.fog = new THREE.FogExp2(p.fog, p.fogDensity);

    this.hemi.color.setHex(p.hemiSky);
    this.hemi.groundColor.setHex(p.hemiGround);
    this.hemi.intensity = p.ambient;

    // 가로등은 어두울 때만 켠다
    for (const lamp of this.lamps) {
      lamp.light.intensity = p.lamps * 130;
      lamp.bulb.material.color.setHex(p.lamps > 0.1 ? 0xffe9c4 : 0x5a5a52);
    }

    this.sun.color.setHex(p.sun);
    this.sun.intensity = p.sunIntensity;
    const r = this.radius ?? 60;
    const c = this.center ?? new THREE.Vector3();
    this.sun.position.set(
      c.x + p.sunAngle[0] * r * 2.2,
      p.sunAngle[1] * r * 2.0,
      c.z + p.sunAngle[2] * r * 2.2
    );
    this.renderer.toneMappingExposure = p.exposure;
    return p;
  }

  resize() {
    const w = innerWidth;
    const h = innerHeight;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  update(dt) {
    if (this._camTween) {
      const tw = this._camTween;
      tw.t = Math.min(1, tw.t + dt * 1.6);
      const e = 1 - Math.pow(1 - tw.t, 3);
      this.camera.position.lerpVectors(tw.from, tw.to, e);
      if (tw.t >= 1) this._camTween = null;
    }
    this.controls.update();
  }

  render() {
    this.renderer.render(this.scene, this.camera);
  }
}

export { THREE };
