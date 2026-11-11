import { Check, Square, Wrench } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
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
  onSend: (prompt: string) => void;
  onStop: () => void;
}

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

function BlockView({ block }: { block: ChatBlock }) {
  switch (block.kind) {
    case "user":
      return (
        <div className="flex justify-end">
          <div className="max-w-[82%] whitespace-pre-wrap rounded-md border border-zinc-700/70 bg-zinc-800/55 px-3 py-2 text-[13px] leading-6 text-zinc-100">
            {block.text}
          </div>
        </div>
      );
    case "text":
      return (
        <div className="border-l-2 border-zinc-800 pl-3">
          <div className={`chat-markdown ${MARKDOWN_CLASS}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
              {block.text}
            </ReactMarkdown>
          </div>
        </div>
      );
    case "thinking":
      return <div className="pl-3 text-[10px] text-zinc-600">Agent 正在思考…</div>;
    case "tool":
      return (
        <div className="flex min-w-0 items-center gap-1.5 pl-3 font-mono text-[11px] text-zinc-600">
          <Wrench className="size-3 shrink-0 text-zinc-600" />
          <span className="shrink-0 text-zinc-500">{block.name}</span>
          <span className="truncate" title={block.input}>
            {block.input}
          </span>
        </div>
      );
    case "tool_result":
      return (
        <div className="flex items-center gap-1.5 pl-3 text-[10px] text-zinc-700">
          <Check className="size-3" />
          {block.text}
        </div>
      );
    case "meta":
      return (
        <div className="my-1 flex items-center gap-2 text-[10px] text-zinc-600">
          <div className="h-px flex-1 bg-zinc-800/70" />
          <span className="shrink-0">{block.text}</span>
          <div className="h-px flex-1 bg-zinc-800/70" />
        </div>
      );
    case "error":
      return (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {block.text}
        </div>
      );
  }
}

/**
 * 受控聊天面板：历史由 App 按 role 维护，这里只负责输入与渲染。
 * 发送只由用户事件触发，不做任何自动请求。
 */
export function ChatPanel({ role, entries, busy, inheritHint, onSend, onStop }: ChatPanelProps) {
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

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col border-x border-zinc-800 bg-zinc-950">
      <header className="shrink-0 border-b border-zinc-800 px-4 py-2">
        <div className="flex items-baseline justify-between gap-3">
          <div className="flex items-center gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">Chat</h2>
            {role ? (
              <span className="rounded border border-sky-900/70 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] text-sky-400">
                {role}
              </span>
            ) : null}
          </div>
          <span className="truncate font-mono text-[10px] text-zinc-600">
            {role ? `独立会话 · ${role}` : "未选择角色"}
          </span>
        </div>
        <div className="mt-0.5 text-[10px] text-zinc-600">
          Enter 发送 · Shift+Enter 换行 · 每个角色独立 session
        </div>
        {inheritHint ? (
          <div className="mt-0.5 text-[10px] text-sky-500/80">{inheritHint}</div>
        ) : null}
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {!role ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
            <div className="text-sm text-zinc-400">请先在左侧选择角色</div>
            <div className="text-xs text-zinc-600">点击 Agents 列表中的一行开始独立对话</div>
          </div>
        ) : entries.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
            <div className="text-sm text-zinc-400">与 {role} 开始对话</div>
            <div className="text-xs text-zinc-600">
              思考过程只显示一行提示；工具调用只保留灰色状态行
            </div>
          </div>
        ) : (
          entries.map((entry) => <BlockView key={entry.id} block={entry.block} />)
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
