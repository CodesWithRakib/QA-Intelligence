"use client";

import { useQuery } from "@tanstack/react-query";

import { QueryView } from "@/components/query";
import { Badge, Card, Notice, PageHeader, StateBadge, Table } from "@/components/ui";
import { api, formatDate, formatDuration } from "@/lib/api";
import type { AgentsResponse } from "@/lib/types";

export default function AgentsPage() {
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api<AgentsResponse>("/system/agents") });
  return (
    <>
      <PageHeader
        title="Agents"
        description="Agents are bounded units of work with fixed contracts. Only the orchestrator invokes them, in a fixed workflow; agents cannot call each other."
      />
      <QueryView query={agents}>
        {(data) => (
          <div className="space-y-4">
            {data.not_implemented.length > 0 && (
              <Notice tone="warn">Not implemented: {data.not_implemented.map((name) => name.replaceAll("_", " ")).join(", ")}.</Notice>
            )}
            <Card title={`Registered agents (${data.agents.length})`}>
              <Table label="Agents" head={["Agent", "Responsibility", "Tools", "Timeout", "Retries", "Evidence", "Runs", "Failures", "Avg time"]}>
                {data.agents.map((agent) => (
                  <tr key={agent.name}>
                    <td className="font-medium whitespace-nowrap">
                      {agent.name.replaceAll("_", " ")} {agent.uses_ai && <Badge tone="warn">AI</Badge>}
                    </td>
                    <td className="min-w-64">{agent.responsibility}</td>
                    <td className="text-muted">{agent.tools.join(", ") || "-"}</td>
                    <td className="whitespace-nowrap tabular-nums">{agent.timeout_s}s</td>
                    <td className="tabular-nums">{agent.retry_limit}</td>
                    <td className="text-muted">{agent.evidence}</td>
                    <td className="tabular-nums">{agent.executions}</td>
                    <td className="tabular-nums">{agent.failures}</td>
                    <td className="whitespace-nowrap">{agent.executions ? formatDuration(agent.avg_duration_ms) : "-"}</td>
                  </tr>
                ))}
              </Table>
            </Card>
            <Card title="Recent executions">
              {data.recent_executions.length === 0 ? (
                <p className="text-muted">No agent has run yet.</p>
              ) : (
                <Table label="Recent agent executions" head={["Time", "Agent", "Status", "Duration", "Output"]}>
                  {data.recent_executions.map((execution) => (
                    <tr key={execution.id}>
                      <td className="whitespace-nowrap text-muted">{formatDate(execution.created_at)}</td>
                      <td>{execution.agent.replaceAll("_", " ")}</td>
                      <td><StateBadge state={execution.status} /></td>
                      <td className="whitespace-nowrap">{formatDuration(execution.duration_ms)}</td>
                      <td className="max-w-md font-mono text-xs break-words text-muted">{execution.error ?? JSON.stringify(execution.output)}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>
        )}
      </QueryView>
    </>
  );
}
