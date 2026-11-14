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
    webglOk,
    bytes: written,
    ms: Math.round(elapsed),
    fps: Number((frames / (elapsed / 1000)).toFixed(1)),
  };
};

const step = () => {
  const remaining = totalBytes - written;
  if (remaining <= 0) {
    finish();
    return;
  }
  if (remaining <= chunk.length) {
    written += remaining;
    term.write(chunk.slice(0, remaining), finish);
    return;
  }
  term.write(chunk);
  written += chunk.length;
  setTimeout(step, 0);
};

step();
