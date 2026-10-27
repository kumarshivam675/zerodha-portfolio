"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { CheckCircle2, AlertCircle, X } from "lucide-react";
import { PrivacyToggle } from "@/lib/privacy";

/* ---------- Change pill: sign + arrow so gain/loss is never colour-only ---------- */

export function ChangePill({ value, children }: { value: number | null | undefined; children: ReactNode }) {
  const cls = value == null || value === 0 ? "pill-neutral" : value > 0 ? "pill-gain" : "pill-loss";
  const arrow = value == null || value === 0 ? "" : value > 0 ? "▲" : "▼";
  return (
    <span className={`pill ${cls}`}>
      {arrow && <span className="text-[0.6rem]">{arrow}</span>}
      {children}
    </span>
  );
}

/* ---------- Page header ---------- */

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <div className="text-sm text-muted mt-1">{subtitle}</div>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {actions}
        <PrivacyToggle labelled className="hidden lg:inline-flex" />
      </div>
    </div>
  );
}

/* ---------- Empty state ---------- */

export function EmptyState({ icon, title, body, action }: { icon: ReactNode; title: string; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center text-center px-6 py-12">
      <div className="size-11 rounded-xl bg-accent-soft text-accent grid place-items-center mb-3">{icon}</div>
      <div className="font-medium">{title}</div>
      {body && <div className="text-sm text-muted mt-1 max-w-sm">{body}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/* ---------- Modal ---------- */

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      <div className="absolute inset-0 bg-slate-900/30 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label={title} className="relative w-full max-w-md card shadow-[var(--shadow-pop)] animate-toast">
        <div className="flex items-center justify-between px-5 pt-4">
          <h2 className="font-semibold">{title}</h2>
          <button onClick={onClose} className="btn btn-ghost btn-sm -mr-2" aria-label="Close">
            <X className="size-4" />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 px-5 pb-4">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------- Toasts ---------- */

type Toast = { id: number; kind: "success" | "error"; text: string };
const ToastContext = createContext<(kind: Toast["kind"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = ++nextId.current;
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 4000);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-[min(380px,calc(100vw-2rem))]">
        {toasts.map((t) => (
          <div key={t.id} className="card shadow-[var(--shadow-pop)] animate-toast flex items-start gap-3 px-4 py-3 text-sm">
            {t.kind === "success" ? (
              <CheckCircle2 className="size-5 text-gain shrink-0" />
            ) : (
              <AlertCircle className="size-5 text-loss shrink-0" />
            )}
            <span className="flex-1">{t.text}</span>
            <button onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))} aria-label="Dismiss" className="text-subtle hover:text-foreground">
              <X className="size-4" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

/* ---------- Skeleton ---------- */

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}
