"""Report generation. Every number is a count of stored rows; nothing is estimated or inferred."""

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agents.registry import agent_run
from app.config import get_settings
from app.domain import ResultStatus, Severity, values
from app.models import (
    Finding,
    Project,
    ProjectVersion,
    RepairExecution,
    RepairProposal,
    Report,
    TestResult,
    TestRun,
)
from app.services import audit

_LABEL = {"passed": "Passed", "failed": "Failed", "skipped": "Skipped", "blocked": "Blocked",
          "not_tested": "Not tested", "not_applicable": "Not applicable"}  # fmt: skip


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")[:220]


def generate(db: Session, run_id: uuid.UUID) -> Report:
    run = db.scalar(
        select(TestRun).where(TestRun.id == run_id)
        .options(selectinload(TestRun.results).selectinload(TestResult.case), selectinload(TestRun.plan))
    )  # fmt: skip
    if run is None:
        raise LookupError("run not found")
    project = db.get_one(Project, run.project_id)
    version = db.get_one(ProjectVersion, run.version_id)
    with agent_run(db, "reporting", project_id=project.id, run_id=str(run.id)) as out:
        results = sorted(run.results, key=lambda r: r.case.key)
        counts = Counter(r.status for r in results)
        findings = list(db.scalars(select(Finding).where(Finding.run_id == run.id)))
        order = values(Severity)
        findings.sort(key=lambda f: (order.index(f.severity), f.title))
        executions = list(db.scalars(
            select(RepairExecution).join(RepairProposal).join(Finding)
            .where(Finding.project_id == project.id).options(selectinload(RepairExecution.proposal))
        ))  # fmt: skip
        executed = counts["passed"] + counts["failed"]
        unverified = sum(counts[s] for s in ("blocked", "not_tested", "skipped"))

        lines = [
            f"# QA report: {project.name}", "",
            f"- Generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
            f"- Project version: {version.number} (content hash `{version.content_hash[:12]}`)",
            f"- Test plan: {run.plan.name}",
            f"- Target URL: {run.plan.target_url or 'none (static analysis only)'}",
            f"- Run status: **{run.status}**" + (f" - {run.error}" if run.error else ""),
            "", "## Result summary", "", "| State | Count |", "|---|---|",
            *[f"| {_LABEL[state]} | {counts.get(state, 0)} |" for state in values(ResultStatus)],
            f"| **Total** | **{len(results)}** |", "",
            f"{executed} of {len(results)} planned checks were actually executed to a pass/fail verdict. "
            f"{unverified} check(s) have **no verdict** (blocked, skipped or not tested) and must not be read as passing.",
            "", "## Results", "", "| Case | Category | State | Detail |", "|---|---|---|---|",
            *[f"| {r.case.key} {_cell(r.case.title)} | {r.case.category} | {_LABEL[r.status]} | {_cell(r.message)} |"
              for r in results],
            "", f"## Findings from this run ({len(findings)})", "",
        ]  # fmt: skip
        if findings:
            lines += [
                "| Severity | Category | Finding | Component | Confidence | Tool |",
                "|---|---|---|---|---|---|",
            ]
            lines += [f"| {f.severity} | {f.category} | {_cell(f.title)} | {_cell(f.affected_component)} | "
                      f"{f.confidence:.0%} | {f.source_tool} |" for f in findings]  # fmt: skip
        else:
            lines.append("No findings were recorded. This only covers the checks that were executed (see summary).")
        lines += ["", f"## Repairs for this project ({len(executions)})", ""]
        if executions:
            lines += ["| Strategy | Files | Status | Outcome |", "|---|---|---|---|"]
            lines += [f"| {e.proposal.strategy} ({e.proposal.generated_by}) | {_cell(', '.join(e.proposal.affected_files))} | "
                      f"{e.status} | {_cell(e.outcome)} |" for e in executions]  # fmt: skip
            lines += [
                "",
                "Verified repairs exist only as a git branch in an isolated workspace; the original source was not changed.",
            ]
        else:
            lines.append("No repair has been executed.")
        lines += ["", "## Coverage notes and limits", "", *[f"- {note}" for note in run.plan.notes],
                  "- Automated accessibility rules cover only part of WCAG; manual review is still required.",
                  "- Performance numbers are single-host lab samples, not load tests or field data.",
                  "- AI-generated explanations and suggestions are advisory and are not counted as evidence."]  # fmt: skip
        content = "\n".join(lines) + "\n"
        data: dict[str, Any] = {"counts": {s: counts.get(s, 0) for s in values(ResultStatus)}, "total": len(results),
                                "findings": len(findings), "repairs": len(executions)}  # fmt: skip
        report = Report(project_id=project.id, run_id=run.id, title=f"QA report - {project.name} - v{version.number}",
                        content_markdown=content, data=data)  # fmt: skip
        db.add(report)
        db.flush()
        path = get_settings().reports_dir / f"{report.id}.md"
        path.write_text(content, encoding="utf-8")
        report.path = f"reports/{report.id}.md"
        out.update(data)
    audit.record(db, "report.generated", "report", report.id, run_id=str(run.id))
    return report
