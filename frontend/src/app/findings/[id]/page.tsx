"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";

import { EvidenceList } from "@/components/evidence";
import { pollWhile, QueryView, useAction } from "@/components/query";
import { Badge, Button, Card, ErrorNote, Notice, PageHeader, Pre, SeverityBadge, StateBadge, Table } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import type { FindingDetail } from "@/lib/types";

export default function FindingPage() {
  const { id } = useParams<{ id: string }>();
  const explain = useAction("POST", `/findings/${id}/explanation`);
  const propose = useAction("POST", `/findings/${id}/repair-proposals`);
  const update = useAction<{ status: string }, FindingDetail>("PATCH", `/findings/${id}`);
  const waiting = explain.isSuccess || propose.isSuccess;
  const finding = useQuery({
    queryKey: ["finding", id],
    queryFn: () => api<FindingDetail>(`/findings/${id}`),
    refetchInterval: pollWhile<FindingDetail>(
      (data) => waiting || data.proposals.some((p) => p.status === "approved"),
      4000,
    ),
  });

  return (
    <QueryView query={finding}>
      {(data) => {
        const pending = data.proposals.some((p) => p.status === "proposed" || p.status === "approved");
        return (
          <>
            <PageHeader
              title={data.title}
              description={<span className="font-mono text-xs">{data.affected_component}</span>}
              actions={
                <>
                  <SeverityBadge severity={data.severity} />
                  <StateBadge state={data.status} />
                  <StateBadge state={data.verification_status} />
                </>
              }
            />
            <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
              <div className="space-y-4">
                {[explain, propose, update].map((action, index) => action.isError && <ErrorNote key={index} error={action.error} />)}
                <Card title="Description">
                  <p className="whitespace-pre-wrap">{data.description}</p>
                </Card>
                <Card title="Reproduction steps">
                  {data.reproduction_steps.length ? (
                    <ol className="list-decimal space-y-1 pl-5">
                      {data.reproduction_steps.map((step) => (
                        <li key={step}>{step}</li>
                      ))}
                    </ol>
                  ) : (
                    <p className="text-muted">No steps recorded.</p>
                  )}
                </Card>
                <Card title="Remediation">
                  <p className="whitespace-pre-wrap">{data.remediation || "No remediation guidance recorded."}</p>
                </Card>
                <Card title={`Evidence (${data.evidence.length})`}>
                  <EvidenceList items={data.evidence} />
                </Card>
                <Card
                  title="AI explanation (advisory)"
                  action={
                    <Button disabled={explain.isPending || explain.isSuccess} onClick={() => explain.mutate()}>
                      {explain.isSuccess ? "Queued…" : data.ai_explanation ? "Regenerate" : "Explain with local model"}
                    </Button>
                  }
                >
                  {data.ai_explanation ? <p className="whitespace-pre-wrap">{data.ai_explanation}</p> : <p className="text-muted">Not generated. AI output is never treated as evidence.</p>}
                </Card>
                <Card
                  title="Repair"
                  action={
                    <Button variant="primary" disabled={pending || propose.isPending || propose.isSuccess} onClick={() => propose.mutate()}>
                      {propose.isSuccess ? "Proposal queued…" : "Propose a repair"}
                    </Button>
                  }
                >
                  {data.proposals.length === 0 ? (
                    <p className="text-muted">No repair proposed. A proposal is only a diff for you to review: nothing is applied without your approval.</p>
                  ) : (
                    <Table label="Repair proposals" head={["Created", "Strategy", "Status", "Execution", ""]}>
                      {data.proposals.map((proposal) => (
                        <tr key={proposal.id}>
                          <td className="whitespace-nowrap text-muted">{formatDate(proposal.created_at)}</td>
                          <td>{proposal.strategy.replace("_", " ")} <span className="text-muted">({proposal.generated_by})</span></td>
                          <td><StateBadge state={proposal.status} /></td>
                          <td>{proposal.latest_execution_status ? <StateBadge state={proposal.latest_execution_status} /> : "-"}</td>
                          <td>
                            {proposal.strategy === "none" ? (
                              <span className="text-xs text-muted">{proposal.rationale}</span>
                            ) : (
                              <Link href={`/repairs/${proposal.id}`} className="text-accent underline-offset-2 hover:underline">Review</Link>
                            )}
                          </td>
                        </tr>
                      ))}
                    </Table>
                  )}
                </Card>
              </div>
              <div className="space-y-4">
                <Card title="Details">
                  <dl className="grid grid-cols-[6.5rem_1fr] gap-y-1.5">
                    <dt className="text-muted">Category</dt><dd><Badge>{data.category}</Badge></dd>
                    <dt className="text-muted">Confidence</dt><dd>{Math.round(data.confidence * 100)}%</dd>
                    <dt className="text-muted">Detected by</dt><dd>{data.source_tool}</dd>
                    <dt className="text-muted">First seen</dt><dd>{formatDate(data.created_at)}</dd>
                    <dt className="text-muted">Last seen</dt><dd>{formatDate(data.last_seen_at)}</dd>
                    <dt className="text-muted">Run</dt>
                    <dd>{data.run_id ? <Link className="underline" href={`/runs/${data.run_id}`}>open run</Link> : "-"}</dd>
                    <dt className="text-muted">ID</dt><dd className="font-mono text-xs break-all">{data.id}</dd>
                  </dl>
                </Card>
                <Card title="Triage">
                  {data.status === "fixed" && <Notice tone="ok">A fix was verified in an isolated workspace. Your original source is unchanged until you apply the patch.</Notice>}
                  <div className="mt-2 flex flex-wrap gap-2">
                    {data.status !== "open" && <Button disabled={update.isPending} onClick={() => update.mutate({ status: "open" })}>Reopen</Button>}
                    {data.status === "open" && (
                      <>
                        <Button disabled={update.isPending} onClick={() => update.mutate({ status: "wont_fix" })}>Won’t fix</Button>
                        <Button disabled={update.isPending} onClick={() => update.mutate({ status: "false_positive" })}>False positive</Button>
                      </>
                    )}
                  </div>
                </Card>
                <Card title="Machine-readable facts">
                  <Pre>{JSON.stringify(data.facts, null, 2)}</Pre>
                </Card>
              </div>
            </div>
          </>
        );
      }}
    </QueryView>
  );
}
