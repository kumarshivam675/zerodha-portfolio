"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Search, Sparkles } from "lucide-react";
import { fmt, tone, type Holding } from "@/lib/api";
import { PrivacyToggle, useFmt } from "@/lib/privacy";
import { useAgentConversation } from "@/lib/useAgent";
import AnalysisDrawer from "./AnalysisDrawer";
import { ChangePill, useToast } from "./ui";

type Key =
  | "tradingsymbol"
  | "current_value"
  | "invested"
  | "pnl"
  | "pnl_percentage"
  | "xirr"
  | "day_change_percentage"
  | "weight";
type Kind = "all" | "equity" | "mf";

const COLUMNS: { key: Key; label: string; numeric?: boolean }[] = [
  { key: "tradingsymbol", label: "Instrument" },
  { key: "invested", label: "Invested", numeric: true },
  { key: "current_value", label: "Current", numeric: true },
  { key: "pnl", label: "Total P&L", numeric: true },
  { key: "xirr", label: "XIRR", numeric: true },
  { key: "day_change_percentage", label: "Today", numeric: true },
  { key: "weight", label: "Weight", numeric: true },
];

export default function HoldingsTable({ holdings, title = "Holdings" }: { holdings: Holding[]; title?: string }) {
  const f = useFmt();
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: "current_value", dir: -1 });
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<Kind>("all");
  const hasMf = holdings.some((h) => h.kind === "mf");
  // XIRR exists for the live portfolio only (snapshots don't carry it).
  const hasXirr = holdings.some((h) => h.xirr_coverage !== undefined);
  const columns = COLUMNS.filter((c) => hasXirr || c.key !== "xirr");
  const toast = useToast();
  const onError = useCallback((m: string) => toast("error", m), [toast]);
  const agent = useAgentConversation({ onError });
  const [analysing, setAnalysing] = useState<Holding | null>(null);

  // Remember each holding's latest analysis so reopening shows it (or re-attaches if still running).
  const holdingKey = (h: Holding) => `analysis:${h.kind}:${h.exchange}:${h.tradingsymbol}`;
  useEffect(() => {
    if (!analysing || !agent.conversationId) return;
    try {
      localStorage.setItem(holdingKey(analysing), agent.conversationId);
    } catch {}
  }, [analysing, agent.conversationId]);

  function openAnalysis(h: Holding) {
    setAnalysing(h);
    let previous: string | null = null;
    try {
      previous = localStorage.getItem(holdingKey(h));
    } catch {}
    if (previous) {
      agent.reset();
      agent.load(previous, { quiet: true }).then((ok) => {
        if (!ok) agent.analyze(h);
      });
    } else agent.analyze(h);
  }

  const { reset } = agent;
  const closeAnalysis = useCallback(() => {
    setAnalysing(null);
    reset();
  }, [reset]);

  const total = holdings.reduce((s, h) => s + h.current_value, 0) || 1;
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = holdings
      .map((h) => ({ ...h, weight: h.weight ?? (h.current_value / total) * 100 }))
      .filter((h) => kind === "all" || h.kind === kind)
      .filter((h) => !q || h.tradingsymbol.toLowerCase().includes(q) || (h.name ?? "").toLowerCase().includes(q));
    filtered.sort((a, b) => {
      const av = a[sort.key] ?? -Infinity;
      const bv = b[sort.key] ?? -Infinity;
      return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
    });
    return filtered;
  }, [holdings, sort, query, kind, total]);

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
        <div>
          <div className="card-title">{title}</div>
          <div className="card-subtitle">{holdings.length} instruments</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {hasMf && (
            <div className="segmented" role="group" aria-label="Filter by type">
              {(["all", "equity", "mf"] as Kind[]).map((k) => (
                <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>
                  {k === "all" ? "All" : k === "equity" ? "Stocks" : "Funds"}
                </button>
              ))}
            </div>
          )}
          <label className="relative">
            <Search className="size-4 text-subtle absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" className="input pl-8 w-44" />
          </label>
          <PrivacyToggle labelled />
        </div>
      </div>

      <div className="overflow-x-auto border-t border-border">
        <table className="table-modern">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key} className={c.numeric ? "text-right" : "text-left"}>
                  <button
                    className={`inline-flex items-center gap-1 hover:text-foreground ${sort.key === c.key ? "text-foreground" : ""}`}
                    onClick={() => setSort((s) => ({ key: c.key, dir: s.key === c.key ? (-s.dir as 1 | -1) : -1 }))}
                  >
                    {c.label}
                    {sort.key === c.key && (sort.dir === -1 ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
                  </button>
                </th>
              ))}
              <th className="text-right">
                <span className="sr-only">Analyse</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <tr key={`${h.kind}:${h.exchange}:${h.tradingsymbol}`}>
                <td>
                  <div className="flex items-center gap-3">
                    <span className="size-8 shrink-0 rounded-lg bg-background border border-border grid place-items-center text-[0.65rem] font-semibold text-muted">
                      {h.kind === "mf" ? "MF" : h.tradingsymbol.slice(0, 2)}
                    </span>
                    <div className="min-w-0">
                      <div className="font-medium truncate max-w-[16rem]">{h.kind === "mf" ? h.name ?? h.tradingsymbol : h.tradingsymbol}</div>
                      <div className="text-xs text-muted tabular-nums">
                        {h.kind === "mf" ? "Fund" : h.exchange} · {f.qty(h.quantity)} @ {fmt.price(h.average_price)}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="text-right tabular-nums text-muted">{f.inr(h.invested)}</td>
                <td className="text-right tabular-nums">
                  <div className="font-medium">{f.inr(h.current_value)}</div>
                  <div className="text-xs text-muted">LTP {fmt.price(h.last_price)}</div>
                </td>
                <td className="text-right tabular-nums">
                  <div className={`font-medium ${tone(h.pnl)}`}>{f.signedInr(h.pnl)}</div>
                  <div className={`text-xs ${tone(h.pnl_percentage)}`}>{fmt.pct(h.pnl_percentage)}</div>
                </td>
                {hasXirr && (
                  <td className="text-right tabular-nums">
                    <XirrCell h={h} />
                  </td>
                )}
                <td className="text-right">
                  <ChangePill value={h.day_change_percentage}>{fmt.pct(h.day_change_percentage)}</ChangePill>
                </td>
                <td className="text-right">
                  <div className="inline-flex items-center gap-2">
                    <div className="w-16 h-1.5 rounded-full bg-background hidden sm:block">
                      <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(h.weight!, 100)}%` }} />
                    </div>
                    <span className="tabular-nums text-muted w-12">{h.weight!.toFixed(1)}%</span>
                  </div>
                </td>
                <td className="text-right">
                  <button onClick={() => openAnalysis(h)} className="btn btn-sm text-accent" title={`AI analysis of ${h.tradingsymbol}`}>
                    <Sparkles className="size-3.5" />
                    Analyse
                  </button>
                </td>
              </tr>
            ))}
            {!rows.length && (
              <tr>
                <td colSpan={columns.length + 1} className="text-center text-muted py-10">
                  {holdings.length ? "No matches." : "No holdings yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <AnalysisDrawer holding={analysing} agent={agent} onClose={closeAnalysis} />
    </div>
  );
}

function XirrCell({ h }: { h: Holding }) {
  if (h.xirr == null) {
    return (
      <span className="text-subtle" title="Import your Console tradebook to calculate XIRR for this holding">
        —
      </span>
    );
  }
  const partial = h.xirr_coverage === "partial";
  return (
    <span
      className={`font-medium ${tone(h.xirr)}`}
      title={
        partial
          ? "Approximate: the imported tradebook explains only some of these units (older trades, bonus/split or transfer-in)"
          : "Annualised return from your trades and today's value"
      }
    >
      {partial && "≈"}
      {fmt.pct(h.xirr)}
    </span>
  );
}
