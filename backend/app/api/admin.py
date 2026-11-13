"""AI configuration, execution settings, system health, agents, integrations and audit log."""

import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from app import __version__
from app.agents.registry import AGENTS, NOT_IMPLEMENTED
from app.ai import benchmark
from app.ai.providers import DECLARED_ONLY, PROVIDERS
from app.ai.service import TASK_TIERS, budget_status, ensure_defaults
from app.api import serializers
from app.api.deps import Db, PagingDep
from app.config import get_settings
from app.domain import ResultStatus, values
from app.engines import sandbox, security_scan
from app.jobs import queue
from app.models import (
    AgentExecution,
    AIConfiguration,
    AIUsage,
    AuditEvent,
    Finding,
    Job,
    Project,
    RepairProposal,
    Setting,
    TestResult,
    TestRun,
)
from app.services import audit
from app.services.settings import ExecutionSettings, get_execution_settings, save_execution_settings

router = APIRouter(tags=["admin"])
WORKER_HEARTBEAT_KEY = "worker_heartbeat"
WORKER_STALE_AFTER = timedelta(seconds=45)


class ProviderUpdate(BaseModel):
    enabled: bool | None = None
    default_model: str | None = Field(default=None, max_length=120)
    # Paid providers only. Approval must be given explicitly together with limits and prices.
    approve_paid_usage: bool | None = None
    monthly_budget_usd: float | None = Field(default=None, ge=0, le=10_000)
    per_project_budget_usd: float | None = Field(default=None, ge=0, le=10_000)
    input_price_per_mtok: float | None = Field(default=None, ge=0, le=1_000)
    output_price_per_mtok: float | None = Field(default=None, ge=0, le=1_000)


class BenchmarkRequest(BaseModel):
    provider: str = Field(max_length=32)
    model: str = Field(max_length=120)


def _provider_view(db: Db, config: AIConfiguration) -> dict[str, Any]:
    provider = PROVIDERS.get(config.provider)
    ok, detail = provider.available() if provider else (False, DECLARED_ONLY.get(config.provider, "unknown provider"))
    return {
        "provider": config.provider, "enabled": config.enabled, "is_paid": config.is_paid,
        "implemented": provider is not None, "available": ok, "availability_detail": detail,
        "default_model": config.default_model, "models": provider.list_models() if provider and ok else [],
        "paid_usage_approved": config.paid_usage_approved_at is not None,
        "paid_usage_approved_at": config.paid_usage_approved_at,
        "per_project_budget_usd": float(config.per_project_budget_usd),
        "input_price_per_mtok": config.input_price_per_mtok, "output_price_per_mtok": config.output_price_per_mtok,
        **budget_status(db, config),
    }  # fmt: skip


@router.get("/ai/config")
def get_ai_config(db: Db) -> dict[str, Any]:
    ensure_defaults(db)
    configs = db.scalars(select(AIConfiguration).order_by(AIConfiguration.is_paid, AIConfiguration.provider)).all()
    return {"providers": [_provider_view(db, c) for c in configs], "task_routing": TASK_TIERS,
            "policy": "Local first. Paid providers are used only for 'premium' tasks, after explicit approval, "
                      "within the monthly and per-project budgets."}  # fmt: skip


@router.patch("/ai/config/{provider}")
def update_ai_config(provider: str, body: ProviderUpdate, db: Db) -> dict[str, Any]:
    ensure_defaults(db)
    config = db.scalar(select(AIConfiguration).where(AIConfiguration.provider == provider))
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider")
    changes = body.model_dump(exclude_unset=True)
    paid_fields = {"approve_paid_usage", "monthly_budget_usd", "per_project_budget_usd",
                   "input_price_per_mtok", "output_price_per_mtok"}  # fmt: skip
    if not config.is_paid and paid_fields & changes.keys():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "budget settings apply to paid providers only")
    if config.is_paid and config.provider in DECLARED_ONLY and (changes.get("enabled") or changes.get("approve_paid_usage")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{provider}: {DECLARED_ONLY[provider]}")
    for name in ("enabled", "default_model", "monthly_budget_usd", "per_project_budget_usd",
                 "input_price_per_mtok", "output_price_per_mtok"):  # fmt: skip
        if name in changes:
            setattr(config, name, changes[name])
    if "approve_paid_usage" in changes:
        if changes["approve_paid_usage"]:
            if not (float(config.monthly_budget_usd) > 0 and config.input_price_per_mtok is not None
                    and config.output_price_per_mtok is not None and config.default_model):  # fmt: skip
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                    "approval requires a default model, a monthly budget above 0 and both token prices")  # fmt: skip
            config.paid_usage_approved_at = datetime.now(UTC)
        else:
            config.paid_usage_approved_at = None
    audit.record(db, "ai.config_changed", "ai_configuration", config.id, provider=provider, changes=changes)
    db.commit()
    return _provider_view(db, config)


@router.get("/ai/usage")
def get_ai_usage(db: Db) -> dict[str, Any]:
    rows = db.execute(
        select(AIUsage.provider, AIUsage.model, AIUsage.task_type, func.count(), func.sum(AIUsage.prompt_tokens),
               func.sum(AIUsage.completion_tokens), func.sum(AIUsage.cost_usd), func.avg(AIUsage.latency_ms),
               func.sum(func.cast(AIUsage.success, type_=AIUsage.prompt_tokens.type)))
        .group_by(AIUsage.provider, AIUsage.model, AIUsage.task_type).order_by(AIUsage.provider, AIUsage.task_type)
    ).all()  # fmt: skip
    return {"items": [
        {"provider": p, "model": m, "task_type": t, "calls": calls, "successful_calls": int(ok or 0),
         "prompt_tokens": int(pt or 0), "completion_tokens": int(ct or 0), "cost_usd": float(cost or 0),
         "avg_latency_ms": round(float(latency or 0))}
        for p, m, t, calls, pt, ct, cost, latency, ok in rows
    ]}  # fmt: skip


@router.get("/ai/benchmark")
def get_benchmarks(db: Db) -> dict[str, Any]:
    return {"items": benchmark.summarise(db), "task_count": len(benchmark.TASKS)}


@router.post("/ai/benchmark", status_code=status.HTTP_202_ACCEPTED)
def start_benchmark(body: BenchmarkRequest, db: Db) -> dict[str, Any]:
    if body.provider not in PROVIDERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "provider is not implemented")
    job = queue.enqueue(db, "ai_benchmark", provider=body.provider, model=body.model)
    audit.record(db, "ai.benchmark_requested", "ai_configuration", None, provider=body.provider, model=body.model)
    db.commit()
    return {"job_id": job.id}


@router.get("/settings/execution")
def read_execution_settings(db: Db) -> ExecutionSettings:
    return get_execution_settings(db)


@router.put("/settings/execution")
def write_execution_settings(body: ExecutionSettings, db: Db) -> ExecutionSettings:
    previous = get_execution_settings(db)
    saved = save_execution_settings(db, body)
    changed = {k: v for k, v in saved.model_dump().items() if previous.model_dump()[k] != v}
    audit.record(db, "settings.execution_changed", "setting", "execution", changes=changed)
    db.commit()
    return saved


def _worker_status(db: Db) -> dict[str, Any]:
    row = db.get(Setting, WORKER_HEARTBEAT_KEY)
    last = datetime.fromisoformat(row.value["at"]) if row else None
    alive = bool(last and datetime.now(UTC) - last < WORKER_STALE_AFTER)
    return {"ok": alive, "detail": f"last heartbeat {last.isoformat(timespec='seconds')}" if last
            else "no heartbeat recorded - start it with scripts/dev.ps1"}  # fmt: skip


@router.get("/system/health")
def system_health(db: Db) -> dict[str, Any]:
    settings = get_settings()
    checks: dict[str, dict[str, Any]] = {}
    try:
        checks["database"] = {"ok": True, "detail": str(db.scalar(text("select version()"))).split(",")[0]}
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        checks["database"] = {"ok": False, "detail": type(exc).__name__}
    checks["worker"] = _worker_status(db)
    docker_ok = sandbox.docker_available()
    checks["docker"] = {
        "ok": docker_ok,
        "detail": "daemon reachable" if docker_ok else "daemon not reachable",
    }
    for label, image in (("sandbox_python", sandbox.RUNTIME_IMAGES["python"]), ("sandbox_node", sandbox.RUNTIME_IMAGES["node"]),
                         ("trivy", security_scan.TRIVY_IMAGE), ("semgrep", security_scan.SEMGREP_IMAGE)):  # fmt: skip
        present = docker_ok and sandbox.image_present(image)
        checks[label] = {
            "ok": present,
            "detail": image if present else f"image not pulled: {image}",
            "optional": label == "sandbox_node",
        }
    ok, detail = PROVIDERS["ollama"].available()
    models = PROVIDERS["ollama"].list_models() if ok else []
    checks["ollama"] = {
        "ok": ok and bool(models),
        "detail": f"{detail}; models: {', '.join(models) or 'none'}",
    }
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        checks["playwright"] = {
            "ok": True,
            "detail": "python package installed (browser binary is checked at run time)",
        }
    except ImportError:
        checks["playwright"] = {"ok": False, "detail": "playwright is not installed"}
    disk = shutil.disk_usage(settings.data_dir)
    checks["disk"] = {
        "ok": disk.free > 5 * 1024**3,
        "detail": f"{disk.free / 1024**3:.1f} GB free on the data volume",
    }
    queued = db.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")) or 0
    return {"version": __version__, "healthy": all(c["ok"] or c.get("optional") for c in checks.values()),
            "checks": checks, "queued_jobs": queued, "checked_at": datetime.now(UTC)}  # fmt: skip


@router.get("/system/overview")
def overview(db: Db) -> dict[str, Any]:
    def count(model: Any, *where: Any) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    by_severity = dict(db.execute(
        select(Finding.severity, func.count()).where(Finding.status == "open").group_by(Finding.severity)).all())  # fmt: skip
    by_category = dict(db.execute(
        select(Finding.category, func.count()).where(Finding.status == "open").group_by(Finding.category)).all())  # fmt: skip
    latest = db.scalar(select(TestRun).where(TestRun.status == "completed").order_by(TestRun.finished_at.desc()))
    results = dict(db.execute(
        select(TestResult.status, func.count()).group_by(TestResult.status)).all())  # fmt: skip
    recent = db.scalars(select(TestRun).order_by(TestRun.created_at.desc()).limit(5)).all()
    return {
        "projects": count(Project, Project.archived_at.is_(None)), "runs": count(TestRun),
        "open_findings": count(Finding, Finding.status == "open"),
        "verified_fixes": count(Finding, Finding.verification_status == "verified_fixed"),
        "pending_repairs": count(RepairProposal, RepairProposal.status == "proposed"),
        "findings_by_severity": by_severity, "findings_by_category": by_category,
        "results_all_time": {state: results.get(state, 0) for state in values(ResultStatus)},
        "latest_completed_run": serializers.run(latest) if latest else None,
        "recent_runs": [serializers.run(r) for r in recent],
    }  # fmt: skip


@router.get("/system/agents")
def agents(db: Db) -> dict[str, Any]:
    stats = {name: (runs, failed, avg) for name, runs, failed, avg in db.execute(
        select(AgentExecution.agent, func.count(), func.count().filter(AgentExecution.status != "succeeded"),
               func.avg(AgentExecution.duration_ms)).group_by(AgentExecution.agent))}  # fmt: skip
    recent = db.scalars(select(AgentExecution).order_by(AgentExecution.created_at.desc()).limit(30)).all()
    return {
        "agents": [{"name": s.name, "responsibility": s.responsibility, "inputs": s.inputs, "outputs": s.outputs,
                    "tools": list(s.tools), "timeout_s": s.timeout_s, "retry_limit": s.retry_limit,
                    "evidence": s.evidence, "uses_ai": s.uses_ai, "executions": stats.get(s.name, (0, 0, 0))[0],
                    "failures": stats.get(s.name, (0, 0, 0))[1],
                    "avg_duration_ms": round(float(stats.get(s.name, (0, 0, 0))[2] or 0))} for s in AGENTS.values()],
        "not_implemented": NOT_IMPLEMENTED,
        "recent_executions": [{"id": e.id, "agent": e.agent, "status": e.status, "duration_ms": e.duration_ms,
                               "input": e.input_summary, "output": e.output_summary, "error": e.error,
                               "created_at": e.created_at} for e in recent],
    }  # fmt: skip


@router.get("/system/jobs")
def jobs(db: Db, paging: PagingDep) -> dict[str, Any]:
    return paging.page(db, select(Job).order_by(Job.created_at.desc()), lambda j: {
        "id": j.id, "kind": j.kind, "status": j.status, "error": j.error, "created_at": j.created_at,
        "started_at": j.started_at, "finished_at": j.finished_at})  # fmt: skip


@router.get("/integrations")
def integrations() -> dict[str, Any]:
    """Honest status: which external integrations exist. None are implemented in the MVP."""
    docker_ok = sandbox.docker_available()
    return {
        "source_control": [
            {"name": "GitHub", "status": "not_implemented", "detail": "Import a local clone or a ZIP export instead."},
            {"name": "GitLab", "status": "not_implemented", "detail": "Import a local clone or a ZIP export instead."},
        ],
        "tools": [
            {"name": "Trivy", "purpose": "dependency, secret and config scanning", "image": security_scan.TRIVY_IMAGE,
             "status": "ready" if docker_ok and sandbox.image_present(security_scan.TRIVY_IMAGE) else "unavailable"},
            {"name": "Semgrep", "purpose": "static application security testing", "image": security_scan.SEMGREP_IMAGE,
             "status": "ready" if docker_ok and sandbox.image_present(security_scan.SEMGREP_IMAGE) else "unavailable"},
            {"name": "Playwright + axe-core", "purpose": "browser, accessibility and web performance checks",
             "image": None, "status": "ready"},
            {"name": "OWASP ZAP", "purpose": "dynamic application security testing", "image": None, "status": "not_implemented"},
            {"name": "k6", "purpose": "load testing", "image": None, "status": "not_implemented"},
        ],
    }  # fmt: skip


@router.get("/audit")
def audit_log(db: Db, paging: PagingDep, action: str | None = None) -> dict[str, Any]:
    query = select(AuditEvent).order_by(AuditEvent.created_at.desc())
    if action:
        query = query.where(AuditEvent.action.like(f"{action.replace('%', '')}%"))
    return paging.page(db, query, serializers.audit_event)
