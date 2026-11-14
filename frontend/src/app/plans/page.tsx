"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { QueryView, useAction } from "@/components/query";
import { Button, Card, Empty, ErrorNote, Field, Input, LinkButton, Loading, PageHeader, Select, Table } from "@/components/ui";
import { api, formatDate, query } from "@/lib/api";
import type { Page, Project, TestPlan } from "@/lib/types";

function PlansContent() {
  const router = useRouter();
  const initialProject = useSearchParams().get("project_id") ?? "";
  const [projectId, setProjectId] = useState(initialProject);
  const [targetUrl, setTargetUrl] = useState("");
  const [allowMutations, setAllowMutations] = useState(false);
  const [managed, setManaged] = useState(false);
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Page<Project>>("/projects?limit=200") });
  const plans = useQuery({
    queryKey: ["plans", projectId],
    queryFn: () => api<Page<TestPlan>>(`/plans${query({ project_id: projectId, limit: 100 })}`),
  });
  const create = useAction<{ project_id: string; target_url: string | null; allow_mutations: boolean; managed_environment: boolean }, TestPlan>("POST", "/plans");
  const profile = useQuery({
    queryKey: ["environment-profile", projectId],
    queryFn: () => api<{ profile: unknown }>(`/projects/${projectId}/environment`),
    enabled: !!projectId,
  });
  const hasProfile = !!profile.data?.profile;
  const selected = projects.data?.items.find((p) => p.id === projectId);

  return (
    <>
      <PageHeader
        title="Test plans"
        description="Plans are generated deterministically from the architecture inventory. Areas without an automated engine are added as manual cases and always report “not tested”."
      />
      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        <Card title="Generate a plan">
          <QueryView query={projects}>
            {(data) =>
              data.items.length === 0 ? (
                <Empty title="Import a project first" action={<LinkButton href="/projects/import" variant="primary">Import project</LinkButton>} />
              ) : (
                <form
                  className="space-y-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    create.mutate(
                      { project_id: projectId, target_url: managed ? null : targetUrl.trim() || null, allow_mutations: allowMutations, managed_environment: managed && hasProfile },
                      { onSuccess: (plan) => router.push(`/plans/${plan.id}`) },
                    );
                  }}
                >
                  <Field label="Project" htmlFor="plan-project">
                    <Select id="plan-project" required value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                      <option value="">Select a project…</option>
                      {data.items.map((project) => (
                        <option key={project.id} value={project.id}>{project.name}</option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Target URL" htmlFor="plan-target" hint={selected?.target_url ? `Leave empty to use ${selected.target_url}` : "Leave empty for static analysis only."}>
                    <Input id="plan-target" type="url" placeholder="http://127.0.0.1:8100" value={targetUrl} onChange={(e) => setTargetUrl(e.target.value)} />
                  </Field>
                  <label className="flex items-start gap-2">
                    <input type="checkbox" className="mt-1" checked={managed && hasProfile} disabled={!hasProfile} onChange={(e) => setManaged(e.target.checked)} />
                    <span>
                      Run against a managed local environment
                      <span className="block text-xs text-muted">
                        {hasProfile
                          ? "The platform starts an isolated copy of the project (own database, generated secrets) and tests that. The target URL above is ignored."
                          : "Needs an environment profile: add one on the project page."}
                      </span>
                    </span>
                  </label>
                  <label className="flex items-start gap-2">
                    <input type="checkbox" className="mt-1" checked={allowMutations} onChange={(e) => setAllowMutations(e.target.checked)} />
                    <span>
                      Allow state-changing API requests
                      <span className="block text-xs text-muted">POST/PUT/PATCH/DELETE contract calls will create or change data on the target. Off by default.</span>
                    </span>
                  </label>
                  {create.isError && <ErrorNote error={create.error} />}
                  <Button type="submit" variant="primary" disabled={!projectId || create.isPending}>
                    {create.isPending ? "Generating…" : "Generate plan"}
                  </Button>
                </form>
              )
            }
          </QueryView>
        </Card>
        <Card title={selected ? `Plans for ${selected.name}` : "All plans"}>
          <QueryView query={plans}>
            {(data) =>
              data.items.length === 0 ? (
                <p className="text-muted">No plans yet.</p>
              ) : (
                <Table label="Test plans" head={["Plan", "Target", "Cases", "Created"]}>
                  {data.items.map((plan) => (
                    <tr key={plan.id}>
                      <td><Link href={`/plans/${plan.id}`} className="font-medium underline-offset-2 hover:underline">{plan.name}</Link></td>
                      <td className="max-w-64 truncate text-muted">{plan.target_url ?? "static only"}</td>
                      <td className="tabular-nums">{plan.case_count}</td>
                      <td className="whitespace-nowrap text-muted">{formatDate(plan.created_at)}</td>
                    </tr>
                  ))}
                </Table>
              )
            }
          </QueryView>
        </Card>
      </div>
    </>
  );
}

export default function PlansPage() {
  return (
    <Suspense fallback={<Loading />}>
      <PlansContent />
    </Suspense>
  );
}
