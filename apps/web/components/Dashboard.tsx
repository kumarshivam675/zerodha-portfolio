"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Camera, ChevronDown, Link2, Loader2, Play, RefreshCw, Sparkles, Wallet } from "lucide-react";
import { api, fmt, tone, type Brief, type JobRun, type KiteStatus, type Portfolio, type Snapshot } from "@/lib/api";
import { PrivacyToggle, useFmt } from "@/lib/privacy";
import HoldingsTable from "./HoldingsTable";
import { Allocation, Movers } from "./Insights";
import TradebookImport from "./TradebookImport";
import { ChangePill, EmptyState, Modal, PageHeader, Skeleton, useToast } from "./ui";
import ValueChart from "./ValueChart";

export default function Dashboard() {
  const f = useFmt();
  const params = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const [kite, setKite] = useState<KiteStatus | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [lastJob, setLastJob] = useState<JobRun | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  const [note, setNote] = useState("");
  const [importOpen, setImportOpen] = useState(false);

  const load = useCallback(
    () =>
      Promise.all([
        api<KiteStatus>("/kite/status"),
        api<Portfolio>("/portfolio"),
        api<Snapshot[]>("/snapshots"),
        api<Brief[]>("/briefs?limit=1"),
        api<JobRun[]>("/jobs"),
      ]).then(
        ([k, p, s, b, j]) => {
          setKite(k);
          setPortfolio(p);
          setSnapshots(s);
          setBrief(b[0] ?? null);
          setLastJob(j[0] ?? null);
        },
        (e: Error) => toast("error", `Could not load data: ${e.message}`),
      ),
    [toast],
  );

  useEffect(() => {
    load();
  }, [load]);

  // Result of the Kite login redirect.
  useEffect(() => {
    const k = params.get("kite");
    if (!k) return;
    if (k === "connected") toast("success", "Connected to Zerodha and synced your holdings.");
    else toast("error", "Zerodha login failed. Check the API key, secret and redirect URL.");
    router.replace("/");
  }, [params, router, toast]);

  // Poll while the daily job runs.
  useEffect(() => {
    if (lastJob?.status !== "running") return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [lastJob?.status, load]);

  async function act(name: string, fn: () => Promise<string>) {
    setBusy(name);
    try {
      toast("success", await fn());
      await load();
    } catch (e) {
      toast("error", (e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const sync = () =>
    act("sync", async () => {
      const r = await api<{ equity: number; mf: number }>("/kite/sync", { method: "POST" });
      return `Synced ${r.equity} stocks and ${r.mf} mutual funds from Zerodha.`;
    });

  const saveSnapshot = () =>
    act("snapshot", async () => {
      const s = await api<Snapshot>("/snapshots", { method: "POST", body: JSON.stringify({ note: note.trim() || null }) });
      setSnapshotOpen(false);
      setNote("");
      return `Snapshot saved for ${s.snapshot_date} at ${f.inr(s.current_value)}.`;
    });

  const runJob = () =>
    act("job", async () => {
      await api("/jobs/daily/run", { method: "POST" });
      setLastJob((j) => ({ ...(j ?? ({} as JobRun)), status: "running" }));
      return "Daily job started: refreshing prices, saving a snapshot and writing your brief.";
    });

  if (!portfolio) return <DashboardSkeleton />;

  const t = portfolio.totals;
  const hasHoldings = portfolio.holdings.length > 0;
  const jobRunning = lastJob?.status === "running";

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle={
          hasHoldings ? (
            <>
              Prices as of {fmt.date(portfolio.price_updated_at)} · Synced {fmt.date(portfolio.synced_at)}
            </>
          ) : (
            "Your Zerodha portfolio at a glance"
          )
        }
        actions={
          <>
            {kite?.connected ? (
              <button onClick={sync} disabled={!!busy} className="btn">
                <RefreshCw className={`size-4 ${busy === "sync" ? "animate-spin" : ""}`} />
                Sync
              </button>
            ) : null}
            <button onClick={runJob} disabled={!!busy || jobRunning || !hasHoldings} className="btn" title="Refresh prices, snapshot, and brief">
              {jobRunning ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
              {jobRunning ? "Running…" : "Run daily job"}
            </button>
            <button onClick={() => setSnapshotOpen(true)} disabled={!!busy || !hasHoldings} className="btn btn-primary">
              <Camera className="size-4" />
              Save snapshot
            </button>
          </>
        }
      />

      {kite && !kite.connected && <ConnectBanner configured={kite.configured} hasHoldings={hasHoldings} />}

      {!hasHoldings ? (
        <div className="card">
          <EmptyState
            icon={<Wallet className="size-5" />}
            title="No holdings yet"
            body="Connect your Zerodha account to pull in your stocks and mutual funds."
          />
        </div>
      ) : (
        <div className="space-y-6">
          <div className="grid lg:grid-cols-3 gap-6 [&>*]:min-w-0">
            <section className="card p-5 lg:col-span-2">
              <div className="flex flex-wrap items-start justify-between gap-6">
                <div>
                  <div className="flex items-center gap-1 text-sm text-muted">
                    Current value
                    <PrivacyToggle className="size-7! -my-1" />
                  </div>
                  <div className="text-4xl font-semibold tracking-tight tabular-nums mt-1">{f.inr(t.current_value)}</div>
                  <div className="flex flex-wrap items-center gap-2 mt-2 text-sm">
                    <ChangePill value={t.day_change}>
                      {f.signedInr(t.day_change)} ({fmt.pct(t.day_change_percentage)})
                    </ChangePill>
                    <span className="text-muted">today</span>
                  </div>
                </div>
                <dl className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
                  <dt className="text-muted">Invested</dt>
                  <dd className="text-right tabular-nums font-medium">{f.inr(t.invested)}</dd>
                  <dt className="text-muted">Total P&amp;L</dt>
                  <dd className={`text-right tabular-nums font-medium ${tone(t.pnl)}`}>{f.signedInr(t.pnl)}</dd>
                  <dt className="text-muted">Return</dt>
                  <dd className={`text-right tabular-nums font-medium ${tone(t.pnl_percentage)}`}>{fmt.pct(t.pnl_percentage)}</dd>
                  <dt className="text-muted">XIRR</dt>
                  <dd className="text-right tabular-nums font-medium">
                    {t.xirr != null ? (
                      <button
                        onClick={() => setImportOpen(true)}
                        className={`${tone(t.xirr)} hover:underline decoration-dotted underline-offset-4`}
                        title={`Annualised return from your imported trades. Covers ${t.xirr_coverage?.toFixed(0)}% of current value; click to import more history.`}
                      >
                        {(t.xirr_coverage ?? 0) < 99.5 && "≈"}
                        {fmt.pct(t.xirr)}
                      </button>
                    ) : (
                      <button onClick={() => setImportOpen(true)} className="text-accent hover:underline">
                        Import trades
                      </button>
                    )}
                  </dd>
                  <dt className="text-muted">Holdings</dt>
                  <dd className="text-right tabular-nums font-medium">{t.count}</dd>
                </dl>
              </div>
              {t.xirr != null && (t.xirr_coverage ?? 0) < 99.5 && (
                <p className="text-xs text-muted mt-3 text-right">
                  XIRR covers {t.xirr_coverage?.toFixed(0)}% of your portfolio value. The rest was bought before your imported
                  tradebook (or came from bonus, split or transfer).{" "}
                  <button onClick={() => setImportOpen(true)} className="text-accent hover:underline">
                    Import older trades
                  </button>
                </p>
              )}
              <div className="mt-6">
                <ValueChart snapshots={snapshots} />
              </div>
            </section>

            <section className="card p-5">
              <div className="card-title mb-4">Allocation</div>
              <Allocation holdings={portfolio.holdings} />
            </section>
          </div>

          <div className="grid lg:grid-cols-2 gap-6 [&>*]:min-w-0">
            <section className="card p-5">
              <div className="card-title mb-2">Today&apos;s movers</div>
              <Movers holdings={portfolio.holdings} />
            </section>
            <BriefCard brief={brief} lastJob={lastJob} />
          </div>

          <HoldingsTable holdings={portfolio.holdings} />
        </div>
      )}

      <TradebookImport open={importOpen} onClose={() => setImportOpen(false)} onImported={load} />

      <Modal
        open={snapshotOpen}
        title="Save today's snapshot"
        onClose={() => setSnapshotOpen(false)}
        footer={
          <>
            <button className="btn" onClick={() => setSnapshotOpen(false)}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={saveSnapshot} disabled={busy === "snapshot"}>
              {busy === "snapshot" && <Loader2 className="size-4 animate-spin" />}
              Save snapshot
            </button>
          </>
        }
      >
        <p className="text-sm text-muted mb-3">
          Freezes your {t.count} holdings worth {f.inr(t.current_value)} under today&apos;s date. Saving again today replaces it.
        </p>
        <label className="text-sm font-medium" htmlFor="snapshot-note">
          Note <span className="text-muted font-normal">(optional)</span>
        </label>
        <input
          id="snapshot-note"
          autoFocus
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && saveSnapshot()}
          placeholder="e.g. Before rebalancing into index funds"
          className="input w-full mt-1.5"
        />
      </Modal>
    </>
  );
}

function BriefCard({ brief, lastJob }: { brief: Brief | null; lastJob: JobRun | null }) {
  const f = useFmt();
  const [expanded, setExpanded] = useState(false);
  return (
    <section className="card p-5 flex flex-col">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <span className="size-7 rounded-lg bg-accent-soft text-accent grid place-items-center">
            <Sparkles className="size-4" />
          </span>
          <div className="card-title">Daily brief</div>
          {brief && <span className="text-xs text-muted">{fmt.date(brief.created_at)}</span>}
        </div>
        <div className="flex items-center gap-1">
          <PrivacyToggle />
          <Link href="/agent" className="btn btn-sm btn-ghost text-accent">
            Ask the analyst
          </Link>
        </div>
      </div>
      {brief ? (
        <>
          <div className={`relative prose-chat text-sm ${expanded ? "" : "max-h-72 overflow-hidden"}`}>
            <Markdown remarkPlugins={[remarkGfm]}>{f.text(brief.content)}</Markdown>
            {!expanded && <div className="absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface to-transparent" />}
          </div>
          <button onClick={() => setExpanded((e) => !e)} className="btn btn-sm btn-ghost self-start mt-2 -ml-2">
            <ChevronDown className={`size-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
            {expanded ? "Show less" : "Read full brief"}
          </button>
        </>
      ) : (
        <p className="text-sm text-muted">
          {lastJob?.status === "running"
            ? "Writing today's brief…"
            : "The analyst writes a brief after the daily job runs (weekdays, 4:30 PM IST). Run the job now to get one."}
        </p>
      )}
    </section>
  );
}

function ConnectBanner({ configured, hasHoldings }: { configured: boolean; hasHoldings: boolean }) {
  return (
    <div className="card mb-6 px-5 py-4 flex flex-wrap items-center justify-between gap-4 bg-gradient-to-r from-accent-soft to-surface">
      <div className="flex items-center gap-3">
        <span className="size-9 rounded-lg bg-surface border border-border grid place-items-center text-accent">
          <Link2 className="size-4.5" />
        </span>
        <div>
          <div className="text-sm font-medium">{configured ? "Connect Zerodha" : "Zerodha isn't configured"}</div>
          <div className="text-sm text-muted">
            {!configured
              ? "Add KITE_API_KEY and KITE_API_SECRET to .env and restart."
              : hasHoldings
                ? "Your session expired (Kite logs out daily at 6 AM). Prices still refresh; log in to update holdings."
                : "Log in with Kite to import your holdings."}
          </div>
        </div>
      </div>
      {configured && (
        // Full-page navigation: the API redirects to Kite's login page.
        // eslint-disable-next-line @next/next/no-html-link-for-pages
        <a href="/api/kite/login" className="btn btn-primary">
          Log in with Kite
        </a>
      )}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-9 w-48" />
      <div className="grid lg:grid-cols-3 gap-6">
        <Skeleton className="h-96 lg:col-span-2 rounded-2xl" />
        <Skeleton className="h-96 rounded-2xl" />
      </div>
      <Skeleton className="h-72 rounded-2xl" />
    </div>
  );
}
