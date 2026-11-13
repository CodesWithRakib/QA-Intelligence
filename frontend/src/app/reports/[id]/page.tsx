"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";

import { QueryView } from "@/components/query";
import { Button, Card, LinkButton, PageHeader, Pre } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import type { Report } from "@/lib/types";

function download(report: Report) {
  const url = URL.createObjectURL(new Blob([report.content_markdown ?? ""], { type: "text/markdown" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `qa-report-${report.id.slice(0, 8)}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const report = useQuery({ queryKey: ["report", id], queryFn: () => api<Report>(`/reports/${id}`) });
  return (
    <QueryView query={report}>
      {(data) => (
        <>
          <PageHeader
            title={data.title}
            description={`Generated ${formatDate(data.created_at)} · stored as Markdown`}
            actions={
              <>
                {data.run_id && <LinkButton href={`/runs/${data.run_id}`}>Open run</LinkButton>}
                <Button variant="primary" onClick={() => download(data)}>Download .md</Button>
              </>
            }
          />
          <Card>
            <Pre>{data.content_markdown}</Pre>
          </Card>
        </>
      )}
    </QueryView>
  );
}
