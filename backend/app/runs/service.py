"""Test run lifecycle.

Application -> Readiness -> Plan -> Queue -> Execute -> Verify -> Evidence -> Findings -> Report

For every enabled case the run service (1) asks the case's adapter whether it can run right now,
(2) executes it or records exactly why it could not, (3) commits the result, evidence and findings
immediately, and (4) publishes real progress. Adapters clean up what they acquired when the run ends.
"""

import hashlib
import logging
import shutil
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import ReadinessReport
from app.adapters.registry import ADAPTERS, adapter_for
from app.agents.registry import agent_run
from app.config import get_settings
from app.db import SessionLocal
from app.domain import Engine, FindingStatus, Readiness, ResultStatus, RunStatus, SourceKind, VerificationStatus, values
from app.engines.base import CaseOutcome, EngineContext
from app.jobs import queue
from app.models import Finding, Project, ProjectVersion, TestCase, TestPlan, TestResult, TestRun, VisualBaseline
from app.security.ssrf import TargetNotAllowed, validate_target
from app.services import audit
from app.services.evidence import to_evidence, upsert_finding
from app.services.settings import get_execution_settings

log = logging.getLogger(__name__)
_ADAPTER_ORDER = {adapter.name: index for index, adapter in enumerate(ADAPTERS)}


class RunRejected(Exception):
    """The run cannot be started (reported as HTTP 422)."""


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


def request_cancel(db: Session, run: TestRun) -> TestRun:
    if run.status not in (RunStatus.QUEUED.value, RunStatus.RUNNING.value):
        raise RunRejected(f"the run is already {run.status}")
    run.cancel_requested = True
    audit.record(db, "run.cancel_requested", "test_run", run.id)
    return run


def build_context(db: Session, plan: TestPlan, version: ProjectVersion, evidence_dir: Path) -> EngineContext:
    data_dir = get_settings().data_dir
    project = db.get_one(Project, plan.project_id)
    source_path = Path(version.source.location) if version.source.kind == SourceKind.LOCAL_FOLDER.value else None
    baselines = {
        b.key: data_dir / b.path for b in db.scalars(select(VisualBaseline).where(VisualBaseline.project_id == project.id))
    }
    return EngineContext(
        workspace=(data_dir / version.workspace_path) if version.workspace_path else None,
        target_url=plan.target_url, settings=get_execution_settings(db), evidence_dir=evidence_dir,
        inventory=version.snapshot.inventory if version.snapshot else {}, allow_mutations=plan.allow_mutations,
        project_config=dict(project.test_config or {}),
        source_path=source_path if source_path and source_path.is_dir() else None, baselines=baselines,
    )  # fmt: skip


def _spec(case: TestCase) -> dict[str, Any]:
    return {**case.spec, "_engine": case.engine}


def case_readiness(ctx: EngineContext, case: TestCase) -> ReadinessReport:
    """Can this case be executed right now? Never raises: an adapter error is itself a blocking reason."""
    if case.engine == Engine.MANUAL.value:
        return ReadinessReport(Readiness.NOT_APPLICABLE, ["manual case: no automated check exists"])
    adapter = adapter_for(case.engine, case.spec)
    if adapter is None:
        return ReadinessReport(Readiness.BLOCKED, [f"no adapter is registered for engine '{case.engine}'"])
    try:
        return adapter.readiness(ctx, _spec(case))
    except Exception as exc:  # noqa: BLE001 - readiness must always produce an answer
        log.exception("readiness check crashed", extra={"case": case.key})
        return ReadinessReport(Readiness.BLOCKED, [f"readiness check failed: {type(exc).__name__}: {str(exc)[:300]}"])


def plan_readiness(db: Session, plan: TestPlan) -> dict[str, Any]:
    """Readiness of every case of a plan, without executing anything (shown before a run is started)."""
    version = db.get_one(ProjectVersion, plan.version_id)
    ctx = build_context(db, plan, version, get_settings().evidence_dir / "readiness")
    cases = {case.key: case_readiness(ctx, case).as_dict() for case in plan.cases if case.enabled}
    counts = Counter(report["status"] for report in cases.values())
    return {"cases": cases, "summary": dict(counts), "checked_at": datetime.now(UTC).isoformat()}


def _execute_case(ctx: EngineContext, case: TestCase, report: ReadinessReport) -> CaseOutcome:
    if case.engine == Engine.MANUAL.value:
        return CaseOutcome(ResultStatus.NOT_TESTED, case.spec.get("reason", "No automated check; manual review required."))
    if not report.runnable:
        status = ResultStatus.NOT_APPLICABLE if report.status is Readiness.NOT_APPLICABLE else ResultStatus.BLOCKED
        label = report.status.value.replace("_", " ")
        return CaseOutcome(
            status, f"{label}: {'; '.join(report.reasons) or 'no reason recorded'}", {"readiness": report.as_dict()}
        )
    adapter = adapter_for(case.engine, case.spec)
    assert adapter is not None  # readiness already rejected unknown adapters
    try:
        return adapter.execute(ctx, _spec(case))
    except Exception as exc:  # noqa: BLE001 - an adapter crash must not abort the run or be hidden
        log.exception("adapter crashed", extra={"case": case.key, "adapter": adapter.name})
        return CaseOutcome.blocked(f"engine error: {type(exc).__name__}: {str(exc)[:500]}")


def _store_baselines(db: Session, run: TestRun, outcome: CaseOutcome) -> None:
    """First-run screenshots become the project's baselines (a later run compares against them)."""
    for key, source in (outcome.details.get("new_baselines") or {}).items():
        source_path = Path(source)
        if not source_path.is_file():
            continue
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        relative = f"baselines/{run.project_id}/{digest[:24]}.png"
        target = get_settings().data_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        with Image.open(target) as image:
            width, height = image.size
        db.add(VisualBaseline(project_id=run.project_id, key=key, path=relative, sha256=digest, width=width, height=height,
                              run_id=run.id))  # fmt: skip


def _persist(db: Session, run: TestRun, case: TestCase, outcome: CaseOutcome, duration_ms: int) -> int:
    result = TestResult(run_id=run.id, case_id=case.id, status=outcome.status.value, duration_ms=duration_ms,
                        message=outcome.message[:4000], details=outcome.details)  # fmt: skip
    db.add(result)
    db.flush()
    db.add_all([to_evidence(e, run_id=run.id, result_id=result.id) for e in outcome.evidence])
    if outcome.status is ResultStatus.PASSED:
        _resolve_previous_findings(db, run, case)
    for draft in outcome.findings:
        upsert_finding(db, draft, project_id=run.project_id, version_id=run.version_id, run_id=run.id, result_id=result.id)
    _store_baselines(db, run, outcome)
    db.commit()
    return len(outcome.findings)


def _resolve_previous_findings(db: Session, run: TestRun, case: TestCase) -> None:
    """The same check now passes: findings it raised earlier in this project are no longer reproduced."""
    earlier = db.scalars(
        select(Finding)
        .join(TestResult, Finding.result_id == TestResult.id)
        .join(TestCase, TestResult.case_id == TestCase.id)
        .where(Finding.project_id == run.project_id, Finding.status == FindingStatus.OPEN.value,
               TestCase.category == case.category, TestCase.title == case.title)
    ).all()  # fmt: skip
    for finding in earlier:
        finding.status = FindingStatus.FIXED.value
        finding.verification_status = VerificationStatus.VERIFIED_FIXED.value
        audit.record(db, "finding.no_longer_reproduced", "finding", finding.id, actor=audit.SYSTEM, run_id=str(run.id),
                     case=case.key)  # fmt: skip


def summarise(db: Session, run_id: uuid.UUID) -> dict[str, int]:
    """Counts per state taken from stored results only."""
    counts = Counter(db.scalars(select(TestResult.status).where(TestResult.run_id == run_id)))
    return {state: counts.get(state, 0) for state in values(ResultStatus)} | {"total": sum(counts.values())}


def _order(case: TestCase) -> tuple[int, int, str]:
    return case.priority, _ADAPTER_ORDER.get(str(case.spec.get("adapter")), len(_ADAPTER_ORDER)), case.key


def execute_run(run_id: uuid.UUID, job_id: uuid.UUID | None = None) -> None:
    """Worker entry point."""
    with SessionLocal() as db:
        run = db.get(TestRun, run_id)
        if run is None or run.status != RunStatus.QUEUED.value:
            return
        run.status, run.started_at = RunStatus.RUNNING.value, datetime.now(UTC)
        db.commit()
        ctx: EngineContext | None = None
        try:
            plan = run.plan
            version = db.get_one(ProjectVersion, run.version_id)
            ctx = build_context(db, plan, version, get_settings().evidence_dir / str(run.id))
            enabled = sorted((c for c in plan.cases if c.enabled), key=_order)
            per_agent: dict[str, Counter[str]] = defaultdict(Counter)
            agent_ms: Counter[str] = Counter()
            readiness: dict[str, Any] = {}
            cancelled = False
            for index, case in enumerate(enabled, start=1):
                db.refresh(run, ["cancel_requested"])
                if run.cancel_requested:
                    cancelled = True
                    _persist(
                        db, run, case, CaseOutcome(ResultStatus.SKIPPED, "run cancelled by the owner before this case started"), 0
                    )
                    continue
                adapter = adapter_for(case.engine, case.spec)
                run.progress = {"case": case.key, "title": case.title, "adapter": adapter.name if adapter else "manual",
                                "index": index, "total": len(enabled), "case_started_at": datetime.now(UTC).isoformat()}  # fmt: skip
                db.commit()
                started = time.monotonic()
                report = case_readiness(ctx, case)
                readiness[case.key] = report.as_dict()
                outcome = _execute_case(ctx, case, report)
                duration = int((time.monotonic() - started) * 1000)
                found = _persist(db, run, case, outcome, duration)
                run.readiness = dict(readiness)
                if adapter:
                    per_agent[adapter.agent][outcome.status.value] += 1
                    per_agent[adapter.agent]["findings"] += found
                    agent_ms[adapter.agent] += duration
            for case in (c for c in plan.cases if not c.enabled):
                _persist(db, run, case, CaseOutcome(ResultStatus.SKIPPED, "case disabled in the plan"), 0)
            for agent, counts in per_agent.items():
                with agent_run(db, agent, project_id=run.project_id, job_id=job_id, run_id=str(run.id)) as output:
                    output.update(counts, engine_ms=agent_ms[agent])
            run.status = RunStatus.CANCELLED.value if cancelled else RunStatus.COMPLETED.value
        except Exception as exc:
            db.rollback()
            log.exception("test run crashed", extra={"run_id": str(run_id)})
            run.status, run.error = RunStatus.FAILED.value, f"{type(exc).__name__}: {exc}"[:2000]
        cleanup = _cleanup(ctx) if ctx else {}
        run.finished_at = datetime.now(UTC)
        run.progress = {"cleanup": cleanup}
        run.summary = summarise(db, run.id)
        audit.record(db, f"run.{run.status}", "test_run", run.id, actor=audit.SYSTEM, summary=run.summary)
        db.commit()


def _cleanup(ctx: EngineContext) -> dict[str, Any]:
    """Every adapter releases what it acquired. A failing cleanup is recorded, never hidden."""
    report: dict[str, Any] = {}
    for adapter in reversed(ADAPTERS):  # reverse order: stop what depends on a resource before the resource
        try:
            result = adapter.cleanup(ctx)
        except Exception as exc:  # noqa: BLE001 - one failing cleanup must not prevent the others
            log.exception("cleanup failed", extra={"adapter": adapter.name})
            result = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        if result:
            report[adapter.name] = result
    return report


def fail_run_for_orphaned_job(db: Session, payload: dict[str, Any]) -> None:
    run = db.get(TestRun, uuid.UUID(payload["run_id"])) if payload.get("run_id") else None
    if run and run.status in (RunStatus.QUEUED.value, RunStatus.RUNNING.value):
        run.status, run.finished_at = RunStatus.FAILED.value, datetime.now(UTC)
        run.error = "worker stopped while the run was in progress; remaining cases were not executed"
        run.summary = summarise(db, run.id)
        db.commit()
