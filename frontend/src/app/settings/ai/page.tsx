"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { QueryView, useAction } from "@/components/query";
import { Badge, Button, Card, ErrorNote, Field, Input, Notice, PageHeader, Select, StateBadge, Table } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import type { AIConfig, AIUsageRow, BenchmarkSummary, ProviderConfig } from "@/lib/types";

type ProviderPatch = Partial<{
  enabled: boolean;
  default_model: string;
  approve_paid_usage: boolean;
  monthly_budget_usd: number;
  per_project_budget_usd: number;
  input_price_per_mtok: number;
  output_price_per_mtok: number;
}>;

function percent(value: number | null): string {
  return value === null ? "not measured" : `${Math.round(value * 100)}%`;
}

function LocalProvider({ provider }: { provider: ProviderConfig }) {
  const update = useAction<ProviderPatch, ProviderConfig>("PATCH", `/ai/config/${provider.provider}`);
  const benchmark = useAction<{ provider: string; model: string }>("POST", "/ai/benchmark");
  const model = provider.default_model ?? provider.models[0] ?? "";
  return (
    <Card title="Ollama (local, free)" action={<StateBadge state={provider.available && provider.models.length ? "ready" : "unavailable"} />}>
      <div className="space-y-3">
        <p className="text-muted">{provider.availability_detail}</p>
        {provider.models.length === 0 ? (
          <Notice tone="warn">No local model is installed. Run scripts/setup-models.ps1, then reload.</Notice>
        ) : (
          <div className="flex flex-wrap items-end gap-2">
            <div className="w-64">
              <Field label="Default model" htmlFor="ollama-model">
                <Select id="ollama-model" value={model} disabled={update.isPending} onChange={(e) => update.mutate({ default_model: e.target.value })}>
                  {provider.models.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </Select>
              </Field>
            </div>
            <Button disabled={benchmark.isPending || benchmark.isSuccess} onClick={() => benchmark.mutate({ provider: "ollama", model })}>
              {benchmark.isSuccess ? "Benchmark queued" : "Run benchmark on this model"}
            </Button>
            <Button disabled={update.isPending} onClick={() => update.mutate({ enabled: !provider.enabled })}>
              {provider.enabled ? "Disable local AI" : "Enable local AI"}
            </Button>
          </div>
        )}
        {[update, benchmark].map((action, index) => action.isError && <ErrorNote key={index} error={action.error} />)}
        {benchmark.isSuccess && <Notice>The benchmark runs in the worker (several minutes on this hardware). Results appear below.</Notice>}
      </div>
    </Card>
  );
}

function PaidProvider({ provider }: { provider: ProviderConfig }) {
  const update = useAction<ProviderPatch, ProviderConfig>("PATCH", `/ai/config/${provider.provider}`);
  const [form, setForm] = useState({
    default_model: provider.default_model ?? "",
    monthly: String(provider.monthly_budget_usd),
    perProject: String(provider.per_project_budget_usd),
    input: provider.input_price_per_mtok === null ? "" : String(provider.input_price_per_mtok),
    output: provider.output_price_per_mtok === null ? "" : String(provider.output_price_per_mtok),
  });
  const [confirmed, setConfirmed] = useState(false);
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const id = (name: string) => `${provider.provider}-${name}`;

  if (!provider.implemented) {
    return (
      <Card title={`${provider.provider} (paid)`} action={<StateBadge state="not_implemented" />}>
        <p className="text-muted">{provider.availability_detail}. It cannot be enabled.</p>
      </Card>
    );
  }
  return (
    <Card title={`${provider.provider} (paid, optional)`} action={<Badge tone={provider.paid_usage_approved ? "warn" : "idle"}>{provider.paid_usage_approved ? "paid usage approved" : "disabled"}</Badge>}>
      <div className="space-y-3">
        <p className="text-muted">
          {provider.availability_detail}. Spent this month: ${provider.spent_this_month_usd.toFixed(4)} of ${provider.monthly_budget_usd.toFixed(2)}.
        </p>
        {provider.alert && <Notice tone="warn">{provider.exhausted ? "Monthly budget exhausted: paid calls are blocked." : "More than 80% of the monthly budget is used."}</Notice>}
        {provider.paid_usage_approved ? (
          <Button variant="danger" disabled={update.isPending} onClick={() => update.mutate({ approve_paid_usage: false, enabled: false })}>
            Revoke approval and disable
          </Button>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              update.mutate({
                enabled: true,
                approve_paid_usage: true,
                default_model: form.default_model.trim(),
                monthly_budget_usd: Number(form.monthly),
                per_project_budget_usd: Number(form.perProject),
                input_price_per_mtok: Number(form.input),
                output_price_per_mtok: Number(form.output),
              });
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Model id" htmlFor={id("model")} hint="Exactly as the provider names it.">
                <Input id={id("model")} required value={form.default_model} onChange={set("default_model")} />
              </Field>
              <Field label="Monthly budget (USD)" htmlFor={id("monthly")} hint="Hard limit. Calls stop when reached.">
                <Input id={id("monthly")} type="number" min="0.01" step="0.01" required value={form.monthly} onChange={set("monthly")} />
              </Field>
              <Field label="Per-project budget (USD)" htmlFor={id("project")}>
                <Input id={id("project")} type="number" min="0.01" step="0.01" required value={form.perProject} onChange={set("perProject")} />
              </Field>
              <Field label="Input price (USD per 1M tokens)" htmlFor={id("input")} hint="From the provider's current price list.">
                <Input id={id("input")} type="number" min="0" step="0.0001" required value={form.input} onChange={set("input")} />
              </Field>
              <Field label="Output price (USD per 1M tokens)" htmlFor={id("output")}>
                <Input id={id("output")} type="number" min="0" step="0.0001" required value={form.output} onChange={set("output")} />
              </Field>
            </div>
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-1" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
              <span>I approve paid API usage for this provider within the limits above. Only “premium” tasks will use it; everything else stays local.</span>
            </label>
            <Button type="submit" variant="primary" disabled={!confirmed || !provider.available || update.isPending}>
              Approve paid usage
            </Button>
            {!provider.available && <p className="text-xs text-muted">Cannot be approved until the provider is available (see the message above).</p>}
          </form>
        )}
        {update.isError && <ErrorNote error={update.error} />}
      </div>
    </Card>
  );
}

export default function AISettingsPage() {
  const config = useQuery({ queryKey: ["ai-config"], queryFn: () => api<AIConfig>("/ai/config") });
  const benchmarks = useQuery({ queryKey: ["ai-benchmark"], queryFn: () => api<{ items: BenchmarkSummary[]; task_count: number }>("/ai/benchmark"), refetchInterval: 20_000 });
  const usage = useQuery({ queryKey: ["ai-usage"], queryFn: () => api<{ items: AIUsageRow[] }>("/ai/usage") });

  return (
    <>
      <PageHeader title="AI model settings" description="Deterministic tools run first. AI is advisory, local by default, and paid providers stay off until you approve a budget here." />
      <div className="space-y-4">
        <QueryView query={config}>
          {(data) => (
            <>
              <Notice>{data.policy}</Notice>
              {data.providers.map((provider) =>
                provider.is_paid ? <PaidProvider key={provider.provider} provider={provider} /> : <LocalProvider key={provider.provider} provider={provider} />,
              )}
              <Card title="Task routing">
                <ul className="flex flex-wrap gap-2">
                  {Object.entries(data.task_routing).map(([task, tier]) => (
                    <li key={task}>
                      <Badge tone={tier === "premium" ? "warn" : "info"}>{task.replace("_", " ")}: {tier === "premium" ? "paid if approved, else local" : "local"}</Badge>
                    </li>
                  ))}
                </ul>
              </Card>
            </>
          )}
        </QueryView>
        <Card title="Model benchmark (latest run per model)">
          <QueryView query={benchmarks}>
            {(data) =>
              data.items.length === 0 ? (
                <p className="text-muted">No benchmark has been run. It sends {data.task_count} fixed tasks to a model and grades the answers deterministically.</p>
              ) : (
                <div className="space-y-4">
                  <Table label="Model benchmark" head={["Model", "Accuracy", "False positives", "Root cause", "Repair", "Test generation", "Valid JSON", "Avg latency", "Tokens", "Cost", "Run"]}>
                    {data.items.map((row) => (
                      <tr key={`${row.provider}:${row.model}`}>
                        <td className="font-medium whitespace-nowrap">{row.provider}:{row.model}</td>
                        <td className="tabular-nums">{row.passed}/{row.tasks} ({percent(row.accuracy)})</td>
                        <td className="tabular-nums">{percent(row.false_positive_rate)}</td>
                        <td className="tabular-nums">{percent(row.root_cause_correct)}</td>
                        <td className="tabular-nums">{percent(row.repair_success)}</td>
                        <td className="tabular-nums">{percent(row.test_generation)}</td>
                        <td className="tabular-nums">{percent(row.structured_output_reliability)}</td>
                        <td className="whitespace-nowrap tabular-nums">{(row.avg_latency_ms / 1000).toFixed(1)} s</td>
                        <td className="tabular-nums">{row.total_tokens}</td>
                        <td className="tabular-nums">${row.cost_usd.toFixed(4)}</td>
                        <td className="whitespace-nowrap text-muted">{formatDate(row.run_at)}</td>
                      </tr>
                    ))}
                  </Table>
                  {data.items.map((row) => (
                    <details key={`${row.provider}:${row.model}`}>
                      <summary className="cursor-pointer text-muted">Task details for {row.model} (not measured: {row.not_measured.join(", ")})</summary>
                      <ul className="mt-2 space-y-1">
                        {row.details.map((task) => (
                          <li key={task.task}>
                            <Badge tone={task.passed ? "ok" : "bad"}>{task.passed ? "pass" : "fail"}</Badge> <span className="font-mono text-xs">{task.task}</span>{" "}
                            <span className="text-muted">· {(task.latency_ms / 1000).toFixed(1)} s · {task.detail}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  ))}
                </div>
              )
            }
          </QueryView>
        </Card>
        <Card title="Usage by task">
          <QueryView query={usage}>
            {(data) =>
              data.items.length === 0 ? (
                <p className="text-muted">No AI call has been made yet.</p>
              ) : (
                <Table label="AI usage" head={["Provider", "Model", "Task", "Calls", "Successful", "Prompt tokens", "Completion tokens", "Avg latency", "Cost"]}>
                  {data.items.map((row) => (
                    <tr key={`${row.provider}${row.model}${row.task_type}`}>
                      <td>{row.provider}</td>
                      <td>{row.model}</td>
                      <td>{row.task_type.replace("_", " ")}</td>
                      <td className="tabular-nums">{row.calls}</td>
                      <td className="tabular-nums">{row.successful_calls}</td>
                      <td className="tabular-nums">{row.prompt_tokens}</td>
                      <td className="tabular-nums">{row.completion_tokens}</td>
                      <td className="whitespace-nowrap tabular-nums">{(row.avg_latency_ms / 1000).toFixed(1)} s</td>
                      <td className="tabular-nums">${row.cost_usd.toFixed(4)}</td>
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
