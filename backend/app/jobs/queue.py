"""PostgreSQL-backed job queue. Chosen over Redis for the MVP: see ADR-004."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain import JobStatus
from app.models import Job


def enqueue(db: Session, kind: str, **payload: Any) -> Job:
    """Add a job to the current transaction; it becomes visible to the worker on commit."""
    job = Job(kind=kind, payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()})
    db.add(job)
    db.flush()
    return job


def claim_next(db: Session) -> Job | None:
    """Atomically claim the oldest queued job (safe with several workers)."""
    job = db.scalar(
        select(Job).where(Job.status == JobStatus.QUEUED.value).order_by(Job.created_at)
        .limit(1).with_for_update(skip_locked=True)
    )  # fmt: skip
    if job:
        now = datetime.now(UTC)
        job.status, job.started_at, job.heartbeat_at = JobStatus.RUNNING.value, now, now
        job.attempts += 1
        db.commit()
    return job


def finish(db: Session, job_id: uuid.UUID, error: str | None = None) -> None:
    db.execute(
        update(Job).where(Job.id == job_id).values(
            status=JobStatus.FAILED.value if error else JobStatus.SUCCEEDED.value,
            finished_at=datetime.now(UTC), error=error,
        )
    )  # fmt: skip
    db.commit()


def fail_orphaned(db: Session) -> list[Job]:
    """On worker start, anything still 'running' belongs to a dead worker. Returns the failed jobs."""
    orphans = list(db.scalars(select(Job).where(Job.status == JobStatus.RUNNING.value)))
    for job in orphans:
        job.status, job.finished_at = JobStatus.FAILED.value, datetime.now(UTC)
        job.error = "worker stopped while the job was running"
    db.commit()
    return orphans
