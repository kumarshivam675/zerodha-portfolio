"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { History, LayoutDashboard, Sparkles, TrendingUp } from "lucide-react";
import { api, type KiteStatus } from "@/lib/api";
import { PrivacyToggle } from "@/lib/privacy";

const LINKS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/snapshots", label: "Snapshots", icon: History },
  { href: "/agent", label: "Analyst", icon: Sparkles },
];

export default function Sidebar() {
  const pathname = usePathname();
  const [kite, setKite] = useState<KiteStatus | null>(null);

  useEffect(() => {
    api<KiteStatus>("/kite/status").then(setKite, () => {});
  }, [pathname]);

  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <>
      {/* Desktop */}
      <aside className="hidden lg:flex fixed inset-y-0 left-0 w-60 flex-col border-r border-border bg-surface px-3 py-5">
        <Link href="/" className="flex items-center gap-2.5 px-2.5 mb-8">
          <span className="size-8 rounded-lg bg-accent text-white grid place-items-center shadow-sm">
            <TrendingUp className="size-4.5" />
          </span>
          <span className="font-semibold tracking-tight">Portfolio</span>
        </Link>
        <nav className="flex flex-col gap-0.5">
          {LINKS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 rounded-lg px-2.5 h-9 text-sm font-medium transition-colors ${
                isActive(href) ? "bg-accent-soft text-accent" : "text-muted hover:text-foreground hover:bg-background"
              }`}
            >
              <Icon className="size-4.5" />
              {label}
            </Link>
          ))}
        </nav>
        <div className="mt-auto rounded-xl border border-border bg-surface-2 p-3">
          <div className="flex items-center gap-2 text-xs font-medium">
            <span className={`size-2 rounded-full ${kite?.connected ? "bg-gain" : "bg-subtle"}`} />
            {kite?.connected ? "Zerodha connected" : "Zerodha not connected"}
          </div>
          <div className="text-xs text-muted mt-1 truncate">
            {kite?.connected ? (kite.user_name ?? kite.user_id) : "Log in to sync holdings"}
          </div>
        </div>
      </aside>

      {/* Mobile */}
      <header className="lg:hidden sticky top-0 z-40 border-b border-border bg-surface/90 backdrop-blur">
        <div className="flex items-center gap-1 px-4 h-14">
          <span className="size-7 rounded-lg bg-accent text-white grid place-items-center mr-2">
            <TrendingUp className="size-4" />
          </span>
          {LINKS.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={`rounded-lg px-2.5 h-8 grid place-items-center text-sm font-medium ${
                isActive(href) ? "bg-accent-soft text-accent" : "text-muted"
              }`}
            >
              {label}
            </Link>
          ))}
          <PrivacyToggle className="ml-auto" />
        </div>
      </header>
    </>
  );
}
