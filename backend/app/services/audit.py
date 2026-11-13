"""Append-only audit trail."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent

LOCAL_OWNER = "local-owner"
SYSTEM = "system"


def record(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: object | None = None,
    *,
    actor: str = LOCAL_OWNER,
    **details: Any,
) -> None:
    """Add an audit event to the current transaction (the caller commits)."""
    db.add(
        AuditEvent(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=details,
        )
    )
