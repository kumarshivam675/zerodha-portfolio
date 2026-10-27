"use client";

import { useState } from "react";
import { FileUp, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { Modal, useToast } from "./ui";

type ImportResult = { rows: number; inserted: number; duplicates: number; skipped: number; from: string | null; to: string | null };

/** Upload Zerodha Console tradebook CSVs so XIRR can be calculated from real cash flows. */
export default function TradebookImport({ open, onClose, onImported }: { open: boolean; onClose: () => void; onImported: () => void }) {
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<{ name: string; result?: ImportResult; error?: string }[]>([]);

  function close() {
    setFiles([]);
    setResults([]);
    onClose();
  }

  async function upload() {
    setBusy(true);
    const out: typeof results = [];
    for (const file of files) {
      try {
        const result = await api<ImportResult>("/trades/import", {
          method: "POST",
          body: JSON.stringify({ csv: await file.text() }),
        });
        out.push({ name: file.name, result });
      } catch (e) {
        out.push({ name: file.name, error: (e as Error).message });
      }
    }
    setResults(out);
    setFiles([]);
    setBusy(false);
    const added = out.reduce((n, r) => n + (r.result?.inserted ?? 0), 0);
    if (added) {
      toast("success", `Imported ${added} trades. XIRR updated.`);
      onImported();
    }
  }

  return (
    <Modal
      open={open}
      title="Import tradebook for XIRR"
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {results.length ? "Done" : "Cancel"}
          </button>
          <button className="btn btn-primary" onClick={upload} disabled={busy || !files.length}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Import {files.length > 1 ? `${files.length} files` : ""}
          </button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <p className="text-muted">
          Kite&apos;s API doesn&apos;t provide trade history or XIRR, so XIRR is calculated from your tradebook: every buy
          and sell with its date, plus today&apos;s value.
        </p>
        <ol className="list-decimal pl-5 space-y-1 text-muted">
          <li>
            Open{" "}
            <a href="https://console.zerodha.com/reports/tradebook" target="_blank" rel="noreferrer" className="text-accent underline">
              Console → Reports → Tradebook
            </a>
            .
          </li>
          <li>Choose segment Equity (and Mutual funds if you hold any) and a date range of up to 1 year.</li>
          <li>Download as CSV. Repeat for each year back to your first purchase.</li>
        </ol>
        <label className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border-strong px-4 py-6 cursor-pointer hover:border-accent hover:bg-accent-soft/40 transition">
          <FileUp className="size-5 text-accent" />
          <span className="font-medium">{files.length ? files.map((f) => f.name).join(", ") : "Choose CSV files"}</span>
          <span className="text-xs text-muted">You can select several. Trades already imported are skipped.</span>
          <input
            type="file"
            accept=".csv,text/csv"
            multiple
            className="sr-only"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>
        {results.length > 0 && (
          <ul className="space-y-1.5">
            {results.map((r) => (
              <li key={r.name} className="rounded-lg bg-surface-2 border border-border px-3 py-2">
                <div className="font-medium truncate">{r.name}</div>
                {r.error ? (
                  <div className="text-loss text-xs">{r.error}</div>
                ) : (
                  <div className="text-xs text-muted">
                    {r.result!.inserted} new trades, {r.result!.duplicates} already imported
                    {r.result!.skipped ? `, ${r.result!.skipped} skipped (non-equity or unreadable)` : ""}
                    {r.result!.from && ` · ${r.result!.from} to ${r.result!.to}`}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
