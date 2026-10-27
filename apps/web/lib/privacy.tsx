"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { Eye, EyeOff } from "lucide-react";
import { api, fmt, type Portfolio, type Snapshot } from "./api";
import { MASK, privacyText, type Sensitive } from "./mask";

/*
 * Privacy mode: position-sized amounts (₹ values, invested, P&L, quantities) are masked by default.
 * The choice is remembered per browser and shared by every screen through a tiny external store.
 * Per-unit prices (LTP, average cost), today's movers, percentages and dates stay visible, and in
 * the analyst's text only the investor's own figures are masked (see maskText).
 */

const KEY = "privacy:amounts-hidden";
const listeners = new Set<() => void>();
let memory: boolean | null = null; // fallback when storage is unavailable

function read(): boolean {
  if (memory !== null) return memory;
  try {
    return localStorage.getItem(KEY) !== "0";
  } catch {
    return true;
  }
}

function write(hidden: boolean) {
  memory = hidden;
  try {
    localStorage.setItem(KEY, hidden ? "1" : "0");
  } catch {}
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) {
      memory = null;
      cb();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

export function useAmountsHidden(): [boolean, () => void] {
  const hidden = useSyncExternalStore(subscribe, read, () => true);
  return [hidden, () => write(!read())];
}

/* ---------- Masking the analyst's free text ----------
 * Only figures that are the investor's own (position values, invested, P&L, portfolio and
 * snapshot totals, share quantities) are masked. Market data (prices, targets, market caps,
 * results) and recommendations stay readable. Amounts are matched to the portfolio's numbers
 * with a tolerance for the rounding the analyst used ("₹4.20 L" matches ₹4,19,870).
 */

const SENSITIVE_TTL_MS = 60_000;
let sensitiveCache: { at: number; promise: Promise<Sensitive> } | null = null;

function loadSensitive(): Promise<Sensitive> {
  if (sensitiveCache && Date.now() - sensitiveCache.at < SENSITIVE_TTL_MS) return sensitiveCache.promise;
  const promise = Promise.all([api<Portfolio>("/portfolio"), api<Snapshot[]>("/snapshots")]).then(([p, snaps]) => {
    const amounts = new Set<number>();
    const add = (v: number | null | undefined) => v != null && Math.abs(v) >= 1 && amounts.add(Math.abs(v));
    [p.totals.current_value, p.totals.invested, p.totals.pnl, p.totals.day_change].forEach(add);
    for (const h of p.holdings) [h.current_value, h.invested, h.pnl].forEach(add);
    for (const s of snaps) [s.current_value, s.invested, s.pnl, s.day_change].forEach(add);
    return { amounts: [...amounts], quantities: p.holdings.map((h) => h.quantity) };
  });
  promise.catch(() => (sensitiveCache = null));
  sensitiveCache = { at: Date.now(), promise };
  return promise;
}

/** Formatters that mask the investor's own amounts while privacy mode is on. */
export function useFmt() {
  const [hidden] = useAmountsHidden();
  const [sensitive, setSensitive] = useState<Sensitive | null>(null);

  useEffect(() => {
    if (!hidden) return;
    let cancelled = false;
    loadSensitive().then(
      (s) => !cancelled && setSensitive(s),
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, [hidden]);

  return useMemo(
    () => ({
      hidden,
      inr: (v: number | null | undefined) => (hidden && v != null ? `₹${MASK}` : fmt.inr(v)),
      signedInr: (v: number | null | undefined) =>
        hidden && v != null ? `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${MASK}` : fmt.signedInr(v),
      qty: (v: number) => (hidden ? MASK : fmt.qty(v)),
      /** Mask the investor's own amounts inside free text such as the analyst's Markdown. */
      text: (s: string) => privacyText(s, hidden, sensitive),
      pct: fmt.pct,
      price: fmt.price,
      date: fmt.date,
    }),
    [hidden, sensitive],
  );
}

export function PrivacyToggle({ className = "", labelled = false }: { className?: string; labelled?: boolean }) {
  const [hidden, toggle] = useAmountsHidden();
  const label = hidden ? "Show amounts" : "Hide amounts";
  const Icon = hidden ? Eye : EyeOff;
  return (
    <button
      onClick={toggle}
      className={labelled ? `btn ${className}` : `btn btn-ghost btn-sm size-9 p-0! ${className}`}
      aria-pressed={!hidden}
      aria-label={label}
      title={label}
    >
      <Icon className="size-4" />
      {labelled && label}
    </button>
  );
}
