"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { QueryView } from "@/components/query";
import { Badge, Card, Empty, Input, LinkButton, PageHeader, Table } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import type { Page, Project } from "@/lib/types";

export default function ProjectsPage() {
  const [filter, setFilter] = useState("");
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Page<Project>>("/projects?limit=200") });

  return (
    <>
      <PageHeader
        title="Projects"
        description="Each import creates an immutable version. Analysis, plans and findings always refer to a version."
        actions={<LinkButton href="/projects/import" variant="primary">Import project</LinkButton>}
      />
      <QueryView query={projects}>
        {(data) => {
          const visible = data.items.filter((p) => p.name.toLowerCase().includes(filter.trim().toLowerCase()));
          if (data.items.length === 0) {
            return (
              <Empty title="No projects imported" action={<LinkButton href="/projects/import" variant="primary">Import project</LinkButton>}>
                Supported sources: local folder, ZIP upload, OpenAPI/Swagger file and running URL.
              </Empty>
            );
          }
          return (
            <Card>
              <div className="mb-3 max-w-xs">
                <label htmlFor="project-filter" className="sr-only">Filter projects by name</label>
                <Input id="project-filter" type="search" placeholder="Filter by name" value={filter} onChange={(e) => setFilter(e.target.value)} />
              </div>
              {visible.length === 0 ? (
                <p className="text-muted">No project matches “{filter}”.</p>
              ) : (
                <Table label="Projects" head={["Name", "Source", "Target", "Version", "Open findings", "Runs", "Imported"]}>
                  {visible.map((project) => (
                    <tr key={project.id}>
                      <td>
                        <Link href={`/projects/${project.id}`} className="font-medium underline-offset-2 hover:underline">{project.name}</Link>
                      </td>
                      <td><Badge>{project.sources[0]?.kind.replace("_", " ") ?? "-"}</Badge></td>
                      <td className="max-w-56 truncate text-muted">{project.target_url ?? "-"}</td>
                      <td className="tabular-nums">v{project.versions.at(-1)?.number ?? "-"}</td>
                      <td className="tabular-nums">{project.open_findings ?? 0}</td>
                      <td className="tabular-nums">{project.runs ?? 0}</td>
                      <td className="whitespace-nowrap text-muted">{formatDate(project.created_at)}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          );
        }}
      </QueryView>
    </>
  );
}
