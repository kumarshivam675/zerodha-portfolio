"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronRight, History } from "lucide-react";
import { api, fmt, tone, type Snapshot } from "@/lib/api";
import { PrivacyToggle, useFmt } from "@/lib/privacy";
import { ChangePill, EmptyState, PageHeader, Skeleton, useToast } from "./ui";
import ValueChart from "./ValueChart";

export default function SnapshotList() {
  const f = useFmt();
  const toast = useToast();
  const [snapshots, setSnapshots] = useState<Snapshot[] | null>(null);

  useEffect(() => {
    api<Snapshot[]>("/snapshots").then(setSnapshots, (e) => toast("error", e.message));
  }, [toast]);

  return (
    <>
      <PageHeader title="Snapshots" subtitle="Daily records of your portfolio, saved manually or by the daily job." />
      {!snapshots ? (
        <Skeleton className="h-96 rounded-2xl" />
      ) : snapshots.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<History className="size-5" />}
            title="No snapshots yet"
            body="Save one from the dashboard, or let the daily job save one every weekday at 4:30 PM IST."
            action={
              <Link href="/" className="btn btn-primary">
                Go to dashboard
              </Link>
            }
          />
        </div>
      ) : (
        <div className="space-y-6">
          <section className="card p-5">
            <ValueChart snapshots={snapshots} />
          </section>
          <section className="card overflow-hidden">
            <div className="flex items-center justify-between gap-3 px-5 py-4">
              <div>
                <div className="card-title">History</div>
                <div className="card-subtitle">{snapshots.length} snapshots</div>
              </div>
              <PrivacyToggle labelled />
            </div>
            <div className="overflow-x-auto border-t border-border">
              <table className="table-modern">
                <thead>
                  <tr>
                    <th className="text-left">Date</th>
                    <th className="text-right">Value</th>
                    <th className="text-right">vs previous</th>
                    <th className="text-right">Invested</th>
                    <th className="text-right">Total P&amp;L</th>
                    <th className="text-left">Note</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {snapshots.map((s, i) => {
                    const prev = snapshots[i + 1];
                    const delta = prev ? s.current_value - prev.current_value : null;
                    const deltaPct = prev && prev.current_value ? (delta! / prev.current_value) * 100 : null;
                    return (
                      <tr key={s.id} className="cursor-pointer">
                        <td>
                          <Link href={`/snapshots/${s.snapshot_date}`} className="font-medium hover:text-accent">
                            {new Date(s.snapshot_date).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" })}
                          </Link>
                          <span className={`ml-2 pill ${s.source === "manual" ? "bg-accent-soft text-accent" : "pill-neutral"}`}>
                            {s.source === "manual" ? "Manual" : "Auto"}
                          </span>
                        </td>
                        <td className="text-right tabular-nums font-medium">{f.inr(s.current_value)}</td>
                        <td className="text-right">
                          {delta != null ? <ChangePill value={delta}>{fmt.pct(deltaPct)}</ChangePill> : <span className="text-subtle">—</span>}
                        </td>
                        <td className="text-right tabular-nums text-muted">{f.inr(s.invested)}</td>
                        <td className={`text-right tabular-nums ${tone(s.pnl)}`}>
                          {f.signedInr(s.pnl)} <span className="text-xs">({fmt.pct(s.pnl_percentage)})</span>
                        </td>
                        <td className="text-muted max-w-[16rem] truncate">{s.note ?? ""}</td>
                        <td className="text-right">
                          <Link href={`/snapshots/${s.snapshot_date}`} className="text-subtle hover:text-foreground inline-flex" aria-label="Open snapshot">
                            <ChevronRight className="size-4" />
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
