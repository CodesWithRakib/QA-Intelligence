"""Test plans, runs, results, reports and evidence."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api import serializers
from app.api.deps import Db, PagingDep, get_or_404
from app.config import get_settings
from app.ingestion.service import check_target_authorised
from app.jobs import queue
from app.models import Evidence, Project, ProjectVersion, Report, TestCase, TestPlan, TestResult, TestRun
from app.planning.planner import generate_plan
from app.reports import service as reports
from app.runs.service import create_run, plan_readiness, request_cancel
from app.services import audit

router = APIRouter(tags=["qa"])


class PlanCreate(BaseModel):
    project_id: uuid.UUID
    name: str | None = Field(default=None, max_length=200)
    target_url: str | None = Field(default=None, max_length=2048)
    allow_mutations: bool = False
    managed_environment: bool = False


class CaseUpdate(BaseModel):
    enabled: bool


@router.get("/plans")
def list_plans(db: Db, paging: PagingDep, project_id: uuid.UUID | None = None) -> dict[str, Any]:
    query = select(TestPlan).order_by(TestPlan.created_at.desc()).options(selectinload(TestPlan.cases))
    if project_id:
        query = query.where(TestPlan.project_id == project_id)
    return paging.page(db, query, serializers.plan)


@router.post("/plans", status_code=status.HTTP_201_CREATED)
def create_plan(body: PlanCreate, db: Db) -> dict[str, Any]:
    project = get_or_404(db, Project, body.project_id)
    version = db.scalar(
        select(ProjectVersion).where(ProjectVersion.project_id == project.id).order_by(ProjectVersion.number.desc())
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "project has no imported version")
    target = (body.target_url or "").strip() or project.target_url
    if target:
        check_target_authorised(db, target)
    if body.managed_environment:
        if not (project.test_config or {}).get("environment"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "store an environment profile for the project first")
        target = None  # the run points itself at the environment it starts
    plan = generate_plan(db, version, target_url=target, name=body.name, allow_mutations=body.allow_mutations,
                         managed_environment=body.managed_environment)  # fmt: skip
    db.commit()
    return serializers.plan(plan, with_cases=True)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: uuid.UUID, db: Db) -> dict[str, Any]:
    return serializers.plan(get_or_404(db, TestPlan, plan_id), with_cases=True)


@router.patch("/plans/{plan_id}/cases/{case_id}")
def update_case(plan_id: uuid.UUID, case_id: uuid.UUID, body: CaseUpdate, db: Db) -> dict[str, Any]:
    case = get_or_404(db, TestCase, case_id)
    if case.plan_id != plan_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found in this plan")
    case.enabled = body.enabled
    audit.record(db, "plan.case_toggled", "test_case", case.id, enabled=body.enabled, key=case.key)
    db.commit()
    return serializers.case(case)


@router.post("/plans/{plan_id}/suggestions", status_code=status.HTTP_202_ACCEPTED)
def request_suggestions(plan_id: uuid.UUID, db: Db) -> dict[str, Any]:
    plan = get_or_404(db, TestPlan, plan_id)
    job = queue.enqueue(db, "ai_suggest_cases", plan_id=plan.id)
    audit.record(db, "ai.suggestions_requested", "test_plan", plan.id)
    db.commit()
    return {"job_id": job.id}


@router.post("/plans/{plan_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def start_run(plan_id: uuid.UUID, db: Db) -> dict[str, Any]:
    run = create_run(db, get_or_404(db, TestPlan, plan_id))
    db.commit()
    return serializers.run(run)


@router.get("/runs")
def list_runs(db: Db, paging: PagingDep, project_id: uuid.UUID | None = None,
              run_status: str | None = None) -> dict[str, Any]:  # fmt: skip
    query = select(TestRun).order_by(TestRun.created_at.desc()).options(selectinload(TestRun.plan))
    if project_id:
        query = query.where(TestRun.project_id == project_id)
    if run_status:
        query = query.where(TestRun.status == run_status)
    return paging.page(db, query, serializers.run)


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, db: Db) -> dict[str, Any]:
    run = get_or_404(db, TestRun, run_id)
    results = db.scalars(
        select(TestResult).where(TestResult.run_id == run_id)
        .options(selectinload(TestResult.case), selectinload(TestResult.evidence))
    ).all()  # fmt: skip
    planned = len([c for c in run.plan.cases])
    return serializers.run(run) | {
        "planned_cases": planned,
        "results": [serializers.result(r) for r in sorted(results, key=lambda r: r.case.key)],
    }


@router.get("/plans/{plan_id}/readiness")
def get_plan_readiness(plan_id: uuid.UUID, db: Db) -> dict[str, Any]:
    """What can run right now, and exactly why the rest cannot. Nothing is executed."""
    return plan_readiness(db, get_or_404(db, TestPlan, plan_id))


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_run(run_id: uuid.UUID, db: Db) -> dict[str, Any]:
    run = request_cancel(db, get_or_404(db, TestRun, run_id))
    db.commit()
    return serializers.run(run)


@router.get("/runs/{run_id}/compare/{base_run_id}")
def compare_runs(run_id: uuid.UUID, base_run_id: uuid.UUID, db: Db) -> dict[str, Any]:
    """Case-by-case comparison of two runs of the same project (regression view)."""
    head, base = get_or_404(db, TestRun, run_id), get_or_404(db, TestRun, base_run_id)
    if head.project_id != base.project_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "runs belong to different projects")

    def states(run: TestRun) -> dict[str, tuple[str, str]]:
        rows = db.scalars(select(TestResult).where(TestResult.run_id == run.id).options(selectinload(TestResult.case))).all()
        return {f"{r.case.category}|{r.case.title}": (r.status, r.case.key) for r in rows}

    before, after = states(base), states(head)
    changes = []
    for identity in sorted(before.keys() | after.keys()):
        old, new = before.get(identity), after.get(identity)
        if (old and old[0]) != (new and new[0]):
            kind = (
                "new"
                if old is None
                else "removed"
                if new is None
                else (
                    "regression"
                    if new[0] == "failed" and old[0] == "passed"
                    else "fixed"
                    if new[0] == "passed" and old[0] == "failed"
                    else "changed"
                )
            )
            changes.append(
                {
                    "case": identity.split("|", 1)[1],
                    "category": identity.split("|", 1)[0],
                    "kind": kind,
                    "before": old[0] if old else None,
                    "after": new[0] if new else None,
                }
            )
    return {
        "base": serializers.run(base),
        "head": serializers.run(head),
        "changes": changes,
        "regressions": sum(1 for c in changes if c["kind"] == "regression"),
        "fixed": sum(1 for c in changes if c["kind"] == "fixed"),
        "unchanged": len(before.keys() & after.keys())
        - sum(1 for c in changes if c["kind"] in ("regression", "fixed", "changed")),
    }


@router.post("/runs/{run_id}/report", status_code=status.HTTP_201_CREATED)
def create_report(run_id: uuid.UUID, db: Db) -> dict[str, Any]:
    run = get_or_404(db, TestRun, run_id)
    if run.status in ("queued", "running"):
        raise HTTPException(status.HTTP_409_CONFLICT, "the run has not finished yet")
    report = reports.generate(db, run.id)
    db.commit()
    return serializers.report(report, full=True)


@router.get("/reports")
def list_reports(db: Db, paging: PagingDep, project_id: uuid.UUID | None = None) -> dict[str, Any]:
    query = select(Report).order_by(Report.created_at.desc())
    if project_id:
        query = query.where(Report.project_id == project_id)
    return paging.page(db, query, serializers.report)


@router.get("/reports/{report_id}")
def get_report(report_id: uuid.UUID, db: Db) -> dict[str, Any]:
    return serializers.report(get_or_404(db, Report, report_id), full=True)


@router.get("/evidence/{evidence_id}/file")
def get_evidence_file(evidence_id: uuid.UUID, db: Db) -> FileResponse:
    evidence = get_or_404(db, Evidence, evidence_id)
    data_dir = get_settings().data_dir.resolve()
    path = (data_dir / (evidence.path or "")).resolve()
    if not evidence.path or not path.is_relative_to(data_dir) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evidence file not found")
    media = "image/png" if path.suffix == ".png" else "application/octet-stream"
    return FileResponse(path, media_type=media, headers={"X-Content-Type-Options": "nosniff"})
