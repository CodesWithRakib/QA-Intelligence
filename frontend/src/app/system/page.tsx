"use client";

import { useQuery } from "@tanstack/react-query";

import { QueryView } from "@/components/query";
import { Button, Card, Notice, PageHeader, StateBadge, Table } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import type { Page, SystemHealth } from "@/lib/types";

interface Job {
  id: string;
  kind: string;
  status: string;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export default function SystemPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<SystemHealth>("/system/health"), refetchInterval: 15_000 });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api<Page<Job>>("/system/jobs?limit=20"), refetchInterval: 15_000 });
  return (
    <>
      <PageHeader
        title="System health"
        description="Live checks of the components the platform depends on."
        actions={
          <Button onClick={() => void health.refetch()} disabled={health.isFetching}>
            {health.isFetching ? "Checking…" : "Check again"}
          </Button>
        }
      />
      <div className="space-y-4">
        <QueryView query={health}>
          {(data) => (
            <>
              <Notice tone={data.healthy ? "ok" : "warn"}>
                {data.healthy
                  ? "All required components are available."
                  : "One or more required components are unavailable. Affected checks will be reported as blocked."}{" "}
                Version {data.version} · {data.queued_jobs} queued job(s) · checked {formatDate(data.checked_at)}
              </Notice>
              <Card title="Components">
                <Table label="Component health" head={["Component", "Status", "Detail"]}>
                  {Object.entries(data.checks).map(([name, check]) => (
                    <tr key={name}>
                      <td className="font-medium">
                        {name.replaceAll("_", " ")}
                        {check.optional ? " (optional)" : ""}
                      </td>
                      <td><StateBadge state={check.ok ? "ready" : "unavailable"} /></td>
                      <td className="text-muted">{check.detail}</td>
                    </tr>
                  ))}
                </Table>
              </Card>
            </>
          )}
        </QueryView>
        <Card title="Recent background jobs">
          <QueryView query={jobs}>
            {(data) =>
              data.items.length === 0 ? (
                <p className="text-muted">No jobs have been queued yet.</p>
              ) : (
                <Table label="Background jobs" head={["Queued", "Kind", "Status", "Finished", "Error"]}>
                  {data.items.map((job) => (
                    <tr key={job.id}>
                      <td className="whitespace-nowrap text-muted">{formatDate(job.created_at)}</td>
                      <td className="font-mono text-xs">{job.kind}</td>
                      <td><StateBadge state={job.status} /></td>
                      <td className="whitespace-nowrap text-muted">{formatDate(job.finished_at)}</td>
                      <td className="max-w-md break-words text-bad">{job.error ?? ""}</td>
                    </tr>
                  ))}
                </Table>
              )
            }
          </QueryView>
        </Card>
      </div>
    </>
  );
}
