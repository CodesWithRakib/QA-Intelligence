"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { QueryView } from "@/components/query";
import { Card, Empty, Input, PageHeader, Pager, Table } from "@/components/ui";
import { api, formatDate, query } from "@/lib/api";
import type { AuditEvent, Page } from "@/lib/types";

export default function AuditPage() {
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const events = useQuery({
    queryKey: ["audit", action, offset],
    queryFn: () => api<Page<AuditEvent>>(`/audit${query({ action: action.trim(), offset, limit: 50 })}`),
  });
  return (
    <>
      <PageHeader title="Audit log" description="Append-only record of imports, plan and run events, approvals, repairs and configuration changes." />
      <Card>
        <div className="mb-3 max-w-xs">
          <label htmlFor="audit-action" className="sr-only">Filter by action prefix</label>
          <Input
            id="audit-action"
            type="search"
            placeholder="Action prefix, e.g. repair."
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setOffset(0);
            }}
          />
        </div>
        <QueryView query={events}>
          {(data) =>
            data.items.length === 0 ? (
              <Empty title="No audit events match" />
            ) : (
              <>
                <Table label="Audit events" head={["Time", "Actor", "Action", "Entity", "Details"]}>
                  {data.items.map((event) => (
                    <tr key={event.id}>
                      <td className="whitespace-nowrap text-muted">{formatDate(event.created_at)}</td>
                      <td>{event.actor}</td>
                      <td className="font-mono text-xs">{event.action}</td>
                      <td className="font-mono text-xs text-muted">
                        {event.entity_type}
                        {event.entity_id ? `:${event.entity_id.slice(0, 8)}` : ""}
                      </td>
                      <td className="max-w-md font-mono text-xs break-words text-muted">{JSON.stringify(event.details)}</td>
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
