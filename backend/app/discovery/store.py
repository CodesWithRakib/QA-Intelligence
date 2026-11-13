"""Persists a scan result as an architecture snapshot."""

from sqlalchemy.orm import Session

from app.discovery.types import ScanResult
from app.models import ApiEndpoint, ArchitectureSnapshot, Component, ProjectVersion, Route


def store_snapshot(db: Session, version: ProjectVersion, scan: ScanResult | None) -> ArchitectureSnapshot:
    """``scan`` is None for sources without files (a running URL): nothing is known statically."""
    if scan is None:
        snapshot = ArchitectureSnapshot(
            version_id=version.id,
            inventory={"file_count": 0, "analysis_notes": ["URL target: no source code was provided."]},
            unsupported=["Static analysis is not applicable to a URL-only project"],
        )
        db.add(snapshot)
        db.flush()
        return snapshot

    snapshot = ArchitectureSnapshot(version_id=version.id, inventory=scan.inventory, unsupported=scan.unsupported)
    snapshot.components = [
        Component(name=c.name[:200], kind=c.kind, path=c.path, languages=c.languages,
                  frameworks=c.frameworks, details=c.details)
        for c in scan.components
    ]  # fmt: skip
    snapshot.routes = [Route(path=r.path, framework=r.framework, source_file=r.source_file) for r in scan.routes]
    snapshot.api_endpoints = [
        ApiEndpoint(method=e.method, path=e.path[:512], origin=e.origin, source_file=e.source_file,
                    operation_id=e.operation_id, spec=e.spec)
        for e in scan.endpoints
    ]  # fmt: skip
    db.add(snapshot)
    db.flush()
    return snapshot
