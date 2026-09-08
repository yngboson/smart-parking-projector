/**
 * 프레임 공급원 — 라이브와 녹화본을 같은 얼굴로 감싼다.
 *
 * 라이브(WebSocket)와 녹화본(trace.jsonl)은 **완전히 같은 프레임 포맷**을 쓴다
 * (docs/DECISIONS.md D-005). 그래서 뷰어 본체는 어느 쪽에서 프레임이 오는지
 * 알 필요가 없고, 재생 코드도 한 벌만 있으면 된다.
 *
 * 발표장에서 이게 왜 중요한가: 서버가 죽거나 네트워크가 막혀도 녹화본으로
 * 같은 화면이 나온다. 그 전환이 코드 한 줄이어야 당황하지 않는다.
 *
 * 두 소스가 공통으로 지키는 계약:
 *   start(onFrame, onStatus)   프레임이 올 때마다 onFrame(frame)
 *   pause() / resume() / setSpeed(x)
 *   stop()
 *   .live                      라이브인가 (파라미터를 바꿀 수 있는가)
 */

/** 라이브 스트림 — 서버가 시뮬레이션을 굴리며 밀어 넣는다. */
export class LiveSource {
  constructor(url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`) {
    this.url = url;
    this.live = true;
    this.label = "라이브";
    this.ws = null;
    this.hello = null;
  }

  /** 연결이 서면 resolve. 실패하면 reject — 호출자가 녹화본으로 넘어간다. */
  start(onFrame, onStatus) {
    return new Promise((resolve, reject) => {
      let opened = false;
      const ws = new WebSocket(this.url);
      this.ws = ws;

      const fail = (why) => {
        onStatus?.({ connected: false, live: true, text: why });
        if (!opened) reject(new Error(why));
      };

      ws.onopen = () => {
        opened = true;
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "hello") {
          this.hello = msg;
          onStatus?.({ connected: true, live: true, text: "라이브 스트리밍" });
          resolve(this);
        } else if (msg.type === "frame") {
          onFrame(msg);
        }
      };
      ws.onerror = () => fail("서버에 연결할 수 없음");
      ws.onclose = () => fail("서버 연결 끊김");

      // 서버가 열려는 있는데 hello 를 안 보내는 경우까지 대비한다
      setTimeout(() => { if (!this.hello) fail("서버 응답 없음"); }, 2500);
    });
  }

  send(msg) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  pause() { this.send({ cmd: "pause" }); }
  resume() { this.send({ cmd: "resume" }); }
  setSpeed(v) { this.send({ cmd: "speed", value: v }); }
  reset(params) { this.send({ cmd: "reset", ...params }); }

  stop() {
    this.ws?.close();
    this.ws = null;
  }
}

/** 녹화본 재생 — 서버 없이도 돌아간다. */
export class TraceSource {
  constructor(url, meta = {}) {
    this.url = url;
    this.meta = meta;
    this.live = false;
    this.label = `녹화본 ${meta.id ?? ""}`.trim();

    this.frames = [];
    this.index = 0;
    this.speed = 1.0;
    this.paused = false;
    this._timer = null;
    this._onFrame = null;
  }

  async start(onFrame, onStatus) {
    const text = await fetch(this.url).then((r) => {
      if (!r.ok) throw new Error(`녹화본을 읽을 수 없습니다 (${r.status})`);
      return r.text();
    });

    // 잘린 마지막 줄은 조용히 버린다 — 녹화 도중 중단된 파일도 거기까지는 재생된다
    this.frames = [];
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      try { this.frames.push(JSON.parse(line)); } catch { break; }
    }
    if (!this.frames.length) throw new Error("녹화본이 비어 있습니다");

    this._onFrame = onFrame;
    onStatus?.({
      connected: true, live: false,
      text: `녹화본 재생 · ${this.frames.length}프레임`,
    });

    this.index = 0;
    this._tick();
    return this;
  }

  /**
   * 프레임의 타임스탬프 간격을 지켜 재생한다.
   *
   * 고정 간격 타이머를 쓰지 않는 이유: 녹화본은 틱을 솎아내서 저장하므로
   * (`trace_writer.record(stride=…)`) 프레임 간격이 파일마다 다르다.
   * 저장된 `t` 를 그대로 존중해야 실제 속도로 재생된다.
   */
  _tick() {
    if (this.index >= this.frames.length) {
      this.index = 0;                       // 발표 중에 끊기지 않도록 반복 재생
    }
    const frame = this.frames[this.index];
    this._onFrame?.(frame);

    const next = this.frames[this.index + 1];
    const gap = next ? Math.max(0.01, next.t - frame.t) : 0.2;
    this.index++;

    this._timer = setTimeout(
      () => { if (!this.paused) this._tick(); },
      (gap * 1000) / this.speed
    );
  }

  pause() { this.paused = true; }
  resume() {
    if (!this.paused) return;
    this.paused = false;
    this._tick();
  }
  setSpeed(v) { this.speed = Math.max(0.1, v); }
  reset() { this.index = 0; }

  stop() {
    clearTimeout(this._timer);
    this._timer = null;
  }
}

/**
 * 라이브를 먼저 시도하고, 안 되면 녹화본으로 넘어간다.
 *
 * 이 순서가 발표 시나리오 그대로다 — 평소엔 라이브로 파라미터를 바꿔 보여주고,
 * 사고가 나면 녹화본이 자동으로 받는다.
 */
export async function connectBestSource(onFrame, onStatus) {
  try {
    const live = new LiveSource();
    await live.start(onFrame, onStatus);
    return live;
  } catch {
    // 라이브 실패 — 녹화본을 찾는다
  }

  let traces = [];
  try {
    traces = (await fetch("/api/traces").then((r) => r.json())).traces ?? [];
  } catch {
    traces = [];
  }
  if (!traces.length) {
    onStatus?.({ connected: false, live: false, text: "재생할 것이 없습니다" });
    return null;
  }

  const pick = traces[traces.length - 1];
  const trace = new TraceSource(pick.path, pick);
  await trace.start(onFrame, onStatus);
  return trace;
}
