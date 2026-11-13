"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Accessibility, Activity, Bot, Boxes, Bug, ClipboardList, FileText, FolderGit2, Gauge, HeartPulse, LayoutDashboard,
  Menu, Plug, ScrollText, Settings2, ShieldAlert, Wrench, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { cx } from "@/components/ui";

const NAV = [
  {
    group: "Workspace",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/projects", label: "Projects", icon: FolderGit2 },
      { href: "/plans", label: "Test Plans", icon: ClipboardList },
      { href: "/runs", label: "Test Runs", icon: Activity },
    ],
  },
  {
    group: "Quality",
    items: [
      { href: "/findings", label: "Bugs & Findings", icon: Bug },
      { href: "/repairs", label: "Fix Review", icon: Wrench },
      { href: "/security", label: "Security", icon: ShieldAlert },
      { href: "/performance", label: "Performance", icon: Gauge },
      { href: "/accessibility", label: "Accessibility & UX", icon: Accessibility },
      { href: "/reports", label: "Reports", icon: FileText },
    ],
  },
  {
    group: "Administration",
    items: [
      { href: "/settings/ai", label: "AI Models", icon: Bot },
      { href: "/settings/execution", label: "Execution", icon: Settings2 },
      { href: "/integrations", label: "Integrations", icon: Plug },
      { href: "/agents", label: "Agents", icon: Boxes },
      { href: "/audit", label: "Audit Log", icon: ScrollText },
      { href: "/system", label: "System Health", icon: HeartPulse },
    ],
  },
];

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function Shell({ children }: { children: ReactNode }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false } } }),
  );
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  return (
    <QueryClientProvider client={client}>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-surface focus:p-2">
        Skip to content
      </a>
      <div className="flex min-h-screen">
        <aside
          className={cx(
            "fixed inset-y-0 left-0 z-40 w-60 shrink-0 overflow-y-auto border-r border-line bg-surface lg:static lg:block",
            open ? "block" : "hidden",
          )}
        >
          <div className="flex items-center justify-between px-4 py-4">
            <Link href="/" className="font-semibold tracking-tight">
              QA Intelligence
            </Link>
            <button type="button" className="lg:hidden" aria-label="Close navigation" onClick={() => setOpen(false)}>
              <X size={18} aria-hidden="true" />
            </button>
          </div>
          <nav aria-label="Main" className="px-2 pb-6">
            {NAV.map((section) => (
              <div key={section.group} className="mb-4">
                <p className="px-2 pb-1 text-xs font-medium tracking-wide text-muted uppercase">{section.group}</p>
                <ul>
                  {section.items.map(({ href, label, icon: Icon }) => {
                    const active = isActive(pathname, href);
                    return (
                      <li key={href}>
                        <Link
                          href={href}
                          aria-current={active ? "page" : undefined}
                          onClick={() => setOpen(false)}
                          className={cx(
                            "flex items-center gap-2 rounded-md px-2 py-1.5",
                            active ? "bg-raised font-medium text-ink" : "text-muted hover:bg-raised hover:text-ink",
                          )}
                        >
                          <Icon size={16} aria-hidden="true" />
                          {label}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>
        </aside>
        <div className="min-w-0 flex-1">
          <header className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2 lg:hidden">
            <button type="button" aria-label="Open navigation" aria-expanded={open} onClick={() => setOpen(true)}>
              <Menu size={20} aria-hidden="true" />
            </button>
            <span className="font-semibold">QA Intelligence</span>
          </header>
          <main id="main" className="mx-auto max-w-7xl px-4 py-6 lg:px-8">
            {children}
          </main>
        </div>
      </div>
    </QueryClientProvider>
  );
}
