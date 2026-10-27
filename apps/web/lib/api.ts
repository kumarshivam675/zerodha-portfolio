export type Holding = {
  kind: "equity" | "mf";
  tradingsymbol: string;
  exchange: string;
  isin: string | null;
  name: string | null;
  quantity: number;
  average_price: number;
  last_price: number;
  close_price: number | null;
  invested: number;
  current_value: number;
  pnl: number;
  pnl_percentage: number | null;
  day_change: number | null;
  day_change_percentage: number | null;
  weight?: number;
  /** Annualised return from imported trades (%); absent on snapshot holdings. */
  xirr?: number | null;
  xirr_coverage?: "full" | "partial" | "none";
};

export type Totals = {
  invested: number;
  current_value: number;
  pnl: number;
  pnl_percentage: number | null;
  day_change: number | null;
  day_change_percentage: number | null;
  count: number;
  xirr?: number | null;
  /** Share of current value (%) whose cost history the imported trades explain. */
  xirr_coverage?: number;
  trades_imported?: number;
};

export type Portfolio = {
  totals: Totals;
  holdings: Holding[];
  synced_at: string | null;
  price_updated_at: string | null;
};

export type KiteStatus = {
  configured: boolean;
  connected: boolean;
  user_id: string | null;
  user_name: string | null;
  login_time: string | null;
};

export type Snapshot = {
  id: number;
  snapshot_date: string;
  taken_at: string | null;
  source: "manual" | "scheduled";
  invested: number;
  current_value: number;
  pnl: number;
  pnl_percentage: number | null;
  day_change: number | null;
  note: string | null;
  holdings?: Holding[];
};

export type Brief = { id: number; brief_date: string; content: string; created_at: string };

export type JobRun = {
  id: number;
  job: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  detail: Record<string, unknown> | null;
};

export type Conversation = { id: string; title: string; created_at: string; updated_at: string; running?: boolean };
export type ChatBubble = {
  role: "user" | "assistant";
  text: string;
  tools: { name: string; input: string }[];
  /** Assistant text written between tool calls (progress narration), oldest first. */
  notes?: string[];
  /** Reasoning summary streaming in right now (moved into notes once the step ends). */
  thinking?: string;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body.detail ?? message;
    } catch {}
    throw new Error(message);
  }
  return res.json();
}

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });

export const fmt = {
  inr: (v: number | null | undefined) => (v == null ? "—" : inr.format(v)),
  price: (v: number | null | undefined) => (v == null ? "—" : inr2.format(v)),
  qty: (v: number) => num.format(v),
  signedInr: (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${inr.format(v)}`),
  pct: (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`),
  date: (v: string | null | undefined) =>
    v ? new Date(v).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—",
};

export function tone(v: number | null | undefined) {
  if (v == null || v === 0) return "text-muted";
  return v > 0 ? "text-gain" : "text-loss";
}
