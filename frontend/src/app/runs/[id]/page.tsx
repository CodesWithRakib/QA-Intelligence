"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { EvidenceList } from "@/components/evidence";
import { pollWhile, QueryView, useAction } from "@/components/query";
import { Badge, Button, Card, ErrorNote, LinkButton, Notice, PageHeader, Pre, RESULT_ORDER, ResultBadge, resultLabel, Select, Stat, StateBadge } from "@/components/ui";
import { api, formatDate, formatDuration } from "@/lib/api";
import type { Page, Report, ResultStatus, RunComparison, RunDetail, TestRun } from "@/lib/types";

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [filter, setFilter] = useState<ResultStatus | "">("");
  const run = useQuery({
    queryKey: ["run", id],
    queryFn: () => api<RunDetail>(`/runs/${id}`),
    refetchInterval: pollWhile<RunDetail>((data) => data.status === "queued" || data.status === "running"),
  });
  const report = useAction<void, Report>("POST", `/runs/${id}/report`);
  const cancel = useAction("POST", `/runs/${id}/cancel`);
  const projectId = run.data?.project_id;
  const siblings = useQuery({
    queryKey: ["runs", projectId, "for-compare"],
    queryFn: () => api<Page<TestRun>>(`/runs?project_id=${projectId}&limit=20`),
    enabled: !!projectId,
  });
  const createdAt = run.data?.created_at;
  const previous = siblings.data?.items.find((other) => other.id !== id && !!createdAt && other.created_at < createdAt && other.status === "completed");
  const comparison = useQuery({
    queryKey: ["run-compare", id, previous?.id],
    queryFn: () => api<RunComparison>(`/runs/${id}/compare/${previous?.id}`),
    enabled: !!previous && run.data?.status === "completed",
  });

  return (
    <QueryView query={run}>
      {(data) => {
        const active = data.status === "queued" || data.status === "running";
        const results = data.results.filter((r) => !filter || r.status === filter);
        const noVerdict = (data.summary.blocked ?? 0) + (data.summary.not_tested ?? 0) + (data.summary.skipped ?? 0);
        return (
          <>
            <PageHeader
              title={`Test results: ${data.plan_name}`}
              description={`Started ${formatDate(data.started_at)} · target: ${data.target_url ?? "static analysis only"}`}
              actions={
                <>
                  <StateBadge state={data.status} />
                  {active && (
                    <Button variant="danger" disabled={cancel.isPending || data.cancel_requested} onClick={() => cancel.mutate()}>
                      {data.cancel_requested ? "Cancelling…" : "Cancel run"}
                    </Button>
                  )}
                  <LinkButton href={`/findings?project_id=${data.project_id}`}>Findings</LinkButton>
                  <Button variant="primary" disabled={active || report.isPending} onClick={() => report.mutate(undefined, { onSuccess: (r) => router.push(`/reports/${r.id}`) })}>
                    {report.isPending ? "Generating…" : "Generate report"}
                  </Button>
                </>
              }
            />
            <div className="space-y-4">
              {report.isError && <ErrorNote error={report.error} />}
              {cancel.isError && <ErrorNote error={cancel.error} />}
              {active && (
                <Notice>
                  <span className="font-medium">
                    {data.progress.case ? `Running ${data.progress.index} of ${data.progress.total}: ${data.progress.case}` : "Waiting for the worker…"}
                  </span>
                  {data.progress.title && <span className="block">{data.progress.title}</span>}
                  <span className="block text-xs">
                    {data.progress.adapter ? `Adapter: ${data.progress.adapter.replaceAll("_", " ")} · ` : ""}
                    {data.results.length} of {data.planned_cases} cases have a stored result. One case runs at a time; this page refreshes automatically.
                  </span>
                </Notice>
              )}
              {data.status === "cancelled" && <Notice tone="warn">The run was cancelled. Cases that had not started are recorded as skipped.</Notice>}
              {comparison.data && comparison.data.changes.length > 0 && (
                <Card title={`Compared with the previous completed run (${formatDate(comparison.data.base.created_at)})`}>
                  <p className="mb-2 text-muted">
                    {comparison.data.regressions} regression(s), {comparison.data.fixed} fixed, {comparison.data.unchanged} unchanged.
                  </p>
                  <ul className="space-y-1">
                    {comparison.data.changes.map((change) => (
                      <li key={`${change.category}${change.case}`}>
                        <StateBadge state={change.kind} /> {change.case}{" "}
                        <span className="text-xs text-muted">({change.before ?? "not planned"} → {change.after ?? "not planned"})</span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
              {comparison.data && comparison.data.changes.length === 0 && <Notice>No result changed compared with the previous completed run.</Notice>}
              {data.error && <Notice tone="bad">The run did not complete: {data.error}</Notice>}
              {!active && noVerdict > 0 && (
                <Notice tone="warn">{noVerdict} check(s) have no verdict (blocked, skipped or not tested). They are not counted as passing.</Notice>
              )}
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
                {RESULT_ORDER.map((status) => (
                  <Stat key={status} label={resultLabel(status)} value={data.summary[status] ?? 0} />
                ))}
              </div>
              <Card
                title={`Results (${results.length})`}
                action={
                  <div className="w-44">
                    <label htmlFor="result-filter" className="sr-only">Filter results by state</label>
                    <Select id="result-filter" value={filter} onChange={(e) => setFilter(e.target.value as ResultStatus | "")}>
                      <option value="">All states</option>
                      {RESULT_ORDER.map((status) => (
                        <option key={status} value={status}>{resultLabel(status)}</option>
                      ))}
                    </Select>
                  </div>
                }
              >
                {results.length === 0 ? (
                  <p className="text-muted">{active ? "Waiting for the first result…" : "No results in this state."}</p>
                ) : (
                  <ul className="divide-y divide-line">
                    {results.map((result) => (
                      <li key={result.id} className="py-2">
                        <details>
                          <summary className="flex cursor-pointer flex-wrap items-center gap-2">
                            <ResultBadge status={result.status} />
                            <span className="font-mono text-xs">{result.case.key}</span>
                            <span className="min-w-0 flex-1">{result.case.title}</span>
                            <Badge>{result.case.category}</Badge>
                            <span className="text-xs text-muted tabular-nums">{formatDuration(result.duration_ms)}</span>
                          </summary>
                          <div className="mt-2 space-y-3 pl-1">
                            <p>{result.message || "No message."}</p>
                            {Object.keys(result.details).length > 0 && <Pre>{JSON.stringify(result.details, null, 2)}</Pre>}
                            <EvidenceList items={result.evidence} />
                          </div>
                        </details>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          </>
        );
      }}
    </QueryView>
  );
}
