"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { QueryView, useAction } from "@/components/query";
import { Button, Card, ErrorNote, Notice } from "@/components/ui";
import { api } from "@/lib/api";

interface ProfileResponse {
  profile: Record<string, unknown> | null;
}

const EXAMPLE = {
  runtime: "node",
  component: "",
  services: [
    { name: "postgres", image: "postgres:16-alpine", env: { POSTGRES_USER: "app", POSTGRES_PASSWORD: "${SECRET:db}", POSTGRES_DB: "app" } },
    { name: "redis", image: "redis:7-alpine" },
  ],
  env: {
    NODE_ENV: "development",
    DATABASE_URL: "postgresql://app:${SECRET:db}@postgres:5432/app",
    REDIS_URL: "redis://redis:6379",
    JWT_SECRET: "${SECRET:jwt}",
    NEXT_PUBLIC_API_URL: "${URL:api}/api",
    CORS_ORIGIN: "${URL:web}",
  },
  setup: ["pnpm --filter api run build", "pnpm --filter api run migration:run"],
  apps: [
    { name: "api", command: "pnpm --filter api run start", port: 4000, health_path: "/health" },
    { name: "web", command: "pnpm --filter web exec next dev --port 3000 --hostname 0.0.0.0", port: 3000 },
  ],
  target: "web",
};

function Editor({ projectId, initial }: { projectId: string; initial: Record<string, unknown> | null }) {
  const [text, setText] = useState(JSON.stringify(initial ?? EXAMPLE, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const save = useAction<Record<string, unknown>, ProfileResponse>("PUT", `/projects/${projectId}/environment`);

  return (
    <div className="space-y-3">
      <p className="text-muted">
        A recipe for running an isolated copy of this project in containers, so that browser, API, accessibility and performance checks can run
        without a deployed environment. It must not contain real secrets: use <code className="font-mono text-xs">{"${SECRET:name}"}</code> for
        generated values and <code className="font-mono text-xs">{"${URL:app}"}</code> for the local address of an app. The project&apos;s own
        .env files are never used.
      </p>
      {!initial && <Notice>No profile is stored yet. The text below is an example to adapt, taken from a typical NestJS + Next.js monorepo.</Notice>}
      <label htmlFor="environment-profile" className="sr-only">Environment profile (JSON)</label>
      <textarea
        id="environment-profile"
        rows={22}
        spellCheck={false}
        className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 font-mono text-xs"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      {parseError && <ErrorNote error={new Error(parseError)} />}
      {save.isError && <ErrorNote error={save.error} />}
      {save.isSuccess && <Notice tone="ok">Profile saved. Create a test plan with “managed local environment” to use it.</Notice>}
      <Button
        variant="primary"
        disabled={save.isPending}
        onClick={() => {
          try {
            const parsed: unknown = JSON.parse(text);
            setParseError(null);
            save.mutate(parsed as Record<string, unknown>);
          } catch (error) {
            setParseError(`Not valid JSON: ${error instanceof Error ? error.message : "parse error"}`);
          }
        }}
      >
        {save.isPending ? "Saving…" : "Save profile"}
      </Button>
    </div>
  );
}

export function EnvironmentProfileCard({ projectId }: { projectId: string }) {
  const profile = useQuery({
    queryKey: ["environment-profile", projectId],
    queryFn: () => api<ProfileResponse>(`/projects/${projectId}/environment`),
    staleTime: Infinity,
  });
  return (
    <Card title="Managed test environment">
      <QueryView query={profile}>{(data) => <Editor projectId={projectId} initial={data.profile} />}</QueryView>
    </Card>
  );
}
