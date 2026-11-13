"""Job worker (the orchestrator). Run with: python -m app.worker

Single process, one job at a time: predictable resource use on a small machine. The workflow for
each job kind is fixed in code; no agent can enqueue or invoke another agent.
"""

import logging
import signal
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.ai import benchmark, tasks
from app.ai.providers import ProviderError
from app.ai.service import AIUnavailable
from app.config import get_settings
from app.db import SessionLocal
from app.jobs import queue
from app.logging_setup import configure_logging
from app.models import Setting
from app.repair import service as repair
from app.runs import service as runs

log = logging.getLogger("app.worker")
POLL_SECONDS = 1.5
HEARTBEAT_SECONDS = 10
HEARTBEAT_KEY = "worker_heartbeat"
_stop = False


def _with_db(fn: Callable[..., Any], key: str) -> Callable[[dict[str, Any], uuid.UUID], None]:
    def handler(payload: dict[str, Any], job_id: uuid.UUID) -> None:
        with SessionLocal() as db:
            fn(db, uuid.UUID(payload[key]), job_id)

    return handler


def _benchmark(payload: dict[str, Any], _job_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        benchmark.run_benchmark(db, payload["provider"], payload["model"])


HANDLERS: dict[str, Callable[[dict[str, Any], uuid.UUID], None]] = {
    "test_run": lambda p, job_id: runs.execute_run(uuid.UUID(p["run_id"]), job_id),
    "repair_proposal": lambda p, job_id: repair.generate_proposal(uuid.UUID(p["finding_id"]), job_id),
    "repair_execution": lambda p, job_id: repair.execute(uuid.UUID(p["execution_id"]), job_id),
    "ai_benchmark": _benchmark,
    "ai_explain_finding": _with_db(tasks.explain_finding, "finding_id"),
    "ai_architecture_summary": _with_db(tasks.summarise_architecture, "version_id"),
    "ai_suggest_cases": _with_db(tasks.suggest_test_cases, "plan_id"),
}


def _recover() -> None:
    with SessionLocal() as db:
        for job in queue.fail_orphaned(db):
            log.warning("failed orphaned job", extra={"job_id": str(job.id), "kind": job.kind})
            runs.fail_run_for_orphaned_job(db, job.payload)
            repair.fail_execution_for_orphaned_job(db, job.payload)


def _heartbeat() -> None:
    with SessionLocal() as db:
        value = {"at": datetime.now(UTC).isoformat()}
        row = db.get(Setting, HEARTBEAT_KEY)
        if row:
            row.value = value
        else:
            db.add(Setting(key=HEARTBEAT_KEY, value=value))
        db.commit()


def _heartbeat_loop() -> None:
    while not _stop:
        try:
            _heartbeat()
        except Exception:  # noqa: BLE001 - a missed beat must not stop the worker
            log.exception("heartbeat failed")
        time.sleep(HEARTBEAT_SECONDS)


def run_one() -> bool:
    """Process at most one job. Returns False when the queue is empty."""
    with SessionLocal() as db:
        job = queue.claim_next(db)
        if job is None:
            return False
        job_id, kind, payload = job.id, job.kind, dict(job.payload)
    log.info("job started", extra={"job_id": str(job_id), "kind": kind})
    error: str | None = None
    try:
        handler = HANDLERS.get(kind)
        if handler is None:
            raise ValueError(f"unknown job kind '{kind}'")
        handler(payload, job_id)
    except (AIUnavailable, ProviderError) as exc:
        error = f"AI unavailable: {exc}"[:2000]
        log.warning("job failed", extra={"job_id": str(job_id), "kind": kind, "error": error})
    except Exception as exc:  # noqa: BLE001 - a failing job must not kill the worker
        error = f"{type(exc).__name__}: {exc}"[:2000]
        log.exception("job crashed", extra={"job_id": str(job_id), "kind": kind})
    with SessionLocal() as db:
        queue.finish(db, job_id, error)
    log.info("job finished", extra={"job_id": str(job_id), "kind": kind, "ok": error is None})
    return True


def main() -> None:
    configure_logging(get_settings().log_level)

    def stop(*_: object) -> None:
        global _stop  # noqa: PLW0603
        _stop = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    _recover()
    log.info("worker ready")
    # Heartbeat runs on its own thread so long jobs do not make the worker look dead.
    threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat").start()
    while not _stop:
        if not run_one():
            time.sleep(POLL_SECONDS)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
