"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";

import { QueryView } from "@/components/query";
import { Badge, Card, Empty, LinkButton, Notice, PageHeader, ResultBar, StateBadge, Table } from "@/components/ui";
import { api, formatBytes, formatDate } from "@/lib/api";
import type { Page, Project, TestPlan, TestRun } from "@/lib/types";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const project = useQuery({ queryKey: ["project", id], queryFn: () => api<Project>(`/projects/${id}`) });
  const plans = useQuery({ queryKey: ["plans", id], queryFn: () => api<Page<TestPlan>>(`/plans?project_id=${id}`) });
  const runs = useQuery({ queryKey: ["runs", id], queryFn: () => api<Page<TestRun>>(`/runs?project_id=${id}&limit=10`) });

  return (
    <QueryView query={project}>
      {(data) => {
        const source = data.sources[0];
        const excluded = source?.config.excluded_secret_files ?? [];
        return (
          <>
            <PageHeader
              title={data.name}
              description={data.description || undefined}
              actions={
                <>
                  <LinkButton href={`/projects/${id}/architecture`}>Architecture</LinkButton>
                  <LinkButton href={`/findings?project_id=${id}`}>Findings</LinkButton>
                  <LinkButton href={`/plans?project_id=${id}`} variant="primary">New test plan</LinkButton>
                </>
              }
            />
            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Source">
                <dl className="grid grid-cols-[8rem_1fr] gap-y-1.5">
                  <dt className="text-muted">Type</dt>
                  <dd><Badge>{source?.kind.replace("_", " ") ?? "-"}</Badge></dd>
                  <dt className="text-muted">Location</dt>
                  <dd className="font-mono text-xs break-all">{source?.location ?? "-"}</dd>
                  <dt className="text-muted">Target URL</dt>
                  <dd className="break-all">{data.target_url ?? "not set"}</dd>
                  <dt className="text-muted">Imported</dt>
                  <dd>{formatDate(data.created_at)}</dd>
                </dl>
                {excluded.length > 0 && (
                  <div className="mt-3">
                    <Notice tone="warn">
                      {excluded.length} secret-like file(s) were not copied into the workspace: {excluded.slice(0, 5).join(", ")}
                      {excluded.length > 5 ? "…" : ""}
                    </Notice>
                  </div>
                )}
              </Card>
              <Card title="Versions">
                <Table label="Versions" head={["Version", "Files", "Size", "Content hash", "Imported"]}>
                  {data.versions.map((version) => (
                    <tr key={version.id}>
                      <td className="tabular-nums">v{version.number}</td>
                      <td className="tabular-nums">{version.file_count}</td>
                      <td className="whitespace-nowrap">{formatBytes(version.size_bytes)}</td>
                      <td className="font-mono text-xs">{version.content_hash.slice(0, 12)}</td>
                      <td className="whitespace-nowrap text-muted">{formatDate(version.created_at)}</td>
                    </tr>
                  ))}
                </Table>
              </Card>
            </div>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <Card title="Test plans">
                <QueryView query={plans}>
                  {(page) =>
                    page.items.length === 0 ? (
                      <Empty title="No test plan yet" action={<LinkButton href={`/plans?project_id=${id}`} variant="primary">Generate a plan</LinkButton>} />
                    ) : (
                      <ul className="space-y-2">
                        {page.items.map((plan) => (
                          <li key={plan.id} className="flex items-center justify-between gap-2">
                            <Link href={`/plans/${plan.id}`} className="font-medium underline-offset-2 hover:underline">{plan.name}</Link>
                            <span className="text-xs text-muted">{plan.case_count} cases · {formatDate(plan.created_at)}</span>
                          </li>
                        ))}
                      </ul>
                    )
                  }
                </QueryView>
              </Card>
              <Card title="Recent runs">
                <QueryView query={runs}>
                  {(page) =>
                    page.items.length === 0 ? (
                      <p className="text-muted">No runs yet.</p>
                    ) : (
                      <ul className="space-y-3">
                        {page.items.map((run) => (
                          <li key={run.id}>
                            <div className="flex items-center justify-between gap-2">
                              <Link href={`/runs/${run.id}`} className="font-medium underline-offset-2 hover:underline">{formatDate(run.created_at)}</Link>
                              <StateBadge state={run.status} />
                            </div>
                            <ResultBar summary={run.summary} />
                          </li>
                        ))}
                      </ul>
                    )
                  }
                </QueryView>
              </Card>
            </div>
          </>
        );
      }}
    </QueryView>
  );
}
