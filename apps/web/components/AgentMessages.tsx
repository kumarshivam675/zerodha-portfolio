"use client";

import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Activity,
  ArrowUp,
  BarChart3,
  Briefcase,
  ChevronRight,
  Code2,
  Database,
  FileText,
  Globe,
  History,
  LineChart,
  Sparkles,
  Wrench,
} from "lucide-react";
import type { ChatBubble } from "@/lib/api";
import { useFmt } from "@/lib/privacy";

const TOOLS: Record<string, { label: string; icon: typeof Globe }> = {
  get_portfolio: { label: "Read portfolio", icon: Briefcase },
  list_snapshots: { label: "Listed snapshots", icon: History },
  get_snapshot: { label: "Read snapshot", icon: History },
  get_price_history: { label: "Price history", icon: LineChart },
  get_technicals: { label: "Technicals", icon: Activity },
  get_stock_info: { label: "Fundamentals", icon: BarChart3 },
  query_database: { label: "SQL query", icon: Database },
  web_search: { label: "Searched", icon: Globe },
  web_fetch: { label: "Read page", icon: FileText },
  code_execution: { label: "Ran analysis code", icon: Code2 },
};

function toolChip(name: string, input: string) {
  let detail = "";
  try {
    const p = JSON.parse(input);
    detail = p.query ?? (p.url ? new URL(p.url).hostname : "") ?? "";
    detail ||= p.tradingsymbol ?? p.snapshot_date ?? "";
    if (name === "code_execution" || name === "query_database") detail = "";
  } catch {}
  const meta = TOOLS[name] ?? { label: name, icon: Wrench };
  return { ...meta, detail: String(detail).slice(0, 60) };
}

export function MessageList({
  messages,
  streaming,
  status,
  hideFirstUser = false,
}: {
  messages: ChatBubble[];
  streaming: boolean;
  status: string | null;
  hideFirstUser?: boolean;
}) {
  const f = useFmt();
  return (
    <div className="space-y-6">
      {messages.map((m, i) =>
        m.role === "user" ? (
          hideFirstUser && i === 0 ? null : (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent text-white px-4 py-2.5 text-sm whitespace-pre-wrap shadow-sm">
                {f.text(m.text)}
              </div>
            </div>
          )
        ) : (
          <div key={i} className="flex gap-3">
            <div className="size-7 shrink-0 rounded-lg bg-accent-soft text-accent grid place-items-center mt-0.5">
              <Sparkles className="size-4" />
            </div>
            <div className="min-w-0 flex-1 space-y-3">
              {(m.tools.length > 0 || (m.notes?.length ?? 0) > 0 || !!m.thinking) && (
                <ResearchSteps bubble={m} live={streaming && i === messages.length - 1} />
              )}
              {m.text && (
                <div className="prose-chat text-[0.9375rem]">
                  <Markdown
                    remarkPlugins={[remarkGfm]}
                    components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}
                  >
                    {f.text(m.text)}
                  </Markdown>
                </div>
              )}
              {streaming && i === messages.length - 1 && status && <TypingStatus status={status} />}
            </div>
          </div>
        ),
      )}
    </div>
  );
}

function ResearchSteps({ bubble, live }: { bubble: ChatBubble; live: boolean }) {
  const f = useFmt();
  // Open while the agent is working so progress is visible; collapsed once the answer is in.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const open = toggled ?? live;
  const notes = bubble.notes ?? [];
  const thinking = bubble.thinking?.trim();
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (live && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [live, notes.length, thinking, bubble.tools.length]);

  return (
    <div className={`rounded-xl border ${live ? "border-accent/30 bg-accent-soft/40" : "border-border bg-surface-2"}`}>
      <button
        onClick={() => setToggled(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs text-muted hover:text-foreground"
        aria-expanded={open}
      >
        <ChevronRight className={`size-3.5 transition-transform ${open ? "rotate-90" : ""}`} />
        <span className={`font-medium ${live ? "text-accent" : ""}`}>{live ? "Researching" : "Research steps"}</span>
        <span>
          · {bubble.tools.length} tool call{bubble.tools.length === 1 ? "" : "s"}
        </span>
      </button>
      {open && (
        <div ref={listRef} className={`px-3 pb-3 space-y-3 ${live ? "max-h-80 overflow-y-auto" : ""}`}>
          {(notes.length > 0 || thinking) && (
            <ol className="space-y-2 text-xs text-muted list-decimal pl-4 marker:text-subtle">
              {notes.map((n, k) => (
                <li key={k} className="whitespace-pre-wrap">
                  {f.text(n)}
                </li>
              ))}
              {thinking && <li className="whitespace-pre-wrap text-foreground/70">{f.text(thinking)}</li>}
            </ol>
          )}
          {bubble.tools.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {bubble.tools.map((t, j) => {
                const chip = toolChip(t.name, t.input);
                const Icon = chip.icon;
                return (
                  <span
                    key={j}
                    className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-1 text-xs text-muted max-w-full"
                    title={t.input}
                  >
                    <Icon className="size-3.5 shrink-0" />
                    <span className="font-medium text-foreground/80">{chip.label}</span>
                    {chip.detail && <span className="truncate">{chip.detail}</span>}
                  </span>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function TypingStatus({ status }: { status: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted">
      <span className="flex gap-1">
        <span className="size-1.5 rounded-full bg-accent animate-bounce [animation-delay:-0.3s]" />
        <span className="size-1.5 rounded-full bg-accent animate-bounce [animation-delay:-0.15s]" />
        <span className="size-1.5 rounded-full bg-accent animate-bounce" />
      </span>
      {status}…
    </div>
  );
}

export function Composer({
  onSend,
  disabled,
  placeholder,
  autoFocus,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
  placeholder: string;
  autoFocus?: boolean;
}) {
  const [input, setInput] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  function submit() {
    if (disabled || !input.trim()) return;
    onSend(input);
    setInput("");
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="flex items-end gap-2 rounded-2xl border border-border-strong bg-surface px-3 py-2 focus-within:border-accent focus-within:shadow-[0_0_0_3px_var(--accent-soft)] transition"
    >
      <textarea
        ref={ref}
        value={input}
        autoFocus={autoFocus}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        rows={1}
        placeholder={placeholder}
        className="flex-1 resize-none bg-transparent py-1.5 text-sm outline-none placeholder:text-subtle"
      />
      <button
        type="submit"
        disabled={disabled || !input.trim()}
        className="size-8 shrink-0 rounded-lg bg-accent text-white grid place-items-center disabled:opacity-40 hover:bg-accent-hover transition"
        aria-label="Send"
      >
        <ArrowUp className="size-4" />
      </button>
    </form>
  );
}
