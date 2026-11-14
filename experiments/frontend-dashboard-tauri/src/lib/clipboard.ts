import { isTauri } from "@tauri-apps/api/core";

/** 读取系统剪贴板：Tauri 用官方插件，浏览器回退 navigator.clipboard。 */
export async function readClipboardText(): Promise<string> {
  if (isTauri()) {
    try {
      const { readText } = await import("@tauri-apps/plugin-clipboard-manager");
      return (await readText()) ?? "";
    } catch {
      // 插件未编译/权限未授予时回退浏览器剪贴板
    }
  }
  try {
    if (navigator.clipboard?.readText) {
      return (await navigator.clipboard.readText()) ?? "";
    }
  } catch {
    // 用户拒绝或非安全上下文
  }
  return "";
}
