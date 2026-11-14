import type { ChatBlock } from "./chat";
import type { TokenUsage } from "./types";

/** 上下文分类（token）：底座 = Claude Code 系统提示 + 内置/已加载工具。 */
export interface ContextBreakdown {
  systemTools: number;
  messages: number;
}

export interface HistoryMessageForBreakdown {
  role: string;
  blocks: ChatBlock[];
  usage?: TokenUsage;
}

const CJK_RANGES: Array<[number, number]> = [
  [0x2e80, 0x9fff], // CJK 部首、标点、汉字、假名、谚文
  [0xac00, 0xd7af], // 谚文音节
  [0xf900, 0xfaff], // CJK 兼容表意文字
  [0xff00, 0xffef], // 全角字符
];

function isCjk(code: number): boolean {
  return CJK_RANGES.some(([start, end]) => code >= start && code <= end);
}

/** 本地估算 token：CJK 按 1 字 1 token，其余按 4 字符 1 token。只用于分类展示。 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  let cjk = 0;
  let total = 0;
  for (const char of text) {
    total += 1;
    if (isCjk(char.codePointAt(0) ?? 0)) cjk += 1;
  }
  return Math.ceil(cjk + (total - cjk) / 4);
}

export function requestTokens(usage: TokenUsage | undefined): number {
  if (!usage) return 0;
  return (
    usage.input_tokens +
    usage.cache_read_input_tokens +
    usage.cache_creation_input_tokens
  );
}

export interface ContextBaseParts {
  base: number;
  firstUserTokens: number;
}

/** 首条带 usage 的 assistant 请求 = 系统 + 工具 + 首条用户消息。 */
export function contextBaseParts(
  messages: HistoryMessageForBreakdown[],
): ContextBaseParts | null {
  const firstAssistant = messages.find(
    (message) => message.role === "assistant" && requestTokens(message.usage) > 0,
  );
  const base = requestTokens(firstAssistant?.usage);
  if (base <= 0) return null;
  const firstUser = messages.find((message) => message.role === "user");
  const firstUserTokens = (firstUser?.blocks ?? []).reduce(
    (sum, block) => sum + (block.kind === "text" ? estimateTokens(block.text) : 0),
    0,
  );
  return { base, firstUserTokens };
}

export function breakdownFromBase(
  base: number,
  firstUserTokens: number,
  contextTokens: number,
): ContextBreakdown {
  return {
    systemTools: Math.max(0, base - Math.min(firstUserTokens, base)),
    messages: Math.max(0, contextTokens - base),
  };
}

/**
 * 用本地 JSONL 历史估算分类：
 * - 首条带 usage 的 assistant 请求 = 系统提示 + 工具 + 首条用户消息；
 * - 扣掉首条用户消息的估算值，得到「系统与工具」底座；
 * - 当前上下文减去底座，得到「历史消息」。
 * JSONL 没有系统提示/工具定义原文，因此无法再拆分系统与工具。
 */
export function computeContextBreakdown(
  messages: HistoryMessageForBreakdown[],
  contextTokens: number,
): ContextBreakdown | null {
  const parts = contextBaseParts(messages);
  if (parts === null || contextTokens <= 0) return null;
  return breakdownFromBase(parts.base, parts.firstUserTokens, contextTokens);
}
