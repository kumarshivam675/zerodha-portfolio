"use client";

import { fmt, type Holding } from "@/lib/api";
import { useFmt } from "@/lib/privacy";
import { ChangePill } from "./ui";

const label = (h: Holding) => (h.kind === "mf" ? h.name ?? h.tradingsymbol : h.tradingsymbol);

/** Top holdings by weight as single-hue bars, remainder folded into "Others". */
export function Allocation({ holdings }: { holdings: Holding[] }) {
  const f = useFmt();
  const total = holdings.reduce((s, h) => s + h.current_value, 0) || 1;
  const sorted = [...holdings].sort((a, b) => b.current_value - a.current_value);
  const top = sorted.slice(0, 7).map((h) => ({ name: label(h), value: h.current_value }));
  const rest = sorted.slice(7).reduce((s, h) => s + h.current_value, 0);
  if (rest > 0) top.push({ name: `Others (${sorted.length - 7})`, value: rest });
  const max = Math.max(...top.map((t) => t.value), 1);

  const equity = holdings.filter((h) => h.kind === "equity").reduce((s, h) => s + h.current_value, 0);
  const equityPct = (equity / total) * 100;

  return (
    <div className="space-y-4">
      <div>
        <div className="flex justify-between text-xs text-muted mb-1.5">
          <span>Stocks {equityPct.toFixed(0)}%</span>
          <span>Mutual funds {(100 - equityPct).toFixed(0)}%</span>
        </div>
        <div className="flex h-2 rounded-full overflow-hidden bg-background gap-0.5">
          <div className="bg-accent rounded-full" style={{ width: `${equityPct}%` }} />
          <div className="bg-accent/35 rounded-full flex-1" />
        </div>
      </div>
      <ul className="space-y-2.5">
        {top.map((t) => (
          <li key={t.name} title={`${t.name}: ${f.inr(t.value)}`}>
            <div className="flex justify-between text-sm mb-1 gap-3">
              <span className="truncate">{t.name}</span>
              <span className="tabular-nums text-muted shrink-0">{((t.value / total) * 100).toFixed(1)}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-background">
              <div className="h-full rounded-full bg-accent" style={{ width: `${(t.value / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Biggest gainers and losers today by % change. */
export function Movers({ holdings }: { holdings: Holding[] }) {
  const moved = holdings.filter((h) => h.day_change_percentage != null && h.day_change_percentage !== 0);
  const byPct = [...moved].sort((a, b) => b.day_change_percentage! - a.day_change_percentage!);
  const gainers = byPct.filter((h) => h.day_change_percentage! > 0).slice(0, 4);
  const losers = byPct.filter((h) => h.day_change_percentage! < 0).reverse().slice(0, 4);

  if (!moved.length) return <p className="text-sm text-muted">No price moves recorded yet today.</p>;

  const Row = ({ h }: { h: Holding }) => (
    <li className="flex items-center justify-between gap-3 py-2">
      <div className="min-w-0">
        <div className="text-sm font-medium truncate">{label(h)}</div>
        <div className="text-xs text-muted tabular-nums whitespace-nowrap">
          {fmt.price(h.last_price)} · {fmt.signedInr(h.day_change)}
        </div>
      </div>
      <ChangePill value={h.day_change_percentage}>{fmt.pct(h.day_change_percentage)}</ChangePill>
    </li>
  );

  return (
    <div className="grid sm:grid-cols-2 gap-x-6">
      <div>
        <div className="text-xs font-medium text-muted uppercase tracking-wide mb-1">Gainers</div>
        <ul className="divide-y divide-border">{gainers.length ? gainers.map((h) => <Row key={h.tradingsymbol} h={h} />) : <li className="text-sm text-muted py-2">None</li>}</ul>
      </div>
      <div>
        <div className="text-xs font-medium text-muted uppercase tracking-wide mb-1 mt-3 sm:mt-0">Losers</div>
        <ul className="divide-y divide-border">{losers.length ? losers.map((h) => <Row key={h.tradingsymbol} h={h} />) : <li className="text-sm text-muted py-2">None</li>}</ul>
      </div>
    </div>
  );
}
