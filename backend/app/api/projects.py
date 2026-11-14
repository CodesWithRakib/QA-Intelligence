"""Projects, ingestion and architecture inventory."""

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api import serializers
from app.api.deps import Db, PagingDep, get_or_404
from app.config import get_settings
from app.domain import SourceKind
from app.engines.environment import EnvironmentProfile
from app.ingestion.service import check_target_authorised, import_project
from app.jobs import queue
from app.models import Finding, Project, ProjectVersion, TestRun, VisualBaseline
from app.services import audit
from app.services.settings import get_execution_settings

router = APIRouter(prefix="/projects", tags=["projects"])
_CHUNK = 1024 * 1024


def _latest_version(db: Db, project_id: uuid.UUID) -> ProjectVersion:
    version = db.scalar(
        select(ProjectVersion).where(ProjectVersion.project_id == project_id).order_by(ProjectVersion.number.desc())
    )
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project has no version")
    return version


@router.get("")
def list_projects(db: Db, paging: PagingDep) -> dict[str, Any]:
    finding_counts = dict(db.execute(
        select(Finding.project_id, func.count()).where(Finding.status == "open").group_by(Finding.project_id)
    ).all())  # fmt: skip
    run_counts = dict(db.execute(select(TestRun.project_id, func.count()).group_by(TestRun.project_id)).all())

    def row(p: Project) -> dict[str, Any]:
        return serializers.project(p) | {
            "open_findings": finding_counts.get(p.id, 0),
            "runs": run_counts.get(p.id, 0),
        }

    query = (select(Project).where(Project.archived_at.is_(None)).order_by(Project.created_at.desc())
             .options(selectinload(Project.sources), selectinload(Project.versions)))  # fmt: skip
    return paging.page(db, query, row)


async def _save_upload(upload: UploadFile, max_bytes: int) -> Path:
    target = get_settings().cache_dir / "uploads" / uuid.uuid4().hex
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with target.open("wb") as sink:
            while chunk := await upload.read(_CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                        f"upload exceeds the {max_bytes // (1024 * 1024)} MB limit")  # fmt: skip
                sink.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_new_project(
    db: Db,
    name: Annotated[str, Form(min_length=1, max_length=120)],
    kind: Annotated[SourceKind, Form()],
    description: Annotated[str, Form(max_length=2000)] = "",
    local_path: Annotated[str | None, Form(max_length=1024)] = None,
    target_url: Annotated[str | None, Form(max_length=2048)] = None,
    repository: Annotated[str | None, Form(max_length=1024)] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    target_url = (target_url or "").strip() or None
    if kind is SourceKind.URL and not target_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "target_url is required for URL projects")
    if target_url:
        check_target_authorised(db, target_url)
    upload_path: Path | None = None
    try:
        if kind in (SourceKind.ZIP, SourceKind.OPENAPI, SourceKind.APK):
            if file is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "a file upload is required for this source type")
            upload_path = await _save_upload(file, get_execution_settings(db).max_upload_mb * 1024 * 1024)
        version = import_project(db, name=name, kind=kind, description=description, local_path=local_path, repository=repository,
                                 upload_path=upload_path, upload_name=file.filename if file else None, url=target_url)  # fmt: skip
    finally:
        if upload_path:
            upload_path.unlink(missing_ok=True)
    return serializers.project(get_or_404(db, Project, version.project_id))


@router.get("/{project_id}")
def get_project(project_id: uuid.UUID, db: Db) -> dict[str, Any]:
    return serializers.project(get_or_404(db, Project, project_id))


@router.get("/{project_id}/architecture")
def get_architecture(project_id: uuid.UUID, db: Db) -> dict[str, Any]:
    get_or_404(db, Project, project_id)
    version = _latest_version(db, project_id)
    snapshot = version.snapshot
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no architecture snapshot for this version")
    return {
        "version": serializers.version(version), "inventory": snapshot.inventory, "unsupported": snapshot.unsupported,
        "ai_summary": snapshot.ai_summary, "ai_model": snapshot.ai_model,
        "components": [{"name": c.name, "kind": c.kind, "path": c.path, "languages": c.languages,
                        "frameworks": c.frameworks, "details": c.details} for c in snapshot.components],
        "routes": [{"path": r.path, "framework": r.framework, "source_file": r.source_file} for r in snapshot.routes],
        "api_endpoints": [{"method": e.method, "path": e.path, "origin": e.origin, "source_file": e.source_file,
                           "operation_id": e.operation_id, "spec": e.spec} for e in snapshot.api_endpoints],
    }  # fmt: skip


@router.post("/{project_id}/architecture/summary", status_code=status.HTTP_202_ACCEPTED)
def request_ai_summary(project_id: uuid.UUID, db: Db) -> dict[str, Any]:
    get_or_404(db, Project, project_id)
    job = queue.enqueue(db, "ai_architecture_summary", version_id=_latest_version(db, project_id).id)
    audit.record(db, "ai.summary_requested", "project", project_id)
    db.commit()
    return {"job_id": job.id}


@router.get("/{project_id}/baselines")
def list_baselines(project_id: uuid.UUID, db: Db) -> dict[str, Any]:
    get_or_404(db, Project, project_id)
    rows = db.scalars(select(VisualBaseline).where(VisualBaseline.project_id == project_id).order_by(VisualBaseline.key)).all()
    return {
        "items": [
            {"id": b.id, "key": b.key, "width": b.width, "height": b.height, "sha256": b.sha256, "created_at": b.created_at}
            for b in rows
        ]
    }


@router.post("/{project_id}/baselines/reset")
def reset_baselines(project_id: uuid.UUID, db: Db) -> dict[str, Any]:
    """Accept the current design: stored baselines are dropped and the next run captures new ones."""
    get_or_404(db, Project, project_id)
    rows = db.scalars(select(VisualBaseline).where(VisualBaseline.project_id == project_id)).all()
    for row in rows:
        db.delete(row)
    audit.record(db, "visual.baselines_reset", "project", project_id, count=len(rows))
    db.commit()
    return {"removed": len(rows)}


@router.get("/{project_id}/environment")
def get_environment_profile(project_id: uuid.UUID, db: Db) -> dict[str, Any]:
    return {"profile": (get_or_404(db, Project, project_id).test_config or {}).get("environment")}


@router.put("/{project_id}/environment")
def put_environment_profile(project_id: uuid.UUID, body: EnvironmentProfile, db: Db) -> dict[str, Any]:
    """Store the non-secret recipe for running an isolated local copy of the project."""
    project = get_or_404(db, Project, project_id)
    project.test_config = {**(project.test_config or {}), "environment": body.model_dump()}
    audit.record(db, "project.environment_profile_saved", "project", project.id, apps=[a.name for a in body.apps],
                 services=[s.image for s in body.services])  # fmt: skip
    db.commit()
    return {"profile": project.test_config["environment"]}
