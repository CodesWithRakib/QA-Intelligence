import Link from "next/link";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

import type { ResultStatus, Severity } from "@/lib/types";

type Tone = "ok" | "bad" | "warn" | "info" | "idle";

const TONES: Record<Tone, string> = {
  ok: "bg-ok-bg text-ok",
  bad: "bg-bad-bg text-bad",
  warn: "bg-warn-bg text-warn",
  info: "bg-info-bg text-info",
  idle: "bg-idle-bg text-idle",
};

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Badge({ tone = "idle", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={cx("inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", TONES[tone])}>
      {children}
    </span>
  );
}

const RESULT: Record<ResultStatus, { tone: Tone; label: string }> = {
  passed: { tone: "ok", label: "Passed" },
  failed: { tone: "bad", label: "Failed" },
  blocked: { tone: "warn", label: "Blocked" },
  skipped: { tone: "idle", label: "Skipped" },
  not_tested: { tone: "idle", label: "Not tested" },
  not_applicable: { tone: "idle", label: "Not applicable" },
};

export const RESULT_ORDER: ResultStatus[] = ["failed", "blocked", "passed", "skipped", "not_tested", "not_applicable"];

export function resultLabel(status: ResultStatus): string {
  return RESULT[status].label;
}

export function ResultBadge({ status }: { status: ResultStatus }) {
  return <Badge tone={RESULT[status].tone}>{RESULT[status].label}</Badge>;
}

const SEVERITY: Record<Severity, Tone> = { critical: "bad", high: "bad", medium: "warn", low: "info", info: "idle" };

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge tone={SEVERITY[severity]}>{severity}</Badge>;
}

const STATE_TONES: Record<string, Tone> = {
  completed: "ok", verified: "ok", verified_fixed: "ok", fixed: "ok", succeeded: "ok", ready: "ok", approved: "info",
  running: "info", queued: "idle", proposed: "warn", fix_proposed: "warn", open: "bad", failed: "bad",
  review_rejected: "bad", verification_failed: "bad", rejected: "idle", unavailable: "warn", not_implemented: "idle",
  timed_out: "warn", cancelled: "idle", wont_fix: "idle", false_positive: "idle", unverified: "idle",
};

/** Generic lifecycle state (runs, repairs, findings, jobs). */
export function StateBadge({ state }: { state: string }) {
  return <Badge tone={STATE_TONES[state] ?? "idle"}>{state.replaceAll("_", " ")}</Badge>;
}

export function Card({ title, action, children, className }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-lg border border-line bg-surface", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";
const BUTTON_VARIANTS = {
  primary: "bg-accent text-accent-ink hover:opacity-90",
  secondary: "border border-line bg-surface hover:bg-raised",
  danger: "border border-bad text-bad hover:bg-bad-bg",
};

type Variant = keyof typeof BUTTON_VARIANTS;

export function Button({ variant = "secondary", className, type = "button", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return <button type={type} className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], className)} {...props} />;
}

export function LinkButton({ href, variant = "secondary", children }: { href: string; variant?: Variant; children: ReactNode }) {
  return (
    <Link href={href} className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant])}>
      {children}
    </Link>
  );
}

const CONTROL = "w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm placeholder:text-muted disabled:opacity-60";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(CONTROL, className)} {...props} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cx(CONTROL, className)} {...props} />;
}

export function Field({ label, htmlFor, hint, error, children }: { label: string; htmlFor: string; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-sm font-medium">
        {label}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-muted">{hint}</p>}
      {error && (
        <p role="alert" className="mt-1 text-xs text-bad">
          {error}
        </p>
      )}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2 py-2">
      <span className="sr-only">{label}…</span>
      {[0, 1, 2].map((row) => (
        <div key={row} className="h-8 animate-pulse rounded bg-raised" />
      ))}
    </div>
  );
}

export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-bad bg-bad-bg px-3 py-2 text-bad">
      <span>{message}</span>
      {retry && <Button onClick={retry}>Retry</Button>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: Tone; children: ReactNode }) {
  return <div className={cx("rounded-md px-3 py-2 text-sm", TONES[tone])}>{children}</div>;
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-line px-4 py-8 text-center">
      <p className="font-medium">{title}</p>
      {children && <p className="mx-auto mt-1 max-w-xl text-muted">{children}</p>}
      {action && <div className="mt-3 flex justify-center">{action}</div>}
    </div>
  );
}

export function Table({ head, children, label }: { head: ReactNode[]; children: ReactNode; label: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm" aria-label={label}>
        <thead>
          <tr className="border-b border-line text-xs tracking-wide text-muted uppercase">
            {head.map((cell, index) => (
              <th key={index} scope="col" className="px-2 py-2 font-medium whitespace-nowrap">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="[&>tr]:border-b [&>tr]:border-line [&>tr:last-child]:border-0 [&>tr>td]:px-2 [&>tr>td]:py-2 [&>tr>td]:align-top">
          {children}
        </tbody>
      </table>
    </div>
  );
}

export function Stat({ label, value, hint, href }: { label: string; value: ReactNode; hint?: ReactNode; href?: string }) {
  const body = (
    <>
      <p className="text-xs tracking-wide text-muted uppercase">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
    </>
  );
  const className = "block rounded-lg border border-line bg-surface p-4";
  return href ? (
    <Link href={href} className={cx(className, "hover:bg-raised")}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

export function Pre({ children }: { children: ReactNode }) {
  return <pre className="max-h-96 overflow-auto rounded-md bg-raised p-3 font-mono text-xs break-words whitespace-pre-wrap">{children}</pre>;
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= limit) return null;
  return (
    <nav aria-label="Pagination" className="mt-3 flex items-center justify-between text-sm text-muted">
      <span>
        {offset + 1}-{Math.min(offset + limit, total)} of {total}
      </span>
      <span className="flex gap-2">
        <Button disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          Next
        </Button>
      </span>
    </nav>
  );
}

/** Proportional bar of result states with a text legend (never colour alone). */
export function ResultBar({ summary }: { summary: Partial<Record<ResultStatus | "total", number>> }) {
  const total = summary.total ?? 0;
  if (!total) return <span className="text-muted">No results yet</span>;
  const colours: Record<ResultStatus, string> = {
    passed: "bg-ok", failed: "bg-bad", blocked: "bg-warn", skipped: "bg-idle", not_tested: "bg-idle-bg", not_applicable: "bg-line",
  };
  const present = RESULT_ORDER.filter((status) => (summary[status] ?? 0) > 0);
  return (
    <div>
      <div className="flex h-2 overflow-hidden rounded bg-raised" aria-hidden="true">
        {present.map((status) => (
          <div key={status} className={colours[status]} style={{ width: `${((summary[status] ?? 0) / total) * 100}%` }} />
        ))}
      </div>
      <p className="mt-1 text-xs text-muted">{present.map((status) => `${summary[status]} ${resultLabel(status).toLowerCase()}`).join(" · ")}</p>
    </div>
  );
}
