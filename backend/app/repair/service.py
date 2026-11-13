"""Repair workflow.

detect -> propose -> owner approval -> isolated copy + git branch -> baseline checks ->
apply -> independent review -> same checks again -> verdict -> audit.

A fix is marked VERIFIED only when (a) the defect was reproduced before the patch, (b) the
targeted check passes after it, (c) the policy review approved it, and (d) the project's tests ran
both times with no newly failing test. Anything else is FAILED, with the reason recorded.
The user's original folder and the stored project version are never modified.
"""

import logging
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.registry import agent_run
from app.ai.providers import ProviderError
from app.ai.service import AIUnavailable
from app.config import get_settings
from app.db import SessionLocal
from app.domain import ExecutionStatus, FindingStatus, ProposalStatus, ResultStatus, VerificationStatus
from app.engines import project_tests, security_scan
from app.engines.base import EvidenceDraft
from app.engines.sandbox import SandboxUnavailable
from app.jobs import queue
from app.models import Evidence, Finding, ProjectVersion, RegressionResult, RepairExecution, RepairProposal
from app.repair import review, strategies
from app.services import audit
from app.services.evidence import to_evidence
from app.services.settings import ExecutionSettings, get_execution_settings

log = logging.getLogger(__name__)


class RepairRejected(Exception):
    """The requested repair action is not allowed in the current state (HTTP 409/422)."""


@dataclass
class Check:
    name: str
    status: ResultStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _workspace(version: ProjectVersion) -> Path:
    if not version.workspace_path:
        raise strategies.RepairNotPossible("this project has no source files to patch")
    return get_settings().data_dir / version.workspace_path


# ---------------------------------------------------------------- proposals


def request_proposal(db: Session, finding: Finding) -> None:
    open_states = (ProposalStatus.PROPOSED.value, ProposalStatus.APPROVED.value)
    existing = db.scalars(select(RepairProposal.status).where(RepairProposal.finding_id == finding.id))
    if any(state in open_states for state in existing):
        raise RepairRejected("a repair proposal for this finding is already pending")
    queue.enqueue(db, "repair_proposal", finding_id=finding.id)
    audit.record(db, "repair.proposal_requested", "finding", finding.id)


def generate_proposal(finding_id: uuid.UUID, job_id: uuid.UUID | None = None) -> None:
    """Worker entry point. A failed attempt is stored too, so the owner sees why."""
    with SessionLocal() as db:
        finding = db.get_one(Finding, finding_id)
        version = db.get_one(ProjectVersion, finding.version_id)
        proposal = RepairProposal(finding_id=finding.id, strategy="none", generated_by="none", rationale="", diff="")
        try:
            with agent_run(db, "code_repair", project_id=finding.project_id, job_id=job_id, finding=str(finding.id)) as out:
                workspace = _workspace(version)
                draft = strategies.propose(db, finding, workspace)
                proposal.strategy, proposal.generated_by = draft.strategy, draft.generated_by
                proposal.rationale, proposal.changes = draft.rationale, draft.changes
                proposal.diff, proposal.affected_files = draft.diff(workspace), draft.affected_files
                proposal.sensitive = draft.sensitive
                finding.status = FindingStatus.FIX_PROPOSED.value
                out.update(strategy=draft.strategy, files=draft.affected_files, generated_by=draft.generated_by)
        except (strategies.RepairNotPossible, AIUnavailable, ProviderError) as exc:
            db.rollback()
            proposal.status = ProposalStatus.FAILED.value
            proposal.rationale = f"No proposal could be generated: {exc}"
        db.add(proposal)
        db.flush()
        audit.record(db, f"repair.proposal_{proposal.status}", "repair_proposal", proposal.id, actor=audit.SYSTEM,
                     finding_id=str(finding.id), strategy=proposal.strategy)  # fmt: skip
        db.commit()


def decide(db: Session, proposal: RepairProposal, *, approve: bool, note: str, confirm_sensitive: bool) -> RepairProposal:
    if proposal.status != ProposalStatus.PROPOSED.value:
        raise RepairRejected(f"proposal is '{proposal.status}', only 'proposed' can be decided")
    if approve and proposal.sensitive and not confirm_sensitive:
        raise RepairRejected("this patch touches security- or payment-sensitive paths; explicit confirmation is required")
    proposal.decided_by, proposal.decided_at, proposal.decision_note = (
        audit.LOCAL_OWNER,
        datetime.now(UTC),
        note,
    )
    finding = db.get_one(Finding, proposal.finding_id)
    if approve:
        proposal.status = ProposalStatus.APPROVED.value
        execution = RepairExecution(proposal_id=proposal.id)
        db.add(execution)
        db.flush()
        queue.enqueue(db, "repair_execution", execution_id=execution.id)
    else:
        proposal.status = ProposalStatus.REJECTED.value
        finding.status = FindingStatus.OPEN.value
    audit.record(db, "repair.approved" if approve else "repair.rejected", "repair_proposal", proposal.id,
                 note=note, sensitive=proposal.sensitive, files=proposal.affected_files)  # fmt: skip
    return proposal


# ---------------------------------------------------------------- execution


def _prepare_branch(source: Path, execution: RepairExecution, version_number: int) -> Path:
    relative = f"repairs/{execution.id.hex}"
    workspace = get_settings().data_dir / relative
    shutil.copytree(source, workspace)
    review.git(workspace, "init", "-q", "-b", review.BASE_BRANCH)
    review.git(workspace, "add", "-A")
    review.git(workspace, "commit", "-q", "-m", f"baseline: imported version {version_number}")
    execution.branch = strategies.new_branch_name(execution.id)
    review.git(workspace, "checkout", "-q", "-b", execution.branch)
    execution.workspace_path = relative
    return workspace


def _run_tests(workspace: Path, inventory: dict[str, Any], settings: ExecutionSettings) -> tuple[Check, set[str]]:
    commands = inventory.get("test_commands") or []
    if not commands:
        return Check("project_tests", ResultStatus.NOT_TESTED, "the project has no runnable test command"), set()
    failing: set[str] = set()
    worst, messages, outputs = ResultStatus.PASSED, [], []
    for command in commands:
        suite = project_tests.run_suite(workspace, command["component"], command["runtime"], settings)
        prefix = f"{command['component']}::" if command["component"] else ""
        failing |= {prefix + test for test in suite.failing_tests}
        messages.append(f"{command['component'] or '.'}: {suite.message}")
        outputs.append((suite.output or suite.install_output)[-4000:])
        if suite.status is ResultStatus.BLOCKED or (suite.status is ResultStatus.FAILED and worst is not ResultStatus.BLOCKED):
            worst = suite.status
    return Check("project_tests", worst, "; ".join(messages),
                 {"failing_tests": sorted(failing), "output_tail": "\n---\n".join(outputs)}), failing  # fmt: skip


def _targeted(finding: Finding, workspace: Path, settings: ExecutionSettings, tests: Check, failing: set[str]) -> Check:
    """Is the specific defect present? FAILED = present, PASSED = absent."""
    facts = finding.facts
    if facts.get("kind") == "vulnerable_dependency":
        try:
            report, raw = security_scan.run_trivy(workspace, "vuln", settings)
        except SandboxUnavailable as exc:
            return Check("targeted", ResultStatus.BLOCKED, str(exc))
        if report is None:
            return Check("targeted", ResultStatus.BLOCKED, f"dependency scan did not complete: {raw[-300:]}")
        entry = security_scan.vulnerable_packages(report).get((facts["manifest"], facts["package"]))
        known = set(a["id"] for a in facts.get("advisories", []))
        remaining = sorted(known & {a["id"] for a in entry["advisories"]}) if entry else []
        if remaining:
            return Check("targeted", ResultStatus.FAILED, f"{facts['package']} still reports {', '.join(remaining)}",
                         {"remaining": remaining, "version": entry["installed_version"] if entry else None})  # fmt: skip
        return Check("targeted", ResultStatus.PASSED, f"none of the reported advisories remain for {facts['package']}")
    if facts.get("kind") == "test_failure":
        if tests.status in (ResultStatus.BLOCKED, ResultStatus.NOT_TESTED):
            return Check("targeted", ResultStatus.BLOCKED, f"tests could not run: {tests.message}")
        prefix = f"{facts.get('component')}::" if facts.get("component") else ""
        still = sorted(failing & {prefix + t for t in facts.get("failing_tests", [])})
        if still:
            return Check(
                "targeted",
                ResultStatus.FAILED,
                f"{len(still)} originally failing test(s) fail",
                {"failing": still},
            )
        return Check("targeted", ResultStatus.PASSED, "the originally failing tests pass")
    return Check("targeted", ResultStatus.BLOCKED, "no targeted check exists for this finding type")


def _verdict(before: dict[str, Check], after: dict[str, Check], policy_ok: bool,
             failing_before: set[str], failing_after: set[str]) -> tuple[bool, str]:  # fmt: skip
    if before["targeted"].status is not ResultStatus.FAILED:
        return (
            False,
            f"the defect was not reproduced before patching ({before['targeted'].message}); nothing to verify",
        )
    if not policy_ok:
        return False, "the independent policy review rejected the patch; it was not verified"
    if after["targeted"].status is not ResultStatus.PASSED:
        return False, f"the patch did not resolve the defect: {after['targeted'].message}"
    runnable = (ResultStatus.PASSED, ResultStatus.FAILED)
    if before["project_tests"].status not in runnable or after["project_tests"].status not in runnable:
        return False, ("the patch resolves the targeted check, but regression could not be verified because the "
                       f"project's tests did not run (before: {before['project_tests'].message}; "
                       f"after: {after['project_tests'].message})")  # fmt: skip
    new_failures = sorted(failing_after - failing_before)
    if new_failures:
        return (
            False,
            f"the patch introduces {len(new_failures)} newly failing test(s): {', '.join(new_failures[:10])}",
        )
    remaining = (
        f" {len(failing_after)} pre-existing test failure(s) remain and are unrelated to this patch." if failing_after else ""
    )
    return True, f"Defect reproduced before the patch, resolved after it, no newly failing tests.{remaining}"


def execute(execution_id: uuid.UUID, job_id: uuid.UUID | None = None) -> None:
    """Worker entry point."""
    with SessionLocal() as db:
        execution = db.get_one(RepairExecution, execution_id)
        if execution.status != ExecutionStatus.QUEUED.value:
            return
        proposal = execution.proposal
        finding = db.get_one(Finding, proposal.finding_id)
        version = db.get_one(ProjectVersion, finding.version_id)
        execution.status, execution.started_at = ExecutionStatus.RUNNING.value, datetime.now(UTC)
        db.commit()
        settings = get_execution_settings(db)
        inventory = version.snapshot.inventory if version.snapshot else {}
        verified, outcome = False, ""
        try:
            workspace = _prepare_branch(_workspace(version), execution, version.number)
            db.commit()

            def measure(phase: str) -> tuple[dict[str, Check], set[str]]:
                with agent_run(db, "regression_verifier", project_id=finding.project_id, job_id=job_id,
                               execution=str(execution.id), phase=phase) as out:  # fmt: skip
                    tests, failing = _run_tests(workspace, inventory, settings)
                    targeted = _targeted(finding, workspace, settings, tests, failing)
                    for check in (targeted, tests):
                        db.add(RegressionResult(execution_id=execution.id, phase=phase, check_name=check.name,
                                                status=check.status.value, message=check.message[:4000], details=check.details))  # fmt: skip
                    out.update(targeted=targeted.status.value, project_tests=tests.status.value)
                db.commit()
                return {"targeted": targeted, "project_tests": tests}, failing

            before, failing_before = measure("before")
            for change in proposal.changes:
                target = (workspace / change["path"]).resolve()
                if not target.is_relative_to(workspace.resolve()):
                    raise strategies.RepairNotPossible(f"patch path escapes the workspace: {change['path']}")
                target.write_text(change["new_content"], encoding="utf-8", newline="")
            review.git(workspace, "add", "-A")
            review.git(workspace, "commit", "-q", "-m", f"repair: {finding.title[:60]}")

            with agent_run(db, "independent_reviewer", project_id=finding.project_id, job_id=job_id,
                           execution=str(execution.id)) as out:  # fmt: skip
                checks = review.policy_review(workspace, proposal)
                policy_ok = all(check["passed"] for check in checks)
                execution.review = {"policy_approved": policy_ok, "checks": checks,
                                    "ai_review": review.ai_review(db, proposal, finding.project_id)}  # fmt: skip
                out.update(policy_approved=policy_ok, ai_review=execution.review["ai_review"].get("status"))
            db.commit()

            if policy_ok:
                after, failing_after = measure("after")
                verified, outcome = _verdict(before, after, policy_ok, failing_before, failing_after)
            else:
                failed = [c["name"] for c in checks if not c["passed"]]
                outcome = f"the independent policy review rejected the patch ({', '.join(failed)}); it was not verified"
            execution.status = (ExecutionStatus.VERIFIED if verified else
                                ExecutionStatus.FAILED if policy_ok else ExecutionStatus.REVIEW_REJECTED).value  # fmt: skip
        except (strategies.RepairNotPossible, review.GitError, SandboxUnavailable, OSError) as exc:
            db.rollback()
            log.exception("repair execution failed", extra={"execution_id": str(execution_id)})
            execution.status, outcome = (
                ExecutionStatus.FAILED.value,
                f"execution error: {type(exc).__name__}: {exc}"[:2000],
            )

        execution.outcome, execution.finished_at = outcome, datetime.now(UTC)
        proposal.status = (ProposalStatus.VERIFIED if verified else ProposalStatus.FAILED).value
        if verified:
            finding.status, finding.verification_status = (
                FindingStatus.FIXED.value,
                VerificationStatus.VERIFIED_FIXED.value,
            )
            db.add(to_evidence_diff(finding.id, proposal))
        else:
            finding.status, finding.verification_status = (
                FindingStatus.OPEN.value,
                VerificationStatus.VERIFICATION_FAILED.value,
            )
        audit.record(db, f"repair.{execution.status}", "repair_execution", execution.id, actor=audit.SYSTEM,
                     finding_id=str(finding.id), outcome=outcome[:500], branch=execution.branch)  # fmt: skip
        db.commit()


def to_evidence_diff(finding_id: uuid.UUID, proposal: RepairProposal) -> Evidence:
    return to_evidence(EvidenceDraft("diff", f"verified patch ({proposal.strategy})", proposal.diff), finding_id=finding_id)


def fail_execution_for_orphaned_job(db: Session, payload: dict[str, Any]) -> None:
    execution = db.get(RepairExecution, uuid.UUID(payload["execution_id"])) if payload.get("execution_id") else None
    if execution and execution.status in (ExecutionStatus.QUEUED.value, ExecutionStatus.RUNNING.value):
        execution.status, execution.finished_at = ExecutionStatus.FAILED.value, datetime.now(UTC)
        execution.outcome = "worker stopped during execution; the patch was not verified"
        execution.proposal.status = ProposalStatus.FAILED.value
        db.commit()
