#!/usr/bin/env node
// Tauri CLI 包装：把 Cargo 构建目录固定到 <项目根>/.claude/tauri-target。
// 背景：项目树可能带 Windows Low 完整性标签（沙箱写入导致），构建产物继承 Low 后
// 无法写 %LOCALAPPDATA%（Tauri/WebView2 初始化报 os error 5）。.claude 目录不带该标签。
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const experimentDir = path.resolve(scriptDir, "..");
const projectRoot = path.resolve(experimentDir, "..", "..");
const targetDir = path.join(projectRoot, ".claude", "tauri-target");

mkdirSync(targetDir, { recursive: true });

const child = spawn("tauri", process.argv.slice(2), {
  stdio: "inherit",
  shell: true,
  env: {
    ...process.env,
    CARGO_TARGET_DIR: targetDir,
    VITE_AGENT_SENTINEL_ROOT: process.env.VITE_AGENT_SENTINEL_ROOT ?? projectRoot,
  },
});

child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  process.exit(code ?? 1);
});
