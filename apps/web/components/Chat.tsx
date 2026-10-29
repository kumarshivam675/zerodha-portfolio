"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Briefcase, History, Loader2, MessageSquare, Newspaper, Plus, Scale, Sparkles, Trash2 } from "lucide-react";
import { api, type Conversation } from "@/lib/api";
import { useAgentConversation } from "@/lib/useAgent";
import { Composer, MessageList } from "./AgentMessages";
import { useToast } from "./ui";
import { PrivacyToggle } from "@/lib/privacy";

const SUGGESTIONS = [
  { icon: Briefcase, title: "Portfolio health check", prompt: "Summarise my portfolio: allocation, concentration risk and biggest winners/losers." },
  { icon: Newspaper, title: "News on my top holdings", prompt: "Any material news this week on my top 5 holdings?" },
  { icon: History, title: "What changed", prompt: "How has my portfolio changed since my earliest snapshot?" },
  { icon: Scale, title: "Valuation check", prompt: "Which holdings look expensive on valuation versus their sector? Should I trim anything?" },
];

export default function Chat() {
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  const refreshList = useCallback(
    () => api<Conversation[]>("/agent/conversations").then(setConversations, (e) => toast("error", e.message)),
    [toast],
  );
  const onError = useCallback((m: string) => toast("error", m), [toast]);
  const agent = useAgentConversation({ onError, onTurnComplete: refreshList });
  const { load, reset } = agent;

  useEffect(() => {
    refreshList();
  }, [refreshList]);

  // Deep link from the analysis panel: /agent?c=<conversation id>
  const linked = params.get("c");
  useEffect(() => {
    if (linked) load(linked);
  }, [linked, load]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [agent.messages, agent.status]);

  function open(id: string) {
    if (agent.streaming) return;
    router.replace(`/agent?c=${id}`);
  }

  function newChat() {
    if (agent.streaming) return;
    reset();
    router.replace("/agent");
  }

  async function remove(id: string) {
    await api(`/agent/conversations/${id}`, { method: "DELETE" });
    setConversations((cs) => cs.filter((c) => c.id !== id));
    if (id === agent.conversationId) newChat();
  }

  async function send(text: string) {
    const isNew = !agent.conversationId;
    await agent.send(text);
    if (isNew) refreshList();
  }

  const empty = agent.messages.length === 0;

  return (
    <div className="grid lg:grid-cols-[260px_1fr] gap-6 h-[calc(100dvh-7.5rem)] lg:h-[calc(100dvh-4rem)]">
      <aside className="hidden lg:flex flex-col card p-3 min-h-0">
        <button onClick={newChat} disabled={agent.streaming} className="btn btn-primary w-full mb-3">
          <Plus className="size-4" /> New chat
        </button>
        <div className="text-xs font-medium text-subtle uppercase tracking-wide px-2 mb-1">History</div>
        <div className="flex-1 overflow-y-auto -mx-1 px-1 space-y-0.5">
          {conversations.length === 0 && <p className="text-sm text-muted px-2 py-2">No conversations yet.</p>}
          {conversations.map((c) => (
            <div
              key={c.id}
              className={`group flex items-center rounded-lg text-sm transition-colors ${
                c.id === agent.conversationId ? "bg-accent-soft text-accent" : "hover:bg-background"
              }`}
            >
              <button onClick={() => open(c.id)} className="flex-1 min-w-0 flex items-center gap-2 text-left px-2.5 py-2" title={c.title}>
                {c.running ? (
                  <Loader2 className="size-4 shrink-0 animate-spin" />
                ) : c.title.startsWith("Analysis:") ? (
                  <Sparkles className="size-4 shrink-0 opacity-60" />
                ) : (
                  <MessageSquare className="size-4 shrink-0 opacity-60" />
                )}
                <span className="truncate">{c.title}</span>
              </button>
              <button
                onClick={() => remove(c.id)}
                className="p-2 text-subtle opacity-0 group-hover:opacity-100 hover:text-loss"
                aria-label="Delete conversation"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      </aside>

      <section className="card flex flex-col min-h-0 overflow-hidden">
        <div className="hidden lg:flex items-center justify-between gap-3 px-4 h-12 border-b border-border">
          <div className="text-sm font-medium truncate">
            {conversations.find((c) => c.id === agent.conversationId)?.title ?? "New conversation"}
          </div>
          <PrivacyToggle />
        </div>
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6">
            {empty ? (
              <div className="pt-8 sm:pt-16">
                <div className="size-12 rounded-2xl bg-accent text-white grid place-items-center shadow-sm mb-4">
                  <Sparkles className="size-6" />
                </div>
                <h1 className="text-2xl font-semibold tracking-tight">What would you like to know?</h1>
                <p className="text-muted mt-1">
                  Your analyst can read your holdings, snapshots and price history, query the database, and search the web.
                </p>
                <div className="grid sm:grid-cols-2 gap-3 mt-8">
                  {SUGGESTIONS.map(({ icon: Icon, title, prompt }) => (
                    <button
                      key={title}
                      onClick={() => send(prompt)}
                      className="group text-left rounded-xl border border-border bg-surface p-4 hover:border-accent hover:shadow-[var(--shadow-card)] transition"
                    >
                      <Icon className="size-4.5 text-accent mb-2" />
                      <div className="text-sm font-medium">{title}</div>
                      <div className="text-xs text-muted mt-0.5 line-clamp-2">{prompt}</div>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <MessageList messages={agent.messages} streaming={agent.streaming} status={agent.status} />
            )}
          </div>
        </div>

        <div className="border-t border-border bg-surface p-3 sm:p-4">
          <div className="max-w-3xl mx-auto">
            <Composer onSend={send} onStop={agent.cancel} disabled={agent.streaming} placeholder="Ask about your portfolio, a stock, or the market…" />
            <p className="text-[0.7rem] text-subtle mt-2 text-center">
              AI analysis, not financial advice. Check important numbers before you trade.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
