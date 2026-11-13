"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { pollWhile, QueryView } from "@/components/query";
import { Badge, Card, Empty, PageHeader, Pager, Select, SeverityBadge, StateBadge, Table } from "@/components/ui";
import { api, formatDate, query } from "@/lib/api";
import type { Page, RepairProposal } from "@/lib/types";

const STATES = ["proposed", "approved", "verified", "failed", "rejected"];

export default function RepairsPage() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const repairs = useQuery({
    queryKey: ["repairs", status, offset],
    queryFn: () => api<Page<RepairProposal>>(`/repairs${query({ proposal_status: status, offset, limit: 25 })}`),
    refetchInterval: pollWhile<Page<RepairProposal>>((page) => page.items.some((p) => p.status === "approved")),
  });

  return (
    <>
      <PageHeader
        title="Fix review"
        description="Proposed patches wait here for your decision. Approved patches are applied on a git branch in an isolated copy, reviewed against policy, and verified with the same checks before and after."
      />
      <Card>
        <div className="mb-3 max-w-48">
          <label htmlFor="repair-status" className="sr-only">Filter by status</label>
          <Select id="repair-status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
            <option value="">All statuses</option>
            {STATES.map((state) => <option key={state} value={state}>{state}</option>)}
          </Select>
        </div>
        <QueryView query={repairs}>
          {(data) =>
            data.items.length === 0 ? (
              <Empty title="No repair proposals">Open a finding and choose “Propose a repair”. Automated repairs exist for vulnerable pinned Python dependencies and failing pytest suites.</Empty>
            ) : (
              <>
                <Table label="Repair proposals" head={["Finding", "Severity", "Strategy", "Files", "Status", "Execution", "Created"]}>
                  {data.items.map((proposal) => (
                    <tr key={proposal.id}>
                      <td className="max-w-sm">
                        <Link href={`/repairs/${proposal.id}`} className="font-medium underline-offset-2 hover:underline">{proposal.finding_title}</Link>
                      </td>
                      <td><SeverityBadge severity={proposal.finding_severity} /></td>
                      <td>
                        {proposal.strategy.replace("_", " ")}
                        <span className="block text-xs text-muted">{proposal.generated_by}</span>
                      </td>
                      <td className="font-mono text-xs">
                        {proposal.affected_files.join(", ") || "-"} {proposal.sensitive && <Badge tone="warn">sensitive</Badge>}
                      </td>
                      <td><StateBadge state={proposal.status} /></td>
                      <td>{proposal.latest_execution_status ? <StateBadge state={proposal.latest_execution_status} /> : "-"}</td>
                      <td className="whitespace-nowrap text-muted">{formatDate(proposal.created_at)}</td>
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
