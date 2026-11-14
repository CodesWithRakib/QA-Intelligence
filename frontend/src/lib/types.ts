/** API contracts (mirrors backend/app/api/serializers.py). */

export type ResultStatus = "passed" | "failed" | "skipped" | "blocked" | "not_tested" | "not_applicable";
export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type Category =
  | "functional"
  | "browser"
  | "api"
  | "database"
  | "security"
  | "performance"
  | "accessibility"
  | "product"
  | "visual"
  | "build"
  | "compatibility"
  | "android";

export type ReadinessStatus =
  | "ready"
  | "partially_ready"
  | "requires_configuration"
  | "requires_credentials"
  | "requires_source_access"
  | "requires_device"
  | "requires_test_environment"
  | "blocked"
  | "not_applicable";

export interface ReadinessReport {
  status: ReadinessStatus;
  reasons: string[];
  requirements: string[];
}

export interface PlanReadiness {
  cases: Record<string, ReadinessReport>;
  summary: Partial<Record<ReadinessStatus, number>>;
  checked_at: string;
}

export type Classification = "confirmed_defect" | "potential_defect" | "ai_hypothesis" | "environment_failure" | "false_positive";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProjectVersion {
  id: string;
  number: number;
  content_hash: string;
  file_count: number;
  size_bytes: number;
  has_files: boolean;
  created_at: string;
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string;
  target_url: string | null;
  created_at: string;
  sources: { kind: string; location: string; config: { excluded_secret_files?: string[]; skipped?: string[] } }[];
  versions: ProjectVersion[];
  open_findings?: number;
  runs?: number;
}

export interface Architecture {
  version: ProjectVersion;
  inventory: {
    file_count?: number;
    languages?: { name: string; files: number; bytes: number }[];
    frameworks?: string[];
    package_managers?: string[];
    test_frameworks?: string[];
    test_files?: { count: number; sample: string[] };
    test_commands?: { component: string; runtime: string; command: string }[];
    monorepo?: { detected: boolean; markers: string[]; package_count: number };
    data_models?: { name: string; kind: string; file: string }[];
    migrations?: string[];
    env_var_names?: string[];
    openapi_specs?: { file: string; title: string; version: string; spec_version: string }[];
    containers?: { dockerfiles: string[]; compose_services: { name: string; image: string }[] };
    ci?: string[];
    security_sensitive_files?: string[];
    analysis_notes?: string[];
  };
  unsupported: string[];
  ai_summary: string | null;
  ai_model: string | null;
  components: {
    name: string;
    kind: string;
    path: string;
    languages: string[];
    frameworks: string[];
    details: { manifest?: string; package_manager?: string; dependencies?: { name: string; spec: string; dev: boolean }[] };
  }[];
  routes: { path: string; framework: string; source_file: string }[];
  api_endpoints: { method: string; path: string; origin: string; source_file: string; operation_id: string | null }[];
}

export interface TestCase {
  id: string;
  key: string;
  title: string;
  category: Category;
  engine: string;
  priority: number;
  origin: "deterministic" | "ai_suggested";
  enabled: boolean;
  spec: Record<string, unknown>;
}

export interface TestPlan {
  id: string;
  project_id: string;
  name: string;
  target_url: string | null;
  allow_mutations: boolean;
  managed_environment: boolean;
  notes: string[];
  case_count: number;
  created_at: string;
  cases?: TestCase[];
}

export type RunSummary = Partial<Record<ResultStatus | "total", number>>;

export interface TestRun {
  id: string;
  plan_id: string;
  project_id: string;
  plan_name: string;
  target_url: string | null;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  summary: RunSummary;
  cancel_requested: boolean;
  progress: { case?: string; title?: string; adapter?: string; index?: number; total?: number; case_started_at?: string; cleanup?: Record<string, unknown> };
  readiness: Record<string, ReadinessReport>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Evidence {
  id: string;
  kind: string;
  label: string;
  content: string | null;
  has_file: boolean;
  sha256: string;
}

export interface TestResult {
  id: string;
  status: ResultStatus;
  duration_ms: number;
  message: string;
  details: Record<string, unknown>;
  case: TestCase;
  evidence: Evidence[];
}

export interface RunDetail extends TestRun {
  planned_cases: number;
  results: TestResult[];
}

export interface Finding {
  id: string;
  project_id: string;
  run_id: string | null;
  category: Category;
  severity: Severity;
  title: string;
  affected_component: string;
  confidence: number;
  status: "open" | "fix_proposed" | "fixed" | "wont_fix" | "false_positive";
  verification_status: "unverified" | "verified_fixed" | "verification_failed";
  source_tool: string;
  classification: Classification;
  created_at: string;
  last_seen_at: string;
}

export interface RegressionResult {
  phase: "before" | "after";
  check: string;
  status: ResultStatus;
  message: string;
  details: Record<string, unknown>;
}

export interface RepairExecution {
  id: string;
  status: "queued" | "running" | "verified" | "failed" | "review_rejected";
  branch: string | null;
  workspace_path: string | null;
  review: {
    policy_approved?: boolean;
    checks?: { name: string; passed: boolean; detail: string }[];
    ai_review?: { status: string; verdict?: string; notes?: string; model?: string; reason?: string };
  };
  outcome: string;
  created_at: string;
  finished_at: string | null;
  regression_results: RegressionResult[];
}

export interface RepairProposal {
  id: string;
  finding_id: string;
  finding_title: string;
  finding_severity: Severity;
  project_id: string;
  strategy: string;
  generated_by: string;
  status: "proposed" | "approved" | "rejected" | "verified" | "failed";
  sensitive: boolean;
  affected_files: string[];
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  latest_execution_status: string | null;
  rationale?: string;
  diff?: string;
  executions?: RepairExecution[];
}

export interface FindingDetail extends Finding {
  description: string;
  reproduction_steps: string[];
  remediation: string;
  expected_behavior: string;
  actual_behavior: string;
  facts: Record<string, unknown>;
  ai_explanation: string | null;
  evidence: Evidence[];
  proposals: RepairProposal[];
}

export interface Report {
  id: string;
  project_id: string;
  run_id: string | null;
  title: string;
  data: { counts?: RunSummary; total?: number; findings?: number; repairs?: number };
  created_at: string;
  content_markdown?: string;
}

export interface Overview {
  projects: number;
  runs: number;
  open_findings: number;
  verified_fixes: number;
  pending_repairs: number;
  findings_by_severity: Partial<Record<Severity, number>>;
  findings_by_category: Partial<Record<Category, number>>;
  results_all_time: Record<ResultStatus, number>;
  latest_completed_run: TestRun | null;
  recent_runs: TestRun[];
}

export interface HealthCheck {
  ok: boolean;
  detail: string;
  optional?: boolean;
}

export interface SystemHealth {
  version: string;
  healthy: boolean;
  checks: Record<string, HealthCheck>;
  queued_jobs: number;
  checked_at: string;
}

export interface AgentInfo {
  name: string;
  responsibility: string;
  inputs: string;
  outputs: string;
  tools: string[];
  timeout_s: number;
  retry_limit: number;
  evidence: string;
  uses_ai: boolean;
  executions: number;
  failures: number;
  avg_duration_ms: number;
}

export interface AgentsResponse {
  agents: AgentInfo[];
  not_implemented: string[];
  recent_executions: {
    id: string;
    agent: string;
    status: string;
    duration_ms: number;
    output: Record<string, unknown>;
    error: string | null;
    created_at: string;
  }[];
}

export interface ProviderConfig {
  provider: string;
  enabled: boolean;
  is_paid: boolean;
  implemented: boolean;
  available: boolean;
  availability_detail: string;
  default_model: string | null;
  models: string[];
  paid_usage_approved: boolean;
  per_project_budget_usd: number;
  monthly_budget_usd: number;
  spent_this_month_usd: number;
  input_price_per_mtok: number | null;
  output_price_per_mtok: number | null;
  alert: boolean;
  exhausted: boolean;
}

export interface AIConfig {
  providers: ProviderConfig[];
  task_routing: Record<string, "local" | "premium">;
  policy: string;
}

export interface BenchmarkSummary {
  provider: string;
  model: string;
  run_at: string;
  tasks: number;
  passed: number;
  accuracy: number;
  false_positive_rate: number | null;
  root_cause_correct: number | null;
  repair_success: number | null;
  test_generation: number | null;
  structured_output_reliability: number;
  avg_latency_ms: number;
  total_tokens: number;
  cost_usd: number;
  cost_per_successful_task_usd: number | null;
  not_measured: string[];
  details: { task: string; passed: boolean; structured_output_ok: boolean; latency_ms: number; detail: string }[];
}

export interface AIUsageRow {
  provider: string;
  model: string;
  task_type: string;
  calls: number;
  successful_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  avg_latency_ms: number;
}

export interface ExecutionSettings {
  allowed_targets: string[];
  sandbox_cpus: number;
  sandbox_memory_mb: number;
  sandbox_pids_limit: number;
  sandbox_tmpfs_mb: number;
  sandbox_install_timeout_s: number;
  sandbox_test_timeout_s: number;
  scanner_timeout_s: number;
  browser_max_pages: number;
  browser_page_timeout_s: number;
  api_request_timeout_s: number;
  api_max_requests_per_run: number;
  perf_samples: number;
  perf_slow_threshold_ms: number;
  browsers: string[];
  load_test_vus: number;
  load_test_duration_s: number;
  android_allow_physical_device: boolean;
  android_boot_timeout_s: number;
  android_explore_actions: number;
  android_emulator_memory_mb: number;
  android_min_free_ram_gb: number;
  git_allowed_hosts: string[];
  max_upload_mb: number;
  max_extracted_mb: number;
  max_files: number;
}

export interface AuditEvent {
  id: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface Integrations {
  source_control: { name: string; status: string; detail: string }[];
  tools: { name: string; purpose: string; image: string | null; status: string }[];
}

export interface Capabilities {
  adapters: ({ name: string; title: string; agent: string; needs_target: boolean; needs_source: boolean } & ReadinessReport)[];
  tools: { name: string; purpose: string; installed: boolean; path: string }[];
  images: { image: string; present: boolean }[];
  android_devices: { serial: string; state: string }[];
  resources: { free_ram_gb: number; free_disk_gb: number; gpu: { name: string; vram_total_mb: number; vram_used_mb: number } | null };
  concurrency: string;
}

export interface RunComparison {
  base: TestRun;
  head: TestRun;
  changes: { case: string; category: string; kind: "new" | "removed" | "regression" | "fixed" | "changed"; before: ResultStatus | null; after: ResultStatus | null }[];
  regressions: number;
  fixed: number;
  unchanged: number;
}
