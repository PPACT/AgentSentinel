import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { startChatStream } from "@/lib/backend";
import { consumeChatStream, type ChatBlock, type ChatTurnSummary } from "@/lib/chat";
import { errorMessage } from "@/lib/format";

interface ChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface Entry {
  id: number;
  block: ChatBlock;
}

function BlockView({ block }: { block: ChatBlock }) {
  switch (block.kind) {
    case "user":
      return (
        <div className="ml-auto max-w-[85%] whitespace-pre-wrap rounded-lg bg-sky-950/70 px-3 py-2 text-zinc-100">
          {block.text}
        </div>
      );
    case "text":
      return (
        <div className="whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-zinc-100">
          {block.text}
        </div>
      );
    case "thinking":
      return (
        <details className="rounded-lg border-l-2 border-zinc-700 bg-zinc-900/30 px-3 py-1.5 text-xs text-zinc-400">
          <summary className="cursor-pointer select-none">思考过程</summary>
          <div className="mt-1 whitespace-pre-wrap">{block.text}</div>
        </details>
      );
    case "tool":
      return (
        <div className="rounded-lg border-l-2 border-emerald-600 bg-emerald-950/30 px-3 py-1.5 font-mono text-xs text-emerald-300">
          ⚙️ {block.name} {block.input}
        </div>
      );
    case "tool_result":
      return <div className="font-mono text-xs text-zinc-500">✅ {block.text}</div>;
    case "meta":
      return <div className="text-center text-[11px] text-zinc-600">{block.text}</div>;
    case "error":
      return (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-300">
          {block.text}
        </div>
      );
  }
}

/** 最小聊天面板：POST /chat + SSE，thinking 折叠、tool 状态条、文本原样渲染。 */
export function ChatDialog({ open, onOpenChange }: ChatDialogProps) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const nextId = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const append = useCallback((blocks: ChatBlock[]) => {
    setEntries((previous) => [
      ...previous,
      ...blocks.map((block) => ({ id: nextId.current++, block })),
    ]);
  }, []);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [entries]);

  useEffect(() => {
    if (!open) abortRef.current?.abort();
  }, [open]);

  const send = useCallback(async () => {
    const prompt = input.trim();
    if (!prompt || busy) return;
    append([{ kind: "user", text: prompt }]);
    setInput("");
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await startChatStream({ prompt, sessionId, signal: controller.signal });
      await consumeChatStream(response, {
        onBlocks: append,
        onEnd: (summary: ChatTurnSummary) => {
          if (summary.session_id) setSessionId(summary.session_id);
        },
      });
    } catch (err) {
      if (!controller.signal.aborted) {
        const detail = errorMessage(err);
        setError(detail);
        append([{ kind: "error", text: detail }]);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
    }
  }, [append, busy, input, sessionId]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    append([{ kind: "meta", text: "已请求停止本轮" }]);
  }, [append]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[85vh] flex-col border-zinc-800 bg-zinc-950 text-zinc-200 sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-sm">对话（PoC）</DialogTitle>
          <DialogDescription className="font-mono text-xs">
            session: {sessionId ?? "(新会话)"} · 纯文本渲染，Markdown 待依赖审批
          </DialogDescription>
        </DialogHeader>
        <div ref={scrollRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {entries.length === 0 ? (
            <div className="py-10 text-center text-sm text-zinc-600">
              输入内容开始对话；每轮由 daemon 拉起独立 claude 进程
            </div>
          ) : (
            entries.map((entry) => <BlockView key={entry.id} block={entry.block} />)
          )}
        </div>
        {error ? <div className="text-xs text-red-400">{error}</div> : null}
        <div className="flex shrink-0 items-end gap-2 border-t border-zinc-800 pt-3">
          <textarea
            className="h-16 flex-1 resize-none rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-sky-600"
            placeholder="输入内容；Enter 发送，Shift+Enter 换行"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
          />
          <div className="flex flex-col gap-2">
            <Button size="sm" disabled={busy || !input.trim()} onClick={() => void send()}>
              {busy ? "流式中…" : "发送"}
            </Button>
            <Button variant="outline" size="sm" disabled={!busy} onClick={stop}>
              停止
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
