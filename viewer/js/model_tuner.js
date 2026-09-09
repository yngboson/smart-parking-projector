/**
 * STL 조정 UI — 모델을 넣고 **화면을 보면서** 축·크기·방향을 맞춘다.
 *
 * STL 파일은 단위도 축 방향도 담고 있지 않다. mm 로 만든 것도 있고 m 로 만든 것도
 * 있으며, CAD 출력물은 대개 Z-up 이고 게임 에셋은 Y-up 이다. 그래서 어떤 파일이
 * 들어와도 **숫자 네 개를 맞추면** 제자리에 서게 만들어 뒀다 (D-007).
 *
 * 문제는 그 네 개를 어떻게 맞추느냐였다. 값을 고치고 → 파일을 저장하고 →
 * 브라우저를 새로고침하고 → 차가 들어오기를 기다려서 확인하는 반복은, 한 모델에
 * 20분씩 걸린다. 발표 전날 밤에 할 일이 아니다.
 *
 * 그래서 조정판을 붙였다.
 *
 *   - 주차장 한쪽에 **미리보기 차량**을 세워 두고 실시간으로 다시 그린다
 *   - 실제 주차면 한 칸을 옆에 같이 그려 크기를 눈으로 비교한다
 *   - 맞으면 저장 — `models.json` 에 되쓰므로 새로고침해도 남는다
 *
 * STL 이 하나도 없으면 조정판 자체를 숨긴다. 없는 것을 조정할 수는 없다.
 */

const FIELDS = [
  { key: "autoFitLength", label: "전장(m)", min: 2.0, max: 8.0, step: 0.05, unit: "m" },
  { key: "headingOffsetDeg", label: "방향 보정", min: -180, max: 180, step: 15, unit: "°" },
  { key: "offsetY", label: "높이 보정", min: -1.0, max: 1.0, step: 0.02, unit: "m" },
];

export class ModelTuner {
  /**
   * @param stage   Stage — 미리보기를 얹을 장면
   * @param models  VehicleModels — 설정을 들고 있는 저장소
   * @param lot     도면 (미리보기를 세울 자리와 주차면 크기를 얻는다)
   */
  constructor(THREE, stage, models, lot, root) {
    this.THREE = THREE;
    this.stage = stage;
    this.models = models;
    this.lot = lot;
    this.root = root;
    this.name = null;
    this.preview = null;

    // 도면 바깥 한쪽. 실제 차량들과 섞이지 않아야 크기를 비교할 수 있다.
    const [minX, minY, , maxY] = lot.bounds;
    this.spot = [minX - 9.0, (minY + maxY) / 2];
  }

  /**
   * 조정판을 그린다. 등록된 모델도 STL 파일도 없으면 아무것도 하지 않는다.
   * @returns {boolean} 그렸는가 — 호출부가 패널을 보일지 정한다
   */
  mount() {
    const names = this.models.names;
    const files = this.models.files ?? [];
    if (!names.length && !files.length) return false;
    this.root.innerHTML = "";

    const pick = document.createElement("select");
    this._field("모델", pick);
    pick.addEventListener("change", () => this.select(pick.value));
    this.pick = pick;
    this._fillModels();

    if (files.length) {
      const fileSel = document.createElement("select");
      fileSel.innerHTML = files.map((f) => `<option value="${f}">${f}</option>`).join("");
      this._field("STL 파일", fileSel);
      fileSel.addEventListener("change", () => this.useFile(fileSel.value));
      this.fileSel = fileSel;

      const add = document.createElement("button");
      add.className = "wide";
      add.textContent = "이 STL 을 새 모델로 등록";
      this.root.append(add);
      add.addEventListener("click", () => this.useFile(fileSel.value, { fresh: true }));
    }

    this.inputs = {};
    for (const f of FIELDS) {
      const input = document.createElement("input");
      Object.assign(input, { type: "range", min: f.min, max: f.max, step: f.step });
      const out = document.createElement("span");
      out.style.color = "var(--text)";
      this._field(`${f.label} `, input, out);
      input.addEventListener("input", () => {
        out.textContent = `${Number(input.value).toFixed(2)}${f.unit}`;
        this.apply(f.key, Number(input.value));
      });
      this.inputs[f.key] = { input, out, spec: f };
    }

    const axis = document.createElement("select");
    axis.innerHTML = `<option value="Y">Y-up (게임 에셋)</option><option value="Z">Z-up (CAD 출력)</option>`;
    this._field("위쪽 축", axis);
    axis.addEventListener("change", () => this.apply("upAxis", axis.value));
    this.axis = axis;

    const pivot = document.createElement("select");
    pivot.innerHTML = `<option value="rear_axle">뒷축 기준</option><option value="center">차체 중심</option>`;
    this._field("원점", pivot);
    pivot.addEventListener("change", () => this.apply("pivot", pivot.value));
    this.pivot = pivot;

    const save = document.createElement("button");
    save.className = "wide";
    save.textContent = "models.json 에 저장";
    const note = document.createElement("p");
    note.className = "note";
    note.textContent =
      "미리보기 옆의 회색 사각형이 실제 주차면 한 칸입니다. 크기를 여기에 맞추세요.";
    this.root.append(save, note);

    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const { saved } = await this.models.save();
        note.textContent = `저장했습니다 (모델 ${saved}개). 새로고침해도 남습니다.`;
      } catch (e) {
        note.textContent = `저장하지 못했습니다: ${e.message}`;
      } finally {
        save.disabled = false;
      }
    });

    if (names.length) this.select(names[0]);
    return true;
  }

  _fillModels() {
    const names = this.models.names;
    this.pick.innerHTML = names.length
      ? names.map((n) => `<option value="${n}">${n}</option>`).join("")
      : `<option value="">(등록된 모델 없음)</option>`;
    if (this.name) this.pick.value = this.name;
  }

  /**
   * STL 파일을 지금 모델에 물리거나, 새 모델로 등록한다.
   *
   * **폴더에 STL 을 넣은 사람이 `models.json` 을 손으로 고치지 않아도 되어야 한다.**
   * 그러지 않으면 "STL 넣으면 즉시 반영"이 아니라 "STL 넣고 JSON 을 배운 다음
   * 반영"이 된다.
   *
   * 이름은 파일 이름에서 딴다. `sedan_a.stl` → `sedan_a` 이고, 이 이름이 곧
   * 프레임의 `model` 필드와 맞물린다 (`sim/common/ids.py` 의 차종 이름).
   */
  useFile(file, { fresh = false } = {}) {
    if (!file) return;
    const name = fresh || !this.name ? file.replace(/\.stl$/i, "") : this.name;
    const existing = this.models.configs[name];
    this.models.configure(name, {
      file,
      // 새로 등록하는 것이면 흔한 CAD 출력 기준으로 시작한다. 어차피 눈으로 맞춘다.
      ...(existing ? {} : { autoFitLength: 4.7, upAxis: "Z", pivot: "rear_axle", headingOffsetDeg: 0, offset: [0, 0, 0] }),
    });
    this.name = name;
    this._fillModels();
    this.select(name);
  }

  _field(label, ...controls) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    const l = document.createElement("label");
    l.textContent = label;
    if (controls.length > 1) l.append(controls.pop());
    wrap.append(l, ...controls);
    this.root.append(wrap);
    return wrap;
  }

  select(name) {
    this.name = name || null;
    if (!this.name) return;
    const cfg = this.models.configs[this.name] ?? {};

    for (const [key, { input, out, spec }] of Object.entries(this.inputs)) {
      const value =
        key === "offsetY" ? (cfg.offset ?? [0, 0, 0])[1] : cfg[key] ?? spec.min;
      input.value = String(value ?? 0);
      out.textContent = `${Number(input.value).toFixed(2)}${spec.unit}`;
    }
    this.axis.value = cfg.upAxis ?? "Y";
    this.pivot.value = cfg.pivot ?? "center";
    if (this.fileSel && cfg.file) this.fileSel.value = cfg.file;
    this.redraw();
  }

  apply(key, value) {
    if (!this.name) return;
    if (key === "offsetY") {
      const cur = this.models.configs[this.name]?.offset ?? [0, 0, 0];
      this.models.configure(this.name, { offset: [cur[0], value, cur[2]] });
    } else {
      this.models.configure(this.name, { [key]: value });
    }
    this.redraw();
  }

  /** 미리보기 차량을 지금 설정으로 다시 만든다. */
  async redraw() {
    if (!this.name) return;
    const THREE = this.THREE;
    const [x, y] = this.spot;

    const group = new THREE.Group();
    const car = await this.models.create(this.name, {
      length: 4.7, width: 1.85, color: 0xe0e3e8,
    });
    group.add(car);
    group.add(this._slotOutline());
    group.position.set(x, 0, -y);

    if (this.preview) this.stage.scene.remove(this.preview);
    this.preview = group;
    this.stage.scene.add(group);
    this.stage.lookAtPoint(x, y);
  }

  /** 실제 주차면 한 칸. 크기를 눈으로 견줄 기준이 없으면 조정이 불가능하다. */
  _slotOutline() {
    const THREE = this.THREE;
    const slot = this.lot.slots[0];
    const w = slot?.width ?? 4.2;
    const l = slot?.length ?? 8.25;

    const pts = [
      new THREE.Vector3(-l / 2, 0.02, -w / 2),
      new THREE.Vector3(l / 2, 0.02, -w / 2),
      new THREE.Vector3(l / 2, 0.02, w / 2),
      new THREE.Vector3(-l / 2, 0.02, w / 2),
      new THREE.Vector3(-l / 2, 0.02, -w / 2),
    ];
    return new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: 0x9aa4b2 })
    );
  }
}
