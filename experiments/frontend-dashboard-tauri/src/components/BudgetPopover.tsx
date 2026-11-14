import { useCallback, useState } from "react";

import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Slider } from "@/components/ui/slider";
import type { ContextBreakdown } from "@/lib/context";
import type { TokenUsage } from "@/lib/types";

interface BudgetPopoverProps {
  contextTokens: number;
  usage: TokenUsage;
  budgetTokens: number;
  contextBreakdown?: ContextBreakdown | null;
  contextDelta?: number;
  contextDeltaSeq?: number;
  onBudgetChange: (tokens: number) => void;
}

const MIN_BUDGET = 64_000;
const MAX_BUDGET = 2_000_000;
const DEFAULT_BUDGET = 200_000;
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

interface BreakdownRowProps {
  color: string;
  label: string;
  hint?: string;
  value: number;
  budget: number;
}

function BreakdownRow({ color, label, hint, value, budget }: BreakdownRowProps) {
  const ratio = budget > 0 ? Math.round((value / budget) * 100) : 0;
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className={`size-1.5 shrink-0 rounded-full ${color}`} />
      <span className="text-zinc-400">{label}</span>
      {hint ? (
        <span className="rounded border border-white/10 px-1 text-[9px] leading-4 text-zinc-600">
          {hint}
        </span>
      ) : null}
      <span className="ml-auto font-mono text-zinc-300">{formatBudget(value)}</span>
      <span className="w-8 text-right font-mono text-[9px] text-zinc-600">{ratio}%</span>
    </div>
  );
}

/** CHAT 头部 Token 进度条：本地 usage 驱动，点击弹出预算与上下文分段明细。 */
export function BudgetPopover({
  contextTokens,
  usage,
  budgetTokens,
  contextBreakdown = null,
  contextDelta = 0,
  contextDeltaSeq = 0,
  onBudgetChange,
}: BudgetPopoverProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(clampBudget(budgetTokens || DEFAULT_BUDGET));
  const [input, setInput] = useState(String(clampBudget(budgetTokens || DEFAULT_BUDGET)));
  const [dirty, setDirty] = useState(false);

  const budget = budgetTokens > 0 ? budgetTokens : DEFAULT_BUDGET;
  const used = Math.max(0, Math.min(contextTokens, budget));
  const systemTools = contextBreakdown
    ? Math.min(contextBreakdown.systemTools, used)
    : used;
  const messages = contextBreakdown
    ? Math.max(0, Math.min(contextBreakdown.messages, used - systemTools))
    : 0;
  const free = Math.max(0, budget - used);
  const width = (value: number) => `${budget > 0 ? (value / budget) * 100 : 0}%`;

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
          const current = clampBudget(budgetTokens || DEFAULT_BUDGET);
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
          <div className="flex h-1 w-16 overflow-hidden rounded-full bg-white/[0.07]">
            <div className="h-full bg-purple-500" style={{ width: width(systemTools) }} />
            <div className="h-full bg-purple-300/85" style={{ width: width(messages) }} />
          </div>
          <span className="relative inline-flex items-center">
            <span className="font-mono text-[9px] text-zinc-500">
              <span className="text-purple-300/90">{formatBudget(contextTokens)}</span>
              {" / "}
              {formatBudget(budget)}
            </span>
            {contextDelta > 0 ? (
              <span
                key={contextDeltaSeq}
                aria-hidden="true"
                className="token-delta pointer-events-none absolute -top-3 left-0 whitespace-nowrap font-mono text-[9px] font-medium text-purple-300"
              >
                +{formatBudget(contextDelta)}
              </span>
            ) : null}
          </span>
        </button>
      </PopoverTrigger>

      <PopoverContent
        align="start"
        className="w-[21rem] border-white/10 bg-zinc-950/95 p-4 shadow-[0_16px_40px_-20px_rgba(0,0,0,0.95)]"
      >
        <div className="space-y-5">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] font-medium text-zinc-400">上下文预算</span>
            <span className="font-mono text-[10px] text-zinc-600">
              已用 <span className="text-purple-300/90">{formatBudget(contextTokens)}</span>
            </span>
          </div>

          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[24px] font-semibold leading-none tracking-tight text-zinc-50">
              {formatBudget(draft)}
            </span>
            <span className="font-mono text-[12px] tracking-wide text-zinc-500">tokens</span>
          </div>

          <div className="space-y-2.5">
            <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-white/[0.07]">
              <div className="h-full bg-purple-500" style={{ width: width(systemTools) }} />
              <div className="h-full bg-purple-300/85" style={{ width: width(messages) }} />
            </div>
            <div className="space-y-1.5">
              <BreakdownRow
                color="bg-purple-500"
                label="系统与工具"
                hint="估算"
                value={systemTools}
                budget={budget}
              />
              <BreakdownRow
                color="bg-purple-300/85"
                label="历史消息"
                value={messages}
                budget={budget}
              />
              <BreakdownRow
                color="bg-white/15"
                label="剩余空间"
                value={free}
                budget={budget}
              />
            </div>
            <p className="text-[9px] leading-4 text-zinc-600">
              JSONL 不含系统提示与工具定义原文；底座按首轮请求估算，消息与剩余为精确值。
            </p>
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
                      ? "border-purple-500/30 bg-purple-500/10 text-purple-200 shadow-[0_0_12px_rgba(168,85,247,0.10)]"
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
                className="h-9 appearance-none rounded-md border border-white/10 bg-zinc-900/60 px-3 font-mono text-[13px] text-zinc-100 outline-none transition-colors focus:border-purple-500/40 focus:ring-1 focus:ring-purple-500/20 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
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
                <div className="mt-0.5 font-mono text-[12px] text-purple-200/75">
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
