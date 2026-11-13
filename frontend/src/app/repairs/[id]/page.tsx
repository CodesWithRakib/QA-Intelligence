"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { pollWhile, QueryView, useAction } from "@/components/query";
import { Badge, Button, Card, cx, ErrorNote, Field, Input, Notice, PageHeader, Pre, ResultBadge, StateBadge, Table } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import type { RepairExecution, RepairProposal } from "@/lib/types";

function Diff({ text }: { text: string }) {
  return (
    <pre className="max-h-[32rem] overflow-auto rounded-md bg-raised p-3 font-mono text-xs">
      {text.split("\n").map((line, index) => (
        <span
          key={index}
          className={cx(
            "block whitespace-pre-wrap",
            line.startsWith("+") && !line.startsWith("+++") && "bg-ok-bg text-ok",
            line.startsWith("-") && !line.startsWith("---") && "bg-bad-bg text-bad",
            line.startsWith("@@") && "text-info",
          )}
        >
          {line || " "}
        </span>
      ))}
    </pre>
  );
}

function Execution({ execution }: { execution: RepairExecution }) {
  const review = execution.review;
  return (
    <Card title={`Execution · ${formatDate(execution.created_at)}`} action={<StateBadge state={execution.status} />}>
      <div className="space-y-4">
        {execution.outcome && <Notice tone={execution.status === "verified" ? "ok" : "warn"}>{execution.outcome}</Notice>}
        {execution.branch && (
          <p className="text-muted">
            Branch <code className="font-mono text-xs">{execution.branch}</code> in <code className="font-mono text-xs">data/{execution.workspace_path}</code>
          </p>
        )}
        {execution.regression_results.length > 0 && (
          <div>
            <h3 className="mb-1 font-medium">Before / after</h3>
            <Table label="Before and after checks" head={["Phase", "Check", "State", "Detail"]}>
              {execution.regression_results.map((result, index) => (
                <tr key={index}>
                  <td>{result.phase}</td>
                  <td>{result.check === "targeted" ? "targeted (this defect)" : "project tests (regression)"}</td>
                  <td>
                    {/* For the targeted check, "failed" before the patch means the defect was reproduced. */}
                    <ResultBadge status={result.status} />
                  </td>
                  <td className="text-muted">{result.message}</td>
                </tr>
              ))}
            </Table>
          </div>
        )}
        {review.checks && (
          <div>
            <h3 className="mb-1 font-medium">Independent policy review {review.policy_approved ? <Badge tone="ok">approved</Badge> : <Badge tone="bad">rejected</Badge>}</h3>
            <ul className="space-y-1">
              {review.checks.map((check) => (
                <li key={check.name}>
                  <Badge tone={check.passed ? "ok" : "bad"}>{check.passed ? "ok" : "violation"}</Badge> {check.name.replaceAll("_", " ")}{" "}
                  <span className="text-muted">· {check.detail}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {review.ai_review && (
          <div>
            <h3 className="mb-1 font-medium">AI review (advisory, cannot approve or verify)</h3>
            {review.ai_review.status === "completed" ? (
              <p>
                <Badge tone={review.ai_review.verdict === "looks_correct" ? "ok" : "warn"}>{review.ai_review.verdict?.replace("_", " ")}</Badge>{" "}
                {review.ai_review.notes} <span className="text-xs text-muted">({review.ai_review.model})</span>
              </p>
            ) : (
              <p className="text-muted">Unavailable: {review.ai_review.reason}</p>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

export default function RepairPage() {
  const { id } = useParams<{ id: string }>();
  const [note, setNote] = useState("");
  const [confirmSensitive, setConfirmSensitive] = useState(false);
  const proposal = useQuery({
    queryKey: ["repair", id],
    queryFn: () => api<RepairProposal>(`/repairs/${id}`),
    refetchInterval: pollWhile<RepairProposal>((data) => data.status === "approved", 4000),
  });
  const decide = useAction<{ approve: boolean; note: string; confirm_sensitive: boolean }, RepairProposal>("POST", `/repairs/${id}/decision`);

  return (
    <QueryView query={proposal}>
      {(data) => (
        <>
          <PageHeader
            title="Fix review"
            description={<Link href={`/findings/${data.finding_id}`} className="underline">{data.finding_title}</Link>}
            actions={
              <>
                <StateBadge state={data.status} />
                <a href={`/api/repairs/${id}/patch`} className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-raised">
                  Download .patch
                </a>
              </>
            }
          />
          <div className="space-y-4">
            <Card title="Proposal">
              <dl className="grid grid-cols-[8rem_1fr] gap-y-1.5">
                <dt className="text-muted">Strategy</dt><dd>{data.strategy.replace("_", " ")}</dd>
                <dt className="text-muted">Generated by</dt>
                <dd>{data.generated_by} {data.generated_by !== "deterministic" && <Badge tone="warn">AI-generated</Badge>}</dd>
                <dt className="text-muted">Files</dt><dd className="font-mono text-xs">{data.affected_files.join(", ") || "-"}</dd>
                <dt className="text-muted">Rationale</dt><dd className="whitespace-pre-wrap">{data.rationale}</dd>
                {data.decided_at && (
                  <>
                    <dt className="text-muted">Decision</dt>
                    <dd>{data.decided_by} · {formatDate(data.decided_at)}{data.decision_note ? ` · “${data.decision_note}”` : ""}</dd>
                  </>
                )}
              </dl>
            </Card>
            <Card title="Diff">{data.diff ? <Diff text={data.diff} /> : <Pre>No diff.</Pre>}</Card>
            {data.status === "proposed" && (
              <Card title="Your decision">
                <div className="space-y-3">
                  <p className="text-muted">
                    Approving applies this diff on a new git branch inside an isolated copy of the project and runs the verification. Your original folder is not touched.
                  </p>
                  {data.sensitive && (
                    <Notice tone="warn">
                      <label className="flex items-start gap-2">
                        <input type="checkbox" className="mt-1" checked={confirmSensitive} onChange={(e) => setConfirmSensitive(e.target.checked)} />
                        <span>This patch touches authentication, payment, cryptography or session related paths. I have reviewed the diff and confirm it explicitly.</span>
                      </label>
                    </Notice>
                  )}
                  <Field label="Note (optional)" htmlFor="decision-note">
                    <Input id="decision-note" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
                  </Field>
                  {decide.isError && <ErrorNote error={decide.error} />}
                  <div className="flex gap-2">
                    <Button
                      variant="primary"
                      disabled={decide.isPending || (data.sensitive && !confirmSensitive)}
                      onClick={() => decide.mutate({ approve: true, note, confirm_sensitive: confirmSensitive })}
                    >
                      Approve and verify
                    </Button>
                    <Button variant="danger" disabled={decide.isPending} onClick={() => decide.mutate({ approve: false, note, confirm_sensitive: false })}>
                      Reject
                    </Button>
                  </div>
                </div>
              </Card>
            )}
            {data.status === "approved" && <Notice>Verification is running in the sandbox (dependency install and tests, before and after). This page refreshes automatically.</Notice>}
            {(data.executions ?? []).map((execution) => (
              <Execution key={execution.id} execution={execution} />
            ))}
          </div>
        </>
      )}
    </QueryView>
  );
}
