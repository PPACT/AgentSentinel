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

/** CHAT 头部 Token 进度条：点击后在原位弹出预算编辑器（Slider + 数字输入）。 */
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
    ratio >= 0.85 ? "bg-red-500" : ratio >= 0.6 ? "bg-amber-400" : "bg-emerald-500";

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
          className="flex shrink-0 cursor-pointer items-center gap-1.5 rounded px-1 py-0.5 transition-shadow hover:ring-2 hover:ring-sky-500/50"
        >
          {budgetTokens > 0 ? (
            <div className="h-1.5 w-16 overflow-hidden rounded-full bg-zinc-800">
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
      <PopoverContent align="start" className="w-80">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-zinc-200">上下文预算</span>
            <span className="font-mono text-[10px] text-zinc-500">
              当前 {formatBudget(contextTokens)}
            </span>
          </div>
          <Slider
            min={MIN_BUDGET}
            max={MAX_BUDGET}
            step={1_000}
            value={[draft]}
            onValueChange={(values) => setValue(values[0] ?? MIN_BUDGET)}
          />
          <div className="flex flex-wrap gap-1">
            {PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => setValue(preset)}
                className={`rounded border px-1.5 py-0.5 font-mono text-[10px] transition-colors ${
                  draft === preset
                    ? "border-sky-700 bg-sky-950/40 text-sky-300"
                    : "border-zinc-700 text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200"
                }`}
              >
                {formatBudget(preset)}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <Input
              aria-label="上下文预算 tokens"
              type="number"
              min={MIN_BUDGET}
              max={MAX_BUDGET}
              step={1_000}
              value={input}
              className="h-8 font-mono text-xs"
              onChange={(event) => {
                setInput(event.target.value);
                const parsed = Number(event.target.value);
                if (Number.isFinite(parsed)) {
                  const clamped = clampBudget(parsed);
                  setDraft(clamped);
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
            <span className="text-[10px] text-zinc-500">tokens</span>
          </div>
          <div className="space-y-0.5 font-mono text-[10px] text-zinc-500">
            <div>
              累计输入 {usage.input_tokens.toLocaleString()} · 输出{" "}
              {usage.output_tokens.toLocaleString()}
            </div>
            <div>
              缓存读 {usage.cache_read_input_tokens.toLocaleString()} · 写入{" "}
              {usage.cache_creation_input_tokens.toLocaleString()}
            </div>
          </div>
          <div className="text-[10px] text-zinc-600">
            拖动滑块或输入数字；失焦或 Enter 生效
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
