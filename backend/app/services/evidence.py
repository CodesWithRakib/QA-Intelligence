"""Evidence and finding persistence shared by test runs and repairs."""

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain import FindingStatus, VerificationStatus
from app.engines.base import EvidenceDraft, FindingDraft
from app.models import Evidence, Finding


def to_evidence(draft: EvidenceDraft, *, run_id: uuid.UUID | None = None, result_id: uuid.UUID | None = None,
                finding_id: uuid.UUID | None = None) -> Evidence:  # fmt: skip
    data_dir = get_settings().data_dir
    if draft.file:
        digest = hashlib.sha256(draft.file.read_bytes()).hexdigest()
        relative: str | None = Path(draft.file).resolve().relative_to(data_dir.resolve()).as_posix()
    else:
        digest = hashlib.sha256((draft.content or "").encode()).hexdigest()
        relative = None
    return Evidence(run_id=run_id, result_id=result_id, finding_id=finding_id, kind=draft.kind,
                    label=draft.label[:300], path=relative, content=draft.content, sha256=digest)  # fmt: skip


def upsert_finding(db: Session, draft: FindingDraft, *, project_id: uuid.UUID, version_id: uuid.UUID,
                   run_id: uuid.UUID | None, result_id: uuid.UUID | None) -> tuple[Finding, bool]:  # fmt: skip
    """Create the finding, or refresh it when the same defect is detected again. Returns (finding, created)."""
    finding = db.scalar(select(Finding).where(Finding.project_id == project_id, Finding.fingerprint == draft.fingerprint))
    created = finding is None
    if finding is None:
        finding = Finding(project_id=project_id, fingerprint=draft.fingerprint, category=draft.category.value,
                          source_tool=draft.source_tool, title="", description="", severity=draft.severity.value,
                          version_id=version_id)  # fmt: skip
        db.add(finding)
    elif finding.status == FindingStatus.FIXED.value and finding.version_id != version_id:
        # Detected again in a newer version after a verified fix: the defect is back (or was never merged).
        finding.status = FindingStatus.OPEN.value
        finding.verification_status = VerificationStatus.UNVERIFIED.value
    finding.version_id, finding.run_id, finding.result_id = version_id, run_id, result_id
    finding.severity = draft.severity.value
    finding.title, finding.description = draft.title[:400], draft.description
    finding.reproduction_steps = draft.reproduction_steps
    finding.affected_component = draft.affected_component[:512]
    finding.confidence = draft.confidence
    finding.remediation = draft.remediation
    finding.facts = draft.facts
    finding.last_seen_at = datetime.now(UTC)
    db.flush()
    if created:
        db.add_all([to_evidence(e, run_id=run_id, finding_id=finding.id) for e in draft.evidence])
    return finding, created
