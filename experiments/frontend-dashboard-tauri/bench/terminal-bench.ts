import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

declare global {
  interface Window {
    __benchResult?: Record<string, unknown>;
  }
}

const params = new URLSearchParams(window.location.search);
const renderer = params.get("renderer") === "webgl" ? "webgl" : "default";
const batchMs = Math.max(0, Number(params.get("batch") ?? 0));
const hidden = params.get("hidden") === "1";
const container = document.getElementById("term");
if (!container) throw new Error("no container");

const term = new Terminal({
  fontFamily: "monospace",
  fontSize: 12,
  lineHeight: 1.2,
  scrollback: 5000,
  convertEol: false,
});
const fit = new FitAddon();
term.loadAddon(fit);
term.open(container);
fit.fit();
if (hidden) {
  // 复刻工作台“终端常驻但 Tab 隐藏”：visibility 保留布局尺寸，xterm 继续解析。
  container.style.visibility = "hidden";
}

let webglOk = false;
if (renderer === "webgl") {
  try {
    term.loadAddon(new WebglAddon());
    webglOk = true;
  } catch {
    webglOk = false;
  }
}

const LINE = "\x1b[32m0123456789abcdef\x1b[0m benchmark line with \x1b[36mcolor\x1b[0m\r\n";
const chunk = LINE.repeat(100);
const totalBytes = 4_000_000;
const started = performance.now();
let frames = 0;
let done = false;

const tick = () => {
  frames += 1;
  if (!done) requestAnimationFrame(tick);
};
requestAnimationFrame(tick);

let written = 0;
const finish = () => {
  done = true;
  const elapsed = performance.now() - started;
  window.__benchResult = {
    renderer,
    batchMs,
    webglOk,
    bytes: written,
    ms: Math.round(elapsed),
    fps: Number((frames / (elapsed / 1000)).toFixed(1)),
  };
};

let pending = "";
let finished = false;
const flush = (callback?: () => void) => {
  const text = pending;
  pending = "";
  if (text) term.write(text, callback);
  else callback?.();
};
const interval = batchMs > 0 ? setInterval(() => flush(), batchMs) : null;

const step = () => {
  if (finished) return;
  const remaining = totalBytes - written;
  if (remaining <= 0) {
    finished = true;
    if (interval) clearInterval(interval);
    if (batchMs > 0) flush(finish);
    else finish();
    return;
  }
  const piece = remaining <= chunk.length ? chunk.slice(0, remaining) : chunk;
  written += piece.length;
  if (batchMs > 0) {
    pending += piece;
    setTimeout(step, 0);
    return;
  }
  term.write(piece, remaining <= chunk.length ? finish : undefined);
  if (remaining > chunk.length) setTimeout(step, 0);
};

step();
