import type { WildProcess } from "./types";

// psutil 把 argv 用空格拼成字符串，路径可能带空格；用边界匹配而不是取首 token。
const CLEANABLE_EXE = /(?:^|[\\/\s"])(node|python|pythonw)\.exe(?:\s|$)/i;

/**
 * 只允许清理 node.exe / python.exe / pythonw.exe 且 create_time 已知的野生进程。
 * create_time 是服务端防止 PID 复用的必填校验位。
 */
export function isCleanupCandidate(process: WildProcess): boolean {
  if (!process.cmdline) return false;
  if (typeof process.create_time !== "number" || process.create_time <= 0) return false;
  return CLEANABLE_EXE.test(process.cmdline);
}
