import { Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import "highlight.js/styles/github-dark.css";

import { Button } from "@/components/ui/button";
import type { ChatBlock, ChatEntry } from "@/lib/chat";

interface ChatPanelProps {
  role: string | null;
  entries: ChatEntry[];
  busy: boolean;
  inheritHint?: string | null;
  contextWarning?: string | null;
  terminalHistory?: boolean;
  onSend: (prompt: string) => void;
  onStop: () => void;
}

type ToolBlock = Extract<ChatBlock, { kind: "tool" }>;

const MARKDOWN_CLASS = [
  "text-[13px] leading-6 text-zinc-200",
  "[&_p]:my-1",
  "[&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
  "[&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-5",
  "[&_ol]:my-1 [&_ol]:list-decimal [&_ol]:pl-5",
  "[&_li]:my-0.5",
  "[&_h1]:mb-1 [&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold",
  "[&_h2]:mb-1 [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold",
  "[&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:text-[13px] [&_h3]:font-semibold",
  "[&_a]:text-sky-400 [&_a]:underline",
  "[&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-zinc-700 [&_blockquote]:pl-3 [&_blockquote]:text-zinc-400",
  "[&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:border-zinc-800 [&_pre]:bg-zinc-950 [&_pre]:p-3 [&_pre]:text-xs",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_code]:rounded [&_code]:bg-zinc-800/80 [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[12px]",
  "[&_table]:my-2 [&_table]:border-collapse [&_table]:text-xs",
  "[&_th]:border [&_th]:border-zinc-700 [&_th]:px-2 [&_th]:py-1",
  "[&_td]:border [&_td]:border-zinc-800 [&_td]:px-2 [&_td]:py-1",
  "[&_hr]:my-3 [&_hr]:border-zinc-800",
].join(" ");

const EDIT_TOOLS = new Set(["edit", "write", "multiedit"]);

function toolGroupLabel(tools: ToolBlock[]): string {
  const bash = tools.filter((tool) => tool.name.toLowerCase() === "bash").length;
  const edits = tools.filter((tool) => EDIT_TOOLS.has(tool.name.toLowerCase())).length;
  if (bash === tools.length) return `执行了 ${bash} 条系统命令`;
  if (edits === tools.length) return `修改了 ${edits} 个文件`;
  return `执行了 ${tools.length} 个工具调用`;
}

function ToolGroup({ tools }: { tools: ToolBlock[] }) {
  return (
    <details className="group rounded-md border border-zinc-800/60 bg-zinc-900/25 px-3 py-1.5 text-[11px] text-zinc-500">
      <summary className="flex cursor-pointer select-none items-center gap-1.5 text-zinc-500">
        <span className="text-[9px] text-zinc-600 transition group-open:rotate-90">▶</span>
        {toolGroupLabel(tools)}
      </summary>
      <div className="mt-1.5 space-y-1 border-t border-zinc-800/60 pt-1.5 font-mono text-[10px] text-zinc-600">
        {tools.map((tool, index) => (
          <div key={`${index}-${tool.name}`} className="truncate" title={tool.input}>
            <span className="text-zinc-500">{tool.name}</span> {tool.input}
          </div>
        ))}
      </div>
    </details>
  );
}

function TextBlock({ block }: { block: Extract<ChatBlock, { kind: "user" | "text" | "error" }> }) {
  if (block.kind === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[78%] whitespace-pre-wrap rounded-md border border-zinc-700/60 bg-zinc-800/45 px-3 py-2 text-[13px] leading-6 text-zinc-100">
          {block.text}
        </div>
      </div>
    );
  }
  if (block.kind === "error") {
    return (
      <div className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
        {block.text}
      </div>
    );
  }
  return (
    <div className="chat-markdown max-w-[88%] py-1 text-[13px] leading-6">
      <div className={MARKDOWN_CLASS}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
          {block.text}
        </ReactMarkdown>
      </div>
    </div>
  );
}

/** 纯问答视图：只保留用户 Prompt + Markdown 结果；工具折叠为状态条，thinking 隐去。 */
export function ChatPanel({
  role,
  entries,
  busy,
  inheritHint,
  contextWarning,
  terminalHistory = false,
  onSend,
  onStop,
}: ChatPanelProps) {
  const [input, setInput] = useState("");
  const composingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [entries]);

  const submit = useCallback(() => {
    const prompt = input.trim();
    if (!prompt || !role || busy) return;
    onSend(prompt);
    setInput("");
  }, [busy, input, onSend, role]);

  const rendered: ReactNode[] = [];
  let index = 0;
  while (index < entries.length) {
    const firstBlock = entries[index].block;
    if (firstBlock.kind === "user") {
      const entry = entries[index];
      rendered.push(<TextBlock key={entry.id} block={firstBlock} />);
      index += 1;
    }
    const tools: ToolBlock[] = [];
    let finalText: ChatEntry | null = null;
    const errors: ChatEntry[] = [];
    while (index < entries.length && entries[index].block.kind !== "user") {
      const entry = entries[index];
      const block = entry.block;
      if (block.kind === "tool") {
        tools.push(block);
      } else if (block.kind === "text") {
        // 一轮里只保留最后一段文本，避免中间过程占用主视觉。
        finalText = entry;
      } else if (block.kind === "error") {
        errors.push(entry);
      }
      index += 1;
    }
    if (tools.length > 0) {
      rendered.push(<ToolGroup key={`tools-${tools[0].name}-${index}`} tools={tools} />);
    }
    if (finalText && finalText.block.kind === "text") {
      rendered.push(<TextBlock key={finalText.id} block={finalText.block} />);
    }
    for (const entry of errors) {
      if (entry.block.kind === "error") {
        rendered.push(<TextBlock key={entry.id} block={entry.block} />);
      }
    }
  }

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col border-x border-zinc-800 bg-zinc-950">
      <header className="shrink-0 border-b border-zinc-800 px-4 py-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">Chat</h2>
            {role ? (
              <span className="rounded border border-sky-900/70 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] text-sky-400">
                {role}
              </span>
            ) : null}
            {terminalHistory ? (
              <span className="rounded border border-zinc-700/70 px-1.5 py-0.5 text-[9px] text-zinc-500">
                终端历史（只读）
              </span>
            ) : null}
            {contextWarning ? (
              <span
                role="img"
                aria-label="上下文用量"
                title={contextWarning}
                className="size-2 shrink-0 cursor-help rounded-full bg-amber-400/60 hover:bg-amber-300"
              />
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {busy ? (
              <span className="flex items-center gap-1.5 text-[10px] text-zinc-500">
                <span className="size-1.5 animate-pulse rounded-full bg-sky-400" />
                思考中
              </span>
            ) : null}
            <span className="truncate font-mono text-[10px] text-zinc-600">
              {role ? `独立会话 · ${role}` : "未选择角色"}
            </span>
          </div>
        </div>
        {inheritHint ? (
          <div className="mt-0.5 truncate text-[10px] text-sky-500/70" title={inheritHint}>
            {inheritHint}
          </div>
        ) : null}
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">
        {!role ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
            <div className="text-sm text-zinc-400">请先在左侧选择角色</div>
            <div className="text-xs text-zinc-600">点击 Agents 列表中的一行开始独立对话</div>
          </div>
        ) : rendered.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
            <div className="text-sm text-zinc-400">与 {role} 开始对话</div>
            <div className="text-xs text-zinc-600">工具调用会折叠为状态条，可点击展开</div>
          </div>
        ) : (
          rendered
        )}
      </div>

      <div className="shrink-0 border-t border-zinc-800 px-3 py-2">
        <div
          className={`overflow-hidden rounded-md border bg-zinc-900/70 ${
            role ? "border-zinc-700/80 focus-within:border-sky-700/80" : "border-zinc-800/60"
          }`}
        >
          <textarea
            className="h-20 w-full resize-none bg-transparent px-3 py-2 text-[13px] leading-6 text-zinc-100 outline-none placeholder:text-zinc-600 disabled:cursor-not-allowed disabled:opacity-60"
            placeholder={role ? `向 ${role} 发起对话…` : "请先在左侧选择角色"}
            value={input}
            disabled={!role}
            onChange={(event) => setInput(event.target.value)}
            onCompositionStart={() => {
              composingRef.current = true;
            }}
            onCompositionEnd={() => {
              composingRef.current = false;
            }}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey) return;
              if (composingRef.current || event.nativeEvent.isComposing) return;
              if (event.keyCode === 229) return;
              event.preventDefault();
              submit();
            }}
          />
          <div className="flex items-center justify-between border-t border-zinc-800/70 px-2 py-1.5">
            <span className="text-[10px] text-zinc-600">Enter 发送 · Shift+Enter 换行</span>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs text-zinc-400"
                disabled={!busy}
                title="停止本轮"
                onClick={onStop}
              >
                <Square className="size-3" />
              </Button>
              <Button
                size="sm"
                className="h-7 px-3 text-xs"
                disabled={!role || busy || !input.trim()}
                onClick={submit}
              >
                {busy ? "流式中…" : "发送"}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
