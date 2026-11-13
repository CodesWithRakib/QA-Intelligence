"use client";

import { useQuery } from "@tanstack/react-query";

import { QueryView } from "@/components/query";
import { Card, PageHeader, StateBadge, Table } from "@/components/ui";
import { api } from "@/lib/api";
import type { Integrations } from "@/lib/types";

export default function IntegrationsPage() {
  const integrations = useQuery({ queryKey: ["integrations"], queryFn: () => api<Integrations>("/integrations") });
  return (
    <>
      <PageHeader title="Integrations" description="What the platform can connect to today. “Not implemented” means exactly that: there is no hidden or partial support." />
      <QueryView query={integrations}>
        {(data) => (
          <div className="space-y-4">
            <Card title="Source control">
              <Table label="Source control integrations" head={["Provider", "Status", "What to do instead"]}>
                {data.source_control.map((item) => (
                  <tr key={item.name}>
                    <td className="font-medium">{item.name}</td>
                    <td><StateBadge state={item.status} /></td>
                    <td className="text-muted">{item.detail}</td>
                  </tr>
                ))}
              </Table>
            </Card>
            <Card title="QA tools">
              <Table label="QA tools" head={["Tool", "Purpose", "Status", "Container image"]}>
                {data.tools.map((tool) => (
                  <tr key={tool.name}>
                    <td className="font-medium">{tool.name}</td>
                    <td>{tool.purpose}</td>
                    <td><StateBadge state={tool.status} /></td>
                    <td className="font-mono text-xs text-muted">{tool.image ?? "-"}</td>
                  </tr>
                ))}
              </Table>
            </Card>
          </div>
        )}
      </QueryView>
    </>
  );
}
