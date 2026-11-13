"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { pollWhile, QueryView } from "@/components/query";
import { Card, Empty, LinkButton, PageHeader, Pager, ResultBar, Select, StateBadge, Table } from "@/components/ui";
import { api, formatDate, query } from "@/lib/api";
import type { Page, TestRun } from "@/lib/types";

const STATES = ["queued", "running", "completed", "failed"];

export default function RunsPage() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const runs = useQuery({
    queryKey: ["runs", status, offset],
    queryFn: () => api<Page<TestRun>>(`/runs${query({ run_status: status, offset, limit: 25 })}`),
    refetchInterval: pollWhile<Page<TestRun>>((page) => page.items.some((run) => run.status === "queued" || run.status === "running")),
  });

  return (
    <>
      <PageHeader title="Test runs" description="A run executes every enabled case of a plan through its engine. “Failed” as a run status means the run itself crashed, not that tests failed." />
      <Card>
        <div className="mb-3 max-w-48">
          <label htmlFor="run-status" className="sr-only">Filter by status</label>
          <Select id="run-status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
            <option value="">All statuses</option>
            {STATES.map((state) => (
              <option key={state} value={state}>{state}</option>
            ))}
          </Select>
        </div>
        <QueryView query={runs}>
          {(data) =>
            data.items.length === 0 ? (
              <Empty title="No runs found" action={<LinkButton href="/plans" variant="primary">Open test plans</LinkButton>}>
                Start a run from a test plan.
              </Empty>
            ) : (
              <>
                <Table label="Test runs" head={["Plan", "Status", "Results", "Target", "Started", "Finished"]}>
                  {data.items.map((run) => (
                    <tr key={run.id}>
                      <td><Link href={`/runs/${run.id}`} className="font-medium underline-offset-2 hover:underline">{run.plan_name}</Link></td>
                      <td><StateBadge state={run.status} /></td>
                      <td className="min-w-56"><ResultBar summary={run.summary} /></td>
                      <td className="max-w-48 truncate text-muted">{run.target_url ?? "static only"}</td>
                      <td className="whitespace-nowrap text-muted">{formatDate(run.started_at)}</td>
                      <td className="whitespace-nowrap text-muted">{formatDate(run.finished_at)}</td>
                    </tr>
                  ))}
                </Table>
                <Pager total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
              </>
            )
          }
        </QueryView>
      </Card>
    </>
  );
}
