import { Suspense } from "react";
import SnapshotDetail from "@/components/SnapshotDetail";

export default function SnapshotPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
      <SnapshotDetail />
    </Suspense>
  );
}
