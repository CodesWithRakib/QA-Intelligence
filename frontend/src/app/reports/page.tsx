"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { QueryView } from "@/components/query";
import { Card, Empty, LinkButton, PageHeader, ResultBar, Table } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import type { Page, Report } from "@/lib/types";

export default function ReportsPage() {
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<Page<Report>>("/reports?limit=100") });
  return (
    <>
      <PageHeader title="Reports" description="Reports are assembled only from stored results. Checks without a verdict are listed as such, never as passed." />
      <Card>
        <QueryView query={reports}>
          {(data) =>
            data.items.length === 0 ? (
              <Empty title="No reports yet" action={<LinkButton href="/runs" variant="primary">Open test runs</LinkButton>}>
                Open a finished run and choose “Generate report”.
              </Empty>
            ) : (
              <Table label="Reports" head={["Report", "Results", "Findings", "Created"]}>
                {data.items.map((report) => (
                  <tr key={report.id}>
                    <td><Link href={`/reports/${report.id}`} className="font-medium underline-offset-2 hover:underline">{report.title}</Link></td>
                    <td className="min-w-56"><ResultBar summary={{ ...report.data.counts, total: report.data.total }} /></td>
                    <td className="tabular-nums">{report.data.findings ?? 0}</td>
                    <td className="whitespace-nowrap text-muted">{formatDate(report.created_at)}</td>
                  </tr>
                ))}
              </Table>
            )
          }
        </QueryView>
      </Card>
    </>
  );
}
