import type { KeyboardEvent } from "react";

import type { CenterTabDef, CenterTabKey } from "@/lib/centerTabs";
import { arrowTarget } from "@/lib/keyboard";

interface CenterTabBarProps {
  tabs: readonly CenterTabDef[];
  active: CenterTabKey;
  onSelect: (key: CenterTabKey) => void;
}

/** 无障碍 Tab 栏：roving tabIndex + ←/→/Home/End 切换，点击后焦点留在 Tab 栏。 */
export function CenterTabBar({ tabs, active, onSelect }: CenterTabBarProps) {
  const focusTab = (key: CenterTabKey) => {
    window.setTimeout(() => {
      document.querySelector<HTMLButtonElement>(`[data-tab-key="${key}"]`)?.focus();
    }, 0);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = arrowTarget(tabs, active, event.key);
    if (!target) return;
    event.preventDefault();
    onSelect(target.key);
    focusTab(target.key);
  };

  const activeHint = tabs.find((tab) => tab.key === active)?.hint ?? "";

  return (
    <div
      role="tablist"
      aria-label="中心面板"
      onKeyDown={onKeyDown}
      className="flex h-9 shrink-0 items-center gap-1 border-x border-b border-zinc-800 bg-zinc-950 px-2"
    >
      {tabs.map((tab) => {
        const Icon = tab.icon;
        const selected = active === tab.key;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            id={`center-tab-${tab.key}`}
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            data-tab-key={tab.key}
            onClick={() => {
              onSelect(tab.key);
              focusTab(tab.key);
            }}
            className={`flex h-7 items-center gap-1.5 rounded px-3 text-[11px] transition-colors ${
              selected
                ? "bg-zinc-800 text-zinc-100"
                : "text-zinc-500 hover:text-zinc-300 focus-visible:text-zinc-200"
            }`}
          >
            <Icon className="size-3.5" />
            {tab.label}
          </button>
        );
      })}
      <span className="ml-auto pr-1 font-mono text-[10px] text-zinc-600">{activeHint}</span>
    </div>
  );
}
