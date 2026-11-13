"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { QueryView } from "@/components/query";
import { Card, Empty, LinkButton, PageHeader, ResultBar, SeverityBadge, Stat, StateBadge, Table } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import type { Overview, Severity, SystemHealth } from "@/lib/types";

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export default function OverviewPage() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/system/overview"), refetchInterval: 10_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<SystemHealth>("/system/health"), refetchInterval: 30_000 });

  return (
    <>
      <PageHeader
        title="Overview"
        description="Everything here is counted from stored test results and findings. Checks without a verdict are shown as such."
        actions={<LinkButton href="/projects/import" variant="primary">Import project</LinkButton>}
      />
      <QueryView query={overview}>
        {(data) =>
          data.projects === 0 ? (
            <Empty title="No projects yet" action={<LinkButton href="/projects/import" variant="primary">Import your first project</LinkButton>}>
              Import a local folder, a ZIP archive, an OpenAPI file or a running URL to generate an architecture inventory and a QA plan.
            </Empty>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                <Stat label="Projects" value={data.projects} href="/projects" />
                <Stat label="Test runs" value={data.runs} href="/runs" />
                <Stat label="Open findings" value={data.open_findings} href="/findings" />
                <Stat label="Repairs awaiting review" value={data.pending_repairs} href="/repairs" />
                <Stat label="Verified fixes" value={data.verified_fixes} hint="before/after evidence" href="/repairs" />
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <Card title="Open findings by severity">
                  {data.open_findings === 0 ? (
                    <p className="text-muted">No open findings.</p>
                  ) : (
                    <ul className="space-y-2">
                      {SEVERITIES.filter((s) => data.findings_by_severity[s]).map((severity) => (
                        <li key={severity} className="flex items-center justify-between">
                          <SeverityBadge severity={severity} />
                          <Link href={`/findings?severity=${severity}`} className="font-medium tabular-nums underline-offset-2 hover:underline">
                            {data.findings_by_severity[severity]}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
                <Card title="Latest completed run">
                  {data.latest_completed_run ? (
                    <div className="space-y-2">
                      <Link href={`/runs/${data.latest_completed_run.id}`} className="font-medium underline-offset-2 hover:underline">
                        {data.latest_completed_run.plan_name}
                      </Link>
                      <p className="text-xs text-muted">Finished {formatDate(data.latest_completed_run.finished_at)}</p>
                      <ResultBar summary={data.latest_completed_run.summary} />
                    </div>
                  ) : (
                    <p className="text-muted">No run has completed yet.</p>
                  )}
                </Card>
              </div>
              <Card title="Recent runs" action={<Link href="/runs" className="text-sm text-accent">All runs</Link>}>
                {data.recent_runs.length === 0 ? (
                  <p className="text-muted">No runs yet. Open a test plan and start one.</p>
                ) : (
                  <Table label="Recent runs" head={["Plan", "Status", "Results", "Started"]}>
                    {data.recent_runs.map((run) => (
                      <tr key={run.id}>
                        <td><Link href={`/runs/${run.id}`} className="font-medium underline-offset-2 hover:underline">{run.plan_name}</Link></td>
                        <td><StateBadge state={run.status} /></td>
                        <td className="min-w-52"><ResultBar summary={run.summary} /></td>
                        <td className="whitespace-nowrap text-muted">{formatDate(run.started_at ?? run.created_at)}</td>
                      </tr>
                    ))}
                  </Table>
                )}
              </Card>
            </div>
          )
        }
      </QueryView>
      <div className="mt-4">
        <Card title="System" action={<Link href="/system" className="text-sm text-accent">Details</Link>}>
          <QueryView query={health}>
            {(data) => (
              <ul className="flex flex-wrap gap-2">
                {Object.entries(data.checks).map(([name, check]) => (
                  <li key={name} title={check.detail}>
                    <StateBadge state={check.ok ? "ready" : check.optional ? "not_implemented" : "unavailable"} /> <span className="text-muted">{name.replaceAll("_", " ")}</span>
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
        </Card>
      </div>
    </>
  );
}
