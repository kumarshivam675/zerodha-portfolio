import { Suspense } from "react";
import Dashboard from "@/components/Dashboard";

export default function Home() {
  return (
    <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
      <Dashboard />
    </Suspense>
  );
}
