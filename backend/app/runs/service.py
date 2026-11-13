"""Test run lifecycle: queue, execute each case through its engine, persist results and evidence."""

import logging
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.registry import agent_run
from app.config import get_settings
from app.db import SessionLocal
from app.domain import Engine, ResultStatus, RunStatus, values
from app.engines import api, browser, database_static, project_tests, security_scan
from app.engines.base import CaseOutcome, EngineContext
from app.jobs import queue
from app.models import ProjectVersion, TestCase, TestPlan, TestResult, TestRun
from app.security.ssrf import TargetNotAllowed, validate_target
from app.services import audit
from app.services.evidence import to_evidence, upsert_finding
from app.services.settings import get_execution_settings

log = logging.getLogger(__name__)
EngineFn = Callable[[EngineContext, dict[str, Any]], CaseOutcome]
_ENGINES: dict[str, EngineFn] = {
    Engine.BROWSER.value: browser.run_case,
    Engine.API.value: api.run_case,
    Engine.DEPENDENCY_SCAN.value: security_scan.dependency_scan,
    Engine.STATIC_ANALYSIS.value: security_scan.static_analysis,
    Engine.PROJECT_TESTS.value: project_tests.run_case,
    Engine.DATABASE_STATIC.value: database_static.run_case,
}
_CHECK_AGENTS = {"accessibility": "accessibility_qa", "web_performance": "performance_qa",
                 "latency": "performance_qa", "security_headers": "security_qa"}  # fmt: skip
_ENGINE_AGENTS = {
    Engine.BROWSER.value: "browser_qa", Engine.API.value: "api_qa", Engine.DEPENDENCY_SCAN.value: "security_qa",
    Engine.STATIC_ANALYSIS.value: "security_qa", Engine.PROJECT_TESTS.value: "functional_qa",
    Engine.DATABASE_STATIC.value: "database_qa",
}  # fmt: skip


class RunRejected(Exception):
    """The run cannot be started (reported as HTTP 422)."""


def agent_for(case: TestCase) -> str | None:
    return _CHECK_AGENTS.get(case.spec.get("check", "")) or _ENGINE_AGENTS.get(case.engine)


def create_run(db: Session, plan: TestPlan) -> TestRun:
    settings = get_execution_settings(db)
    if plan.target_url:
        try:
            validate_target(plan.target_url, settings.allowed_targets)
        except TargetNotAllowed as exc:
            raise RunRejected(str(exc)) from exc
    run = TestRun(plan_id=plan.id, project_id=plan.project_id, version_id=plan.version_id)
    db.add(run)
    db.flush()
    queue.enqueue(db, "test_run", run_id=run.id)
    audit.record(db, "run.queued", "test_run", run.id, plan_id=str(plan.id), target_url=plan.target_url)
    return run


def _execute_case(ctx: EngineContext, case: TestCase) -> CaseOutcome:
    if case.engine == Engine.MANUAL.value:
        return CaseOutcome(ResultStatus.NOT_TESTED, case.spec.get("reason", "No automated check; manual review required."))
    engine = _ENGINES.get(case.engine)
    if engine is None:
        return CaseOutcome.blocked(f"no engine registered for '{case.engine}'")
    try:
        return engine(ctx, case.spec)
    except Exception as exc:  # noqa: BLE001 - an engine crash must not abort the run or be hidden
        log.exception("engine crashed", extra={"case": case.key, "engine": case.engine})
        return CaseOutcome.blocked(f"engine error: {type(exc).__name__}: {str(exc)[:500]}")


def _persist(db: Session, run: TestRun, case: TestCase, outcome: CaseOutcome, duration_ms: int) -> int:
    result = TestResult(run_id=run.id, case_id=case.id, status=outcome.status.value, duration_ms=duration_ms,
                        message=outcome.message[:4000], details=outcome.details)  # fmt: skip
    db.add(result)
    db.flush()
    db.add_all([to_evidence(e, run_id=run.id, result_id=result.id) for e in outcome.evidence])
    for draft in outcome.findings:
        upsert_finding(
            db,
            draft,
            project_id=run.project_id,
            version_id=run.version_id,
            run_id=run.id,
            result_id=result.id,
        )
    db.commit()
    return len(outcome.findings)


def summarise(db: Session, run_id: uuid.UUID) -> dict[str, int]:
    """Counts per state taken from stored results only."""
    counts = Counter(db.scalars(select(TestResult.status).where(TestResult.run_id == run_id)))
    return {state: counts.get(state, 0) for state in values(ResultStatus)} | {"total": sum(counts.values())}


def execute_run(run_id: uuid.UUID, job_id: uuid.UUID | None = None) -> None:
    """Worker entry point."""
    with SessionLocal() as db:
        run = db.get(TestRun, run_id)
        if run is None or run.status != RunStatus.QUEUED.value:
            return
        run.status, run.started_at = RunStatus.RUNNING.value, datetime.now(UTC)
        db.commit()
        try:
            plan = run.plan
            version = db.get_one(ProjectVersion, run.version_id)
            data_dir = get_settings().data_dir
            ctx = EngineContext(
                workspace=(data_dir / version.workspace_path) if version.workspace_path else None,
                target_url=plan.target_url,
                settings=get_execution_settings(db),
                evidence_dir=get_settings().evidence_dir / str(run.id),
                inventory=version.snapshot.inventory if version.snapshot else {},
                allow_mutations=plan.allow_mutations,
            )
            per_agent: dict[str, Counter[str]] = defaultdict(Counter)
            agent_ms: Counter[str] = Counter()
            for case in sorted((c for c in plan.cases if c.enabled), key=lambda c: (c.priority, c.key)):
                started = time.monotonic()
                outcome = _execute_case(ctx, case)
                duration = int((time.monotonic() - started) * 1000)
                found = _persist(db, run, case, outcome, duration)
                agent = agent_for(case)
                if agent:
                    per_agent[agent][outcome.status.value] += 1
                    per_agent[agent]["findings"] += found
                    agent_ms[agent] += duration
            for case in (c for c in plan.cases if not c.enabled):
                _persist(db, run, case, CaseOutcome(ResultStatus.SKIPPED, "case disabled in the plan"), 0)
            for agent, counts in per_agent.items():
                with agent_run(db, agent, project_id=run.project_id, job_id=job_id, run_id=str(run.id)) as output:
                    output.update(counts, engine_ms=agent_ms[agent])
            run.status = RunStatus.COMPLETED.value
        except Exception as exc:
            db.rollback()
            log.exception("test run crashed", extra={"run_id": str(run_id)})
            run.status, run.error = RunStatus.FAILED.value, f"{type(exc).__name__}: {exc}"[:2000]
        run.finished_at = datetime.now(UTC)
        run.summary = summarise(db, run.id)
        audit.record(db, f"run.{run.status}", "test_run", run.id, actor=audit.SYSTEM, summary=run.summary)
        db.commit()


def fail_run_for_orphaned_job(db: Session, payload: dict[str, Any]) -> None:
    run = db.get(TestRun, uuid.UUID(payload["run_id"])) if payload.get("run_id") else None
    if run and run.status in (RunStatus.QUEUED.value, RunStatus.RUNNING.value):
        run.status, run.finished_at = RunStatus.FAILED.value, datetime.now(UTC)
        run.error = "worker stopped while the run was in progress; remaining cases were not executed"
        run.summary = summarise(db, run.id)
        db.commit()
