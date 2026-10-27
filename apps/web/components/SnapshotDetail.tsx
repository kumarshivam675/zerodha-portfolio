"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, Trash2 } from "lucide-react";
import { api, fmt, tone, type Snapshot } from "@/lib/api";
import { useFmt } from "@/lib/privacy";
import HoldingsTable from "./HoldingsTable";
import { Modal, PageHeader, Skeleton, useToast } from "./ui";

export default function SnapshotDetail() {
  const f = useFmt();
  const { date } = useParams<{ date: string }>();
  const router = useRouter();
  const toast = useToast();
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    api<Snapshot>(`/snapshots/${date}`).then(setSnap, (e) => toast("error", e.message));
  }, [date, toast]);

  async function remove() {
    try {
      await api(`/snapshots/${date}`, { method: "DELETE" });
      toast("success", `Deleted the snapshot for ${date}.`);
      router.push("/snapshots");
    } catch (e) {
      toast("error", (e as Error).message);
    }
  }

  if (!snap) return <Skeleton className="h-96 rounded-2xl" />;

  const longDate = new Date(snap.snapshot_date).toLocaleDateString("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  const stats = [
    { label: "Value", value: f.inr(snap.current_value) },
    { label: "Invested", value: f.inr(snap.invested) },
    { label: "Total P&L", value: f.signedInr(snap.pnl), sub: fmt.pct(snap.pnl_percentage), cls: tone(snap.pnl) },
    { label: "Day change", value: f.signedInr(snap.day_change), cls: tone(snap.day_change) },
  ];

  return (
    <>
      <Link href="/snapshots" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-foreground mb-3">
        <ArrowLeft className="size-4" /> All snapshots
      </Link>
      <PageHeader
        title={longDate}
        subtitle={
          <>
            {snap.source === "manual" ? "Saved manually" : "Saved by the daily job"} · {fmt.date(snap.taken_at)}
            {snap.note && <> · &ldquo;{snap.note}&rdquo;</>}
          </>
        }
        actions={
          <button onClick={() => setConfirming(true)} className="btn">
            <Trash2 className="size-4" /> Delete
          </button>
        }
      />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {stats.map((s) => (
          <div key={s.label} className="card p-4">
            <div className="text-sm text-muted">{s.label}</div>
            <div className={`text-xl font-semibold tabular-nums mt-1 ${s.cls ?? ""}`}>{s.value}</div>
            {s.sub && <div className={`text-sm tabular-nums ${s.cls ?? "text-muted"}`}>{s.sub}</div>}
          </div>
        ))}
      </div>
      <HoldingsTable holdings={snap.holdings ?? []} title="Holdings on this day" />

      <Modal
        open={confirming}
        title="Delete snapshot?"
        onClose={() => setConfirming(false)}
        footer={
          <>
            <button className="btn" onClick={() => setConfirming(false)}>
              Cancel
            </button>
            <button className="btn bg-loss! border-loss! text-white!" onClick={remove}>
              Delete
            </button>
          </>
        }
      >
        <p className="text-sm text-muted">The snapshot for {longDate} and its holdings will be permanently removed.</p>
      </Modal>
    </>
  );
}
