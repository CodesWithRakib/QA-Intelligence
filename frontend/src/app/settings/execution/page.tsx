"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { QueryView, useAction } from "@/components/query";
import { Button, Card, ErrorNote, Field, Input, Notice, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { ExecutionSettings } from "@/lib/types";

type NumericKey = Exclude<keyof ExecutionSettings, "allowed_targets">;

const GROUPS: { title: string; fields: { key: NumericKey; label: string; hint: string; step?: number }[] }[] = [
  {
    title: "Sandbox (containers running imported code)",
    fields: [
      { key: "sandbox_cpus", label: "CPUs per container", hint: "0.25 - 4", step: 0.25 },
      { key: "sandbox_memory_mb", label: "Memory per container (MB)", hint: "128 - 3072. Docker itself is capped at 4 GB on this PC." },
      { key: "sandbox_pids_limit", label: "Process limit", hint: "32 - 1024" },
      { key: "sandbox_tmpfs_mb", label: "Temporary filesystem (MB)", hint: "16 - 2048" },
      { key: "sandbox_install_timeout_s", label: "Dependency install timeout (s)", hint: "30 - 1800" },
      { key: "sandbox_test_timeout_s", label: "Test timeout (s)", hint: "10 - 1800" },
      { key: "scanner_timeout_s", label: "Scanner timeout (s)", hint: "30 - 1800" },
    ],
  },
  {
    title: "Active testing against targets",
    fields: [
      { key: "browser_max_pages", label: "Pages per crawl", hint: "1 - 50" },
      { key: "browser_page_timeout_s", label: "Page timeout (s)", hint: "5 - 120" },
      { key: "api_request_timeout_s", label: "API request timeout (s)", hint: "1 - 60" },
      { key: "api_max_requests_per_run", label: "Request cap per API check", hint: "1 - 1000. Protects targets from unbounded traffic." },
      { key: "perf_samples", label: "Latency samples", hint: "3 - 50, sent sequentially (not a load test)" },
      { key: "perf_slow_threshold_ms", label: "Slow endpoint threshold, p95 (ms)", hint: "50 - 30000" },
    ],
  },
  {
    title: "Import limits",
    fields: [
      { key: "max_upload_mb", label: "Upload size (MB)", hint: "1 - 1024" },
      { key: "max_extracted_mb", label: "Extracted size (MB)", hint: "1 - 4096" },
      { key: "max_files", label: "File count", hint: "10 - 200000" },
    ],
  },
];

function SettingsForm({ initial }: { initial: ExecutionSettings }) {
  const [values, setValues] = useState(initial);
  const [targets, setTargets] = useState(initial.allowed_targets.join("\n"));
  const save = useAction<ExecutionSettings, ExecutionSettings>("PUT", "/settings/execution");

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate({ ...values, allowed_targets: targets.split("\n").map((line) => line.trim()).filter(Boolean) });
      }}
    >
      <Card title="Authorised targets">
        <Field
          label="Allowed targets (one host:port per line)"
          htmlFor="allowed-targets"
          hint="Browser and API tests only run against hosts listed here. Add a host only if you own it or are explicitly authorised to test it. Cloud metadata addresses are always blocked."
        >
          <textarea
            id="allowed-targets"
            rows={4}
            spellCheck={false}
            className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 font-mono text-sm"
            placeholder="127.0.0.1:8100"
            value={targets}
            onChange={(event) => setTargets(event.target.value)}
          />
        </Field>
      </Card>
      {GROUPS.map((group) => (
        <Card key={group.title} title={group.title}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {group.fields.map((field) => (
              <Field key={field.key} label={field.label} htmlFor={field.key} hint={field.hint}>
                <Input
                  id={field.key}
                  type="number"
                  step={field.step ?? 1}
                  required
                  value={values[field.key]}
                  onChange={(event) => setValues({ ...values, [field.key]: event.target.valueAsNumber })}
                />
              </Field>
            ))}
          </div>
        </Card>
      ))}
      {save.isError && <ErrorNote error={save.error} />}
      {save.isSuccess && <Notice tone="ok">Settings saved. They apply to the next run.</Notice>}
      <Button type="submit" variant="primary" disabled={save.isPending}>
        {save.isPending ? "Saving…" : "Save settings"}
      </Button>
    </form>
  );
}

export default function ExecutionSettingsPage() {
  const settings = useQuery({ queryKey: ["execution-settings"], queryFn: () => api<ExecutionSettings>("/settings/execution"), staleTime: Infinity });
  return (
    <>
      <PageHeader
        title="Execution settings"
        description="Resource limits and authorisation rules enforced by the backend. Values outside the allowed ranges are rejected by the API."
      />
      <QueryView query={settings}>{(data) => <SettingsForm initial={data} />}</QueryView>
    </>
  );
}
