"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { ExternalLink, RotateCw, X } from "lucide-react";
import { fmt, tone, type Holding } from "@/lib/api";
import { PrivacyToggle, useFmt } from "@/lib/privacy";
import { holdingLabel, type useAgentConversation } from "@/lib/useAgent";
import { Composer, MessageList } from "./AgentMessages";
import { ChangePill } from "./ui";

type Agent = ReturnType<typeof useAgentConversation>;

/** Right-hand panel that streams the AI deep dive for one holding and takes follow-up questions. */
export default function AnalysisDrawer({ holding, agent, onClose }: { holding: Holding | null; agent: Agent; onClose: () => void }) {
  const f = useFmt();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!holding) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [holding, onClose]);

  // Follow the stream unless the reader has scrolled up.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 160) el.scrollTop = el.scrollHeight;
  }, [agent.messages, agent.status]);

  if (!holding) return null;
  const started = agent.messages.some((m) => m.role === "assistant" && (m.text || m.tools.length));

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-slate-900/20 backdrop-blur-[1px]" onClick={onClose} />
      <aside
        role="dialog"
        aria-label={`Analysis of ${holdingLabel(holding)}`}
        className="absolute inset-y-0 right-0 w-full sm:w-[620px] xl:w-[720px] bg-surface border-l border-border shadow-[var(--shadow-pop)] flex flex-col animate-[drawer-in_220ms_ease-out]"
      >
        <header className="px-5 py-4 border-b border-border">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <span className="size-10 shrink-0 rounded-xl bg-accent-soft text-accent grid place-items-center text-xs font-semibold">
                {holding.kind === "mf" ? "MF" : holding.tradingsymbol.slice(0, 2)}
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h2 className="font-semibold text-lg tracking-tight truncate">{holdingLabel(holding)}</h2>
                  <span className="pill pill-neutral">{holding.kind === "mf" ? "Fund" : holding.exchange}</span>
                </div>
                <div className="flex items-center gap-2 text-sm text-muted">
                  <span className="tabular-nums">{fmt.price(holding.last_price)}</span>
                  <ChangePill value={holding.day_change_percentage}>{fmt.pct(holding.day_change_percentage)}</ChangePill>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <button
                onClick={() => agent.analyze(holding)}
                className="btn btn-ghost btn-sm"
                title={agent.streaming ? "Stop this analysis and start again" : "Run the analysis again"}
              >
                <RotateCw className="size-4" />
              </button>
              {agent.conversationId && (
                <Link href={`/agent?c=${agent.conversationId}`} className="btn btn-ghost btn-sm" title="Open in Analyst">
                  <ExternalLink className="size-4" />
                </Link>
              )}
              <PrivacyToggle />
              <button onClick={onClose} className="btn btn-ghost btn-sm" aria-label="Close">
                <X className="size-4" />
              </button>
            </div>
          </div>
          <dl className="grid grid-cols-4 gap-3 mt-4 text-xs">
            {[
              ["Value", f.inr(holding.current_value), ""],
              ["Avg cost", fmt.price(holding.average_price), ""],
              ["P&L", `${f.signedInr(holding.pnl)}`, tone(holding.pnl)],
              ["Weight", holding.weight != null ? `${holding.weight.toFixed(1)}%` : "—", ""],
            ].map(([label, value, cls]) => (
              <div key={label} className="rounded-lg bg-surface-2 border border-border px-2.5 py-2">
                <dt className="text-muted">{label}</dt>
                <dd className={`font-semibold tabular-nums text-sm mt-0.5 truncate ${cls}`}>{value}</dd>
              </div>
            ))}
          </dl>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-5">
          {!started && agent.streaming && (
            <div className="rounded-xl border border-dashed border-border-strong p-4 mb-5 text-sm text-muted">
              Research agents are working in parallel on {holdingLabel(holding)}: company news and fundamentals,
              price action and technicals, and macro or geopolitical drivers. A strategy agent then writes the
              verdict and a risk agent checks it against your limits and the sources. This usually takes three to
              five minutes.
            </div>
          )}
          <MessageList messages={agent.messages} streaming={agent.streaming} status={agent.status} hideFirstUser />
        </div>

        <footer className="border-t border-border p-4">
          <Composer
            onSend={agent.send}
            onStop={agent.cancel}
            disabled={agent.streaming}
            placeholder={agent.streaming ? "Analysis in progress…" : `Ask a follow-up about ${holdingLabel(holding)}…`}
          />
          <p className="text-[0.7rem] text-subtle mt-2 text-center">
            AI analysis, not financial advice.
            {agent.cost > 0 && <span className="tabular-nums"> · Est. API cost ${agent.cost.toFixed(2)}</span>}
          </p>
        </footer>
      </aside>
    </div>
  );
}
