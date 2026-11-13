"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { FindingsTable } from "@/components/findings-table";
import { QueryView } from "@/components/query";
import { Card, PageHeader, ResultBadge, Table } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import type { Category, Overview, RunDetail } from "@/lib/types";

interface Props {
  title: string;
  description: string;
  categories: [Category, ...Category[]];
}

/** Shared layout for the Security, Performance and Accessibility screens. */
export function CategoryView({ title, description, categories }: Props) {
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/system/overview") });
  const latestId = overview.data?.latest_completed_run?.id;
  const run = useQuery({ queryKey: ["run", latestId], queryFn: () => api<RunDetail>(`/runs/${latestId}`), enabled: !!latestId });

  return (
    <>
      <PageHeader title={title} description={description} />
      <div className="space-y-4">
        <Card title="Checks in the latest completed run">
          <QueryView query={overview}>
            {(data) =>
              !data.latest_completed_run ? (
                <p className="text-muted">No run has completed yet, so nothing in this area has been tested.</p>
              ) : (
                <QueryView query={run}>
                  {(detail) => {
                    const results = detail.results.filter((r) => categories.includes(r.case.category));
                    return results.length === 0 ? (
                      <p className="text-muted">
                        The latest run (<Link className="underline" href={`/runs/${detail.id}`}>{detail.plan_name}</Link>) planned no checks in this area.
                      </p>
                    ) : (
                      <>
                        <p className="mb-2 text-xs text-muted">
                          From <Link className="underline" href={`/runs/${detail.id}`}>{detail.plan_name}</Link>, finished {formatDate(detail.finished_at)}
                        </p>
                        <Table label={`${title} checks`} head={["State", "Case", "Detail"]}>
                          {results.map((result) => (
                            <tr key={result.id}>
                              <td><ResultBadge status={result.status} /></td>
                              <td><span className="font-mono text-xs">{result.case.key}</span> {result.case.title}</td>
                              <td className="text-muted">{result.message}</td>
                            </tr>
                          ))}
                        </Table>
                      </>
                    );
                  }}
                </QueryView>
              )
            }
          </QueryView>
        </Card>
        {categories.map((category) => (
          <Card key={category} title={`Findings: ${category}`}>
            <FindingsTable category={category} />
          </Card>
        ))}
      </div>
    </>
  );
}
