"""Findings and the repair approval workflow."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, or_, select
from sqlalchemy.orm import selectinload

from app.api import serializers
from app.api.deps import Db, PagingDep, get_or_404
from app.domain import Category, Classification, FindingStatus, ProposalStatus, Severity, values
from app.jobs import queue
from app.models import Finding, RepairExecution, RepairProposal
from app.repair import service as repair
from app.services import audit

router = APIRouter(tags=["findings"])
_SEVERITY_ORDER = case({name: index for index, name in enumerate(values(Severity))}, value=Finding.severity)


class FindingUpdate(BaseModel):
    status: Literal["open", "wont_fix", "false_positive"]
    note: str = Field(default="", max_length=1000)


class Decision(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=2000)
    confirm_sensitive: bool = False


@router.get("/findings")
def list_findings(
    db: Db, paging: PagingDep,
    project_id: uuid.UUID | None = None, category: Category | None = None, severity: Severity | None = None,
    finding_status: FindingStatus | None = None, classification: Classification | None = None, q: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:  # fmt: skip
    query = select(Finding).order_by(_SEVERITY_ORDER, Finding.last_seen_at.desc())
    for column, value in ((Finding.project_id, project_id), (Finding.category, category),
                          (Finding.severity, severity), (Finding.status, finding_status), (Finding.classification, classification)):  # fmt: skip
        if value is not None:
            query = query.where(column == (value.value if hasattr(value, "value") else value))
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        query = query.where(or_(Finding.title.ilike(like), Finding.affected_component.ilike(like)))
    return paging.page(db, query, serializers.finding)


@router.get("/findings/{finding_id}")
def get_finding(finding_id: uuid.UUID, db: Db) -> dict[str, Any]:
    finding = get_or_404(db, Finding, finding_id)
    proposals = db.scalars(
        select(RepairProposal).where(RepairProposal.finding_id == finding_id).order_by(RepairProposal.created_at.desc())
        .options(selectinload(RepairProposal.executions).selectinload(RepairExecution.regression_results))
    ).all()  # fmt: skip
    return serializers.finding(finding, full=True) | {"proposals": [serializers.proposal(p, full=True) for p in proposals]}


@router.patch("/findings/{finding_id}")
def update_finding(finding_id: uuid.UUID, body: FindingUpdate, db: Db) -> dict[str, Any]:
    finding = get_or_404(db, Finding, finding_id)
    previous, finding.status = finding.status, body.status
    audit.record(
        db,
        "finding.status_changed",
        "finding",
        finding.id,
        previous=previous,
        new=body.status,
        note=body.note,
    )
    db.commit()
    return serializers.finding(finding, full=True)


@router.post("/findings/{finding_id}/explanation", status_code=status.HTTP_202_ACCEPTED)
def request_explanation(finding_id: uuid.UUID, db: Db) -> dict[str, Any]:
    finding = get_or_404(db, Finding, finding_id)
    job = queue.enqueue(db, "ai_explain_finding", finding_id=finding.id)
    audit.record(db, "ai.explanation_requested", "finding", finding.id)
    db.commit()
    return {"job_id": job.id}


@router.post("/findings/{finding_id}/repair-proposals", status_code=status.HTTP_202_ACCEPTED)
def request_repair(finding_id: uuid.UUID, db: Db) -> dict[str, str]:
    repair.request_proposal(db, get_or_404(db, Finding, finding_id))
    db.commit()
    return {"status": "queued"}


@router.get("/repairs")
def list_repairs(db: Db, paging: PagingDep, project_id: uuid.UUID | None = None,
                 proposal_status: ProposalStatus | None = None) -> dict[str, Any]:  # fmt: skip
    query = (select(RepairProposal).join(Finding).order_by(RepairProposal.created_at.desc())
             .options(selectinload(RepairProposal.finding), selectinload(RepairProposal.executions)))  # fmt: skip
    if project_id:
        query = query.where(Finding.project_id == project_id)
    if proposal_status:
        query = query.where(RepairProposal.status == proposal_status.value)
    return paging.page(db, query, serializers.proposal)


@router.get("/repairs/{proposal_id}")
def get_repair(proposal_id: uuid.UUID, db: Db) -> dict[str, Any]:
    return serializers.proposal(get_or_404(db, RepairProposal, proposal_id), full=True)


@router.post("/repairs/{proposal_id}/decision")
def decide_repair(proposal_id: uuid.UUID, body: Decision, db: Db) -> dict[str, Any]:
    proposal = repair.decide(db, get_or_404(db, RepairProposal, proposal_id), approve=body.approve,
                             note=body.note, confirm_sensitive=body.confirm_sensitive)  # fmt: skip
    db.commit()
    return serializers.proposal(proposal, full=True)


@router.get("/repairs/{proposal_id}/patch", response_class=PlainTextResponse)
def download_patch(proposal_id: uuid.UUID, db: Db) -> PlainTextResponse:
    proposal = get_or_404(db, RepairProposal, proposal_id)
    return PlainTextResponse(proposal.diff, headers={
        "Content-Disposition": f'attachment; filename="repair-{proposal.id.hex[:8]}.patch"'})  # fmt: skip
