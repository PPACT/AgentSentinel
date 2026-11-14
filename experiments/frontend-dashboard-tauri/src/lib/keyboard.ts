/** 中心面板快捷键映射（纯函数，便于单测）。 */

export interface TabLike {
  key: string;
}

export interface ShortcutEventLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
}

/** Ctrl/⌘+Tab 循环切换；Alt+1..9 直选可见 Tab；其余按键返回 null。 */
export function shortcutTarget<T extends TabLike>(
  tabs: readonly T[],
  active: string,
  event: ShortcutEventLike,
): T | null {
  if (tabs.length === 0) return null;
  if ((event.ctrlKey || event.metaKey) && event.key === "Tab") {
    const index = Math.max(
      0,
      tabs.findIndex((tab) => tab.key === active),
    );
    const step = event.shiftKey ? -1 : 1;
    return tabs[(index + step + tabs.length) % tabs.length] ?? null;
  }
  if (event.altKey && !event.ctrlKey && !event.metaKey && /^[1-9]$/.test(event.key)) {
    return tabs[Number(event.key) - 1] ?? null;
  }
  return null;
}

/** Tab 栏内 ←/→/Home/End 导航。 */
export function arrowTarget<T extends TabLike>(
  tabs: readonly T[],
  active: string,
  key: string,
): T | null {
  if (tabs.length === 0) return null;
  const index = Math.max(
    0,
    tabs.findIndex((tab) => tab.key === active),
  );
  if (key === "Home") return tabs[0] ?? null;
  if (key === "End") return tabs[tabs.length - 1] ?? null;
  if (key === "ArrowLeft") return tabs[(index - 1 + tabs.length) % tabs.length] ?? null;
  if (key === "ArrowRight") return tabs[(index + 1) % tabs.length] ?? null;
  return null;
}
