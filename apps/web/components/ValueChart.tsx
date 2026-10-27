"use client";

import { useId, useMemo, useRef, useState } from "react";
import { fmt, type Snapshot } from "@/lib/api";
import { useFmt } from "@/lib/privacy";

const W = 800;
const H = 240;
const PAD = { top: 16, right: 8, bottom: 28, left: 8 };
const RANGES = [
  { key: "1M", days: 31 },
  { key: "3M", days: 92 },
  { key: "6M", days: 183 },
  { key: "1Y", days: 366 },
  { key: "All", days: Infinity },
] as const;

/** Portfolio value across saved snapshots: one series, range tabs, crosshair + tooltip. */
export default function ValueChart({ snapshots }: { snapshots: Snapshot[] }) {
  const f = useFmt();
  const [range, setRange] = useState<(typeof RANGES)[number]["key"]>("All");
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const gradientId = useId();

  const points = useMemo(() => {
    const sorted = [...snapshots].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date));
    const days = RANGES.find((r) => r.key === range)!.days;
    if (!sorted.length || days === Infinity) return sorted;
    const last = new Date(sorted[sorted.length - 1].snapshot_date).getTime();
    return sorted.filter((p) => last - new Date(p.snapshot_date).getTime() <= days * 86_400_000);
  }, [snapshots, range]);

  const first = points[0];
  const last = points[points.length - 1];
  const change = first && last ? last.current_value - first.current_value : null;
  const changePct = change != null && first.current_value ? (change / first.current_value) * 100 : null;

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
      <div className="text-sm text-muted">
        {points.length >= 2 ? (
          <>
            <span className={change! >= 0 ? "text-gain font-medium" : "text-loss font-medium"}>
              {f.signedInr(change)} ({fmt.pct(changePct)})
            </span>{" "}
            since {fmtDate(first.snapshot_date)}
          </>
        ) : (
          "Value over time"
        )}
      </div>
      <div className="segmented" role="group" aria-label="Range">
        {RANGES.map((r) => (
          <button key={r.key} aria-pressed={range === r.key} onClick={() => setRange(r.key)}>
            {r.key}
          </button>
        ))}
      </div>
    </div>
  );

  if (points.length < 2) {
    return (
      <div>
        {header}
        <div className="h-[140px] rounded-xl border border-dashed border-border-strong grid place-items-center text-sm text-muted text-center px-6">
          Save snapshots on at least two days to see your value trend. The daily job saves one every weekday.
        </div>
      </div>
    );
  }

  const values = points.map((p) => p.current_value);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || hi * 0.02 || 1;
  const yMin = lo - span * 0.15;
  const yMax = hi + span * 0.15;
  const x = (i: number) => PAD.left + (i / (points.length - 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.current_value).toFixed(1)}`).join(" ");
  const area = `${line} L${x(points.length - 1).toFixed(1)},${H - PAD.bottom} L${x(0).toFixed(1)},${H - PAD.bottom} Z`;
  const grid = [0.25, 0.5, 0.75].map((t) => PAD.top + t * (H - PAD.top - PAD.bottom));
  const labelIdx = [0, Math.floor((points.length - 1) / 2), points.length - 1];

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const rect = svgRef.current!.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - PAD.left) / (W - PAD.left - PAD.right)) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  }

  const hp = hover != null ? points[hover] : null;
  return (
    <div>
      {header}
      <div className="relative">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-auto touch-none select-none"
          role="img"
          aria-label="Portfolio value by snapshot date"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.16} />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
            </linearGradient>
          </defs>
          {grid.map((gy) => (
            <line key={gy} x1={PAD.left} x2={W - PAD.right} y1={gy} y2={gy} stroke="var(--border)" strokeDasharray="3 4" />
          ))}
          <path d={area} fill={`url(#${gradientId})`} />
          <path d={line} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {labelIdx.map((i, k) => (
            <text
              key={k}
              x={x(i)}
              y={H - 8}
              fontSize={11}
              fill="var(--subtle)"
              textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"}
            >
              {fmtDate(points[i].snapshot_date)}
            </text>
          ))}
          {hp && (
            <g>
              <line x1={x(hover!)} x2={x(hover!)} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--border-strong)" />
              <circle cx={x(hover!)} cy={y(hp.current_value)} r={5} fill="var(--accent)" stroke="#fff" strokeWidth={2} />
            </g>
          )}
        </svg>
        {hp && (
          <div
            className="absolute top-0 pointer-events-none card shadow-[var(--shadow-pop)] px-3 py-2 text-xs"
            style={{
              left: `${(x(hover!) / W) * 100}%`,
              transform: hover! > points.length / 2 ? "translateX(calc(-100% - 12px))" : "translateX(12px)",
            }}
          >
            <div className="text-muted mb-1">{fmtDate(hp.snapshot_date)}</div>
            <div className="font-semibold text-sm tabular-nums">{f.inr(hp.current_value)}</div>
            <div className="tabular-nums text-muted">Invested {f.inr(hp.invested)}</div>
            <div className={`tabular-nums ${hp.pnl >= 0 ? "text-gain" : "text-loss"}`}>
              {f.signedInr(hp.pnl)} ({fmt.pct(hp.pnl_percentage)})
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function fmtDate(d: string) {
  return new Date(d).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" });
}
