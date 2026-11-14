import { useCallback, useState } from "react";

import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Slider } from "@/components/ui/slider";
import type { TokenUsage } from "@/lib/types";

interface BudgetPopoverProps {
  contextTokens: number;
  usage: TokenUsage;
  budgetTokens: number;
  onBudgetChange: (tokens: number) => void;
}

const MIN_BUDGET = 64_000;
const MAX_BUDGET = 2_000_000;
const PRESETS = [128_000, 200_000, 500_000, 1_000_000, 2_000_000];

function clampBudget(value: number): number {
  if (!Number.isFinite(value)) return MIN_BUDGET;
  return Math.max(MIN_BUDGET, Math.min(MAX_BUDGET, Math.round(value)));
}

function formatBudget(value: number): string {
  if (value >= 1_000_000) {
    const millions = value / 1_000_000;
    return `${Number.isInteger(millions) ? millions : millions.toFixed(1)}M`;
  }
  if (value >= 1_000) return `${Math.round(value / 1_000)}k`;
  return String(value);
}

const STATS: Array<{ label: string; key: keyof TokenUsage }> = [
  { label: "累计输入", key: "input_tokens" },
  { label: "累计输出", key: "output_tokens" },
  { label: "缓存读", key: "cache_read_input_tokens" },
  { label: "缓存写", key: "cache_creation_input_tokens" },
];

/** CHAT 头部 Token 进度条：点击弹出预算编辑器。 */
export function BudgetPopover({
  contextTokens,
  usage,
  budgetTokens,
  onBudgetChange,
}: BudgetPopoverProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(clampBudget(budgetTokens || 200_000));
  const [input, setInput] = useState(String(clampBudget(budgetTokens || 200_000)));
  const [dirty, setDirty] = useState(false);

  const ratio = budgetTokens > 0 ? contextTokens / budgetTokens : 0;
  const barColor =
    ratio >= 0.85 ? "bg-red-500/80" : ratio >= 0.6 ? "bg-amber-400/80" : "bg-sky-500/70";

  const commit = useCallback(
    (close = false) => {
      const value = clampBudget(draft);
      if (dirty && value !== budgetTokens) onBudgetChange(value);
      setDirty(false);
      if (close) setOpen(false);
    },
    [budgetTokens, dirty, draft, onBudgetChange],
  );

  const setValue = (value: number) => {
    const clamped = clampBudget(value);
    setDraft(clamped);
    setInput(String(clamped));
    setDirty(true);
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) {
          const current = clampBudget(budgetTokens || 200_000);
          setDraft(current);
          setInput(String(current));
          setDirty(false);
          setOpen(true);
        } else {
          commit();
          setOpen(false);
        }
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="编辑上下文预算"
          className="flex shrink-0 cursor-pointer items-center gap-1.5 rounded px-1 py-0.5 transition-colors hover:bg-white/[0.04]"
        >
          {budgetTokens > 0 ? (
            <div className="h-1 w-16 overflow-hidden rounded-full bg-white/10">
              <div
                className={`h-full transition-[width] ${barColor}`}
                style={{ width: `${Math.min(100, ratio * 100)}%` }}
              />
            </div>
          ) : null}
          <span className="font-mono text-[9px] text-zinc-500">
            {formatBudget(contextTokens)} / {formatBudget(budgetTokens || 200_000)}
          </span>
        </button>
      </PopoverTrigger>

      <PopoverContent
        align="start"
        className="w-80 border-white/10 bg-zinc-950/95 p-4 shadow-[0_16px_40px_-20px_rgba(0,0,0,0.95)]"
      >
        <div className="space-y-5">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] font-medium text-zinc-400">上下文预算</span>
            <span className="font-mono text-[10px] text-zinc-600">
              已用 {formatBudget(contextTokens)}
            </span>
          </div>

          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[24px] font-semibold leading-none tracking-tight text-zinc-50">
              {formatBudget(draft)}
            </span>
            <span className="font-mono text-[12px] tracking-wide text-zinc-500">tokens</span>
          </div>

          <Slider
            min={MIN_BUDGET}
            max={MAX_BUDGET}
            step={1_000}
            value={[draft]}
            onValueChange={(values) => setValue(values[0] ?? MIN_BUDGET)}
          />

          <div className="flex flex-wrap gap-2.5">
            {PRESETS.map((preset) => {
              const selected = draft === preset;
              return (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setValue(preset)}
                  className={`cursor-pointer rounded-md border px-2 py-1 font-mono text-[11px] transition-all duration-150 ${
                    selected
                      ? "border-sky-500/30 bg-sky-500/10 text-sky-200 shadow-[0_0_12px_rgba(56,189,248,0.10)]"
                      : "border-white/10 bg-transparent text-zinc-500 hover:border-white/20 hover:text-zinc-300"
                  }`}
                >
                  {formatBudget(preset)}
                </button>
              );
            })}
          </div>

          <div className="space-y-2">
            <label
              htmlFor="budget-input"
              className="text-[11px] font-medium text-zinc-400"
            >
              自定义
            </label>
            <div className="flex items-center gap-2">
              <Input
                id="budget-input"
                aria-label="上下文预算 tokens"
                type="number"
                min={MIN_BUDGET}
                max={MAX_BUDGET}
                step={1_000}
                value={input}
                className="h-9 appearance-none rounded-md border border-white/10 bg-zinc-900/60 px-3 font-mono text-[13px] text-zinc-100 outline-none transition-colors focus:border-sky-500/40 focus:ring-1 focus:ring-sky-500/20 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                onChange={(event) => {
                  setInput(event.target.value);
                  const parsed = Number(event.target.value);
                  if (Number.isFinite(parsed)) {
                    setDraft(clampBudget(parsed));
                    setDirty(true);
                  }
                }}
                onBlur={() => commit()}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    commit(true);
                  }
                }}
              />
              <span className="font-mono text-[11px] text-zinc-600">tokens</span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            {STATS.map((stat) => (
              <div
                key={stat.key}
                className="rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-2"
              >
                <div className="text-[10px] text-zinc-500/70">{stat.label}</div>
                <div className="mt-0.5 font-mono text-[12px] text-zinc-300/80">
                  {usage[stat.key].toLocaleString()}
                </div>
              </div>
            ))}
          </div>

          <p className="text-[10px] leading-4 text-zinc-600">
            拖动滑块或输入数字；失焦或 Enter 生效
          </p>
        </div>
      </PopoverContent>
    </Popover>
  );
}
