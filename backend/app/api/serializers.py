"""ORM -> JSON-safe dictionaries. Kept in one place so API contracts stay consistent."""

from typing import Any

from app.models import (
    AuditEvent,
    Evidence,
    Finding,
    Project,
    ProjectVersion,
    RegressionResult,
    RepairExecution,
    RepairProposal,
    Report,
    TestCase,
    TestPlan,
    TestResult,
    TestRun,
)


def version(v: ProjectVersion) -> dict[str, Any]:
    return {"id": v.id, "number": v.number, "content_hash": v.content_hash, "file_count": v.file_count,
            "size_bytes": v.size_bytes, "has_files": v.workspace_path is not None, "created_at": v.created_at}  # fmt: skip


def project(p: Project) -> dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "slug": p.slug, "description": p.description, "target_url": p.target_url,
        "created_at": p.created_at,
        "sources": [{"kind": s.kind, "location": s.location, "config": s.config} for s in p.sources],
        "versions": [version(v) for v in p.versions],
    }  # fmt: skip


def case(c: TestCase) -> dict[str, Any]:
    return {"id": c.id, "key": c.key, "title": c.title, "category": c.category, "engine": c.engine,
            "priority": c.priority, "origin": c.origin, "enabled": c.enabled, "spec": c.spec}  # fmt: skip


def plan(p: TestPlan, *, with_cases: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": p.id, "project_id": p.project_id, "version_id": p.version_id, "name": p.name,
        "target_url": p.target_url, "allow_mutations": p.allow_mutations, "notes": p.notes,
        "case_count": len(p.cases), "created_at": p.created_at,
    }  # fmt: skip
    if with_cases:
        data["cases"] = [case(c) for c in p.cases]
    return data


def run(r: TestRun) -> dict[str, Any]:
    return {"id": r.id, "plan_id": r.plan_id, "project_id": r.project_id, "version_id": r.version_id,
            "plan_name": r.plan.name, "target_url": r.plan.target_url, "status": r.status, "summary": r.summary,
            "error": r.error, "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at}  # fmt: skip


def evidence(e: Evidence) -> dict[str, Any]:
    return {"id": e.id, "kind": e.kind, "label": e.label, "content": e.content, "has_file": e.path is not None,
            "sha256": e.sha256, "created_at": e.created_at}  # fmt: skip


def result(r: TestResult) -> dict[str, Any]:
    return {"id": r.id, "status": r.status, "duration_ms": r.duration_ms, "message": r.message, "details": r.details,
            "case": case(r.case), "evidence": [evidence(e) for e in r.evidence]}  # fmt: skip


def finding(f: Finding, *, full: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": f.id, "project_id": f.project_id, "version_id": f.version_id, "run_id": f.run_id,
        "category": f.category, "severity": f.severity, "title": f.title, "affected_component": f.affected_component,
        "confidence": f.confidence, "status": f.status, "verification_status": f.verification_status,
        "source_tool": f.source_tool, "created_at": f.created_at, "last_seen_at": f.last_seen_at,
    }  # fmt: skip
    if full:
        data |= {"description": f.description, "reproduction_steps": f.reproduction_steps,
                 "remediation": f.remediation, "facts": f.facts, "ai_explanation": f.ai_explanation,
                 "evidence": [evidence(e) for e in f.evidence]}  # fmt: skip
    return data


def regression(r: RegressionResult) -> dict[str, Any]:
    return {
        "phase": r.phase,
        "check": r.check_name,
        "status": r.status,
        "message": r.message,
        "details": r.details,
    }


def execution(e: RepairExecution) -> dict[str, Any]:
    return {"id": e.id, "status": e.status, "branch": e.branch, "workspace_path": e.workspace_path,
            "review": e.review, "outcome": e.outcome, "created_at": e.created_at, "started_at": e.started_at,
            "finished_at": e.finished_at, "regression_results": [regression(r) for r in e.regression_results]}  # fmt: skip


def proposal(p: RepairProposal, *, full: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": p.id, "finding_id": p.finding_id, "finding_title": p.finding.title,
        "finding_severity": p.finding.severity, "project_id": p.finding.project_id, "strategy": p.strategy,
        "generated_by": p.generated_by, "status": p.status, "sensitive": p.sensitive,
        "affected_files": p.affected_files, "created_at": p.created_at, "decided_by": p.decided_by,
        "decided_at": p.decided_at, "decision_note": p.decision_note,
        "latest_execution_status": p.executions[-1].status if p.executions else None,
    }  # fmt: skip
    if full:
        data |= {"rationale": p.rationale, "diff": p.diff, "executions": [execution(e) for e in p.executions]}
    return data


def report(r: Report, *, full: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {"id": r.id, "project_id": r.project_id, "run_id": r.run_id, "title": r.title,
                            "data": r.data, "created_at": r.created_at}  # fmt: skip
    if full:
        data["content_markdown"] = r.content_markdown
    return data


def audit_event(a: AuditEvent) -> dict[str, Any]:
    return {"id": a.id, "actor": a.actor, "action": a.action, "entity_type": a.entity_type,
            "entity_id": a.entity_id, "details": a.details, "created_at": a.created_at}  # fmt: skip
