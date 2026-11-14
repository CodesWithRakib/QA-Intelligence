"""Project ingestion: turns a source (folder, ZIP, git, URL, OpenAPI file, APK) into an immutable version."""

import hashlib
import re
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.discovery.scanner import scan_workspace
from app.discovery.store import store_snapshot
from app.domain import SourceKind
from app.engines import android_static
from app.ingestion import git_source
from app.models import Project, ProjectSource, ProjectVersion
from app.security.archive import ImportLimits, ImportSummary, UnsafeContentError, copy_tree, extract_zip
from app.security.ssrf import TargetNotAllowed, validate_target
from app.services import audit
from app.services.settings import ExecutionSettings, get_execution_settings


class IngestionError(Exception):
    """User-correctable import problem (reported as HTTP 422)."""


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100] or "project"
    return f"{base}-{uuid.uuid4().hex[:6]}"


def _limits(settings: ExecutionSettings) -> ImportLimits:
    return ImportLimits(max_total_bytes=settings.max_extracted_mb * 1024 * 1024, max_files=settings.max_files)


def _new_workspace() -> tuple[Path, str]:
    relative = f"workspaces/{uuid.uuid4().hex}"
    path = get_settings().data_dir / relative
    path.mkdir(parents=True)
    return path, relative


def _validate_local_folder(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise IngestionError("Provide an absolute folder path.")
    path = path.resolve()
    if not path.is_dir():
        raise IngestionError(f"Folder not found: {path}")
    if path.parent == path:
        raise IngestionError("Importing a drive root is not allowed.")
    data_dir = get_settings().data_dir.resolve()
    if path.is_relative_to(data_dir) or data_dir.is_relative_to(path):
        raise IngestionError("The folder overlaps the platform's own data directory.")
    return path


def _git_state(folder: Path) -> dict[str, object]:
    """Branch, commit and dirty-file count of a local repository (recorded so results are traceable)."""
    if not (folder / ".git").exists():
        return {}
    try:
        commit = git_source._git(["rev-parse", "HEAD"], cwd=folder).stdout.strip()
        branch = git_source._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=folder).stdout.strip()
        dirty = len(git_source._git(["status", "--porcelain"], cwd=folder).stdout.splitlines())
    except (git_source.GitSourceError, OSError, subprocess.TimeoutExpired):
        return {}
    return {"commit": commit, "branch": branch, "uncommitted_files": dirty,
            "note": "the working tree was imported, including uncommitted changes" if dirty else "clean working tree"}  # fmt: skip


def _apk_metadata(apk: Path) -> dict[str, object]:
    """Package facts for the architecture inventory. Missing tools are reported, not hidden."""
    try:
        return android_static.analyse(apk).summary()
    except android_static.ApkAnalysisError as exc:
        return {"analysis_unavailable": str(exc)}


def _create_version(
    db: Session,
    project: Project,
    source: ProjectSource,
    summary: ImportSummary | None,
    workspace_relative: str | None,
) -> ProjectVersion:
    latest = db.scalar(select(func.max(ProjectVersion.number)).where(ProjectVersion.project_id == project.id))
    version = ProjectVersion(
        project_id=project.id,
        source_id=source.id,
        number=(latest or 0) + 1,
        content_hash=summary.content_hash if summary else hashlib.sha256(source.location.encode()).hexdigest(),
        workspace_path=workspace_relative,
        file_count=summary.file_count if summary else 0,
        size_bytes=summary.size_bytes if summary else 0,
    )
    db.add(version)
    db.flush()
    return version


def import_project(
    db: Session,
    *,
    name: str,
    kind: SourceKind,
    description: str = "",
    local_path: str | None = None,
    upload_path: Path | None = None,
    upload_name: str | None = None,
    url: str | None = None,
    repository: str | None = None,
) -> ProjectVersion:
    """Create a project (with its first version and architecture snapshot) from a source."""
    exec_settings = get_execution_settings(db)
    workspace: Path | None = None
    workspace_relative: str | None = None
    summary: ImportSummary | None = None
    config: dict[str, object] = {}
    git_config: dict[str, object] = {}

    try:
        if kind is SourceKind.LOCAL_FOLDER:
            folder = _validate_local_folder(local_path or "")
            workspace, workspace_relative = _new_workspace()
            summary = copy_tree(folder, workspace, _limits(exec_settings))
            location = str(folder)
        elif kind is SourceKind.ZIP:
            if not upload_path:
                raise IngestionError("A ZIP file is required.")
            workspace, workspace_relative = _new_workspace()
            summary = extract_zip(upload_path, workspace, _limits(exec_settings))
            location = upload_name or "upload.zip"
        elif kind is SourceKind.OPENAPI:
            if not upload_path:
                raise IngestionError("An OpenAPI/Swagger file is required.")
            suffix = Path(upload_name or "openapi.json").suffix.lower()
            if suffix not in {".json", ".yaml", ".yml"}:
                raise IngestionError("OpenAPI files must be .json, .yaml or .yml.")
            workspace, workspace_relative = _new_workspace()
            data = upload_path.read_bytes()
            (workspace / f"openapi{suffix}").write_bytes(data)
            summary = ImportSummary(file_count=1, size_bytes=len(data))
            summary._digests.append((f"openapi{suffix}", hashlib.sha256(data).hexdigest()))
            summary.finalise()
            location = upload_name or f"openapi{suffix}"
        elif kind is SourceKind.URL:
            if not url:
                raise IngestionError("A URL is required.")
            location = url
        elif kind is SourceKind.APK:
            if not upload_path:
                raise IngestionError("An APK file is required.")
            if not zipfile.is_zipfile(upload_path):
                raise IngestionError("The file is not a valid APK (not a ZIP archive).")
            with zipfile.ZipFile(upload_path) as bundle:
                if "AndroidManifest.xml" not in bundle.namelist():
                    raise IngestionError("The archive has no AndroidManifest.xml: it is not an Android application package.")
            workspace, workspace_relative = _new_workspace()
            data = upload_path.read_bytes()
            (workspace / "app.apk").write_bytes(data)
            summary = ImportSummary(file_count=1, size_bytes=len(data))
            summary._digests.append(("app.apk", hashlib.sha256(data).hexdigest()))
            summary.finalise()
            location = upload_name or "app.apk"
        elif kind is SourceKind.GIT:
            clone_dir = get_settings().cache_dir / "clones" / uuid.uuid4().hex
            clone_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                info = git_source.clone(repository or local_path or "", clone_dir, exec_settings.git_allowed_hosts)
                workspace, workspace_relative = _new_workspace()
                summary = copy_tree(clone_dir, workspace, _limits(exec_settings))
            except git_source.GitSourceError as exc:
                raise IngestionError(str(exc)) from exc
            finally:
                shutil.rmtree(clone_dir, ignore_errors=True)
            location = info.location
            git_config = {"commit": info.commit, "branch": info.branch, "remote": info.remote}
        else:
            raise IngestionError(f"Source type '{kind.value}' is not supported yet (see compatibility matrix).")

        if summary:
            config = {
                "excluded_secret_files": summary.excluded_secret_files,
                "skipped": summary.skipped[:200],
                **git_config,
            }
        if kind is SourceKind.LOCAL_FOLDER:
            config |= _git_state(Path(location))

        project = Project(name=name.strip(), slug=_slugify(name), description=description.strip(), target_url=url)
        db.add(project)
        db.flush()
        source = ProjectSource(project_id=project.id, kind=kind.value, location=location, config=config)
        db.add(source)
        db.flush()

        version = _create_version(db, project, source, summary, workspace_relative)
        scan = scan_workspace(workspace) if workspace else None
        if scan and kind is SourceKind.APK:
            scan.inventory["android"]["apk"] = _apk_metadata(workspace / "app.apk") if workspace else {}
        store_snapshot(db, version, scan)
        audit.record(
            db, "project.imported", "project", project.id,
            kind=kind.value, version=version.number, files=version.file_count,
            excluded_secret_files=len(summary.excluded_secret_files) if summary else 0,
        )  # fmt: skip
        db.commit()
        return version
    except UnsafeContentError as exc:
        db.rollback()
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)
        raise IngestionError(str(exc)) from exc
    except Exception:
        db.rollback()
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)
        raise


def check_target_authorised(db: Session, url: str) -> None:
    """Raise IngestionError when a URL is not an authorised test target."""
    try:
        validate_target(url, get_execution_settings(db).allowed_targets)
    except TargetNotAllowed as exc:
        raise IngestionError(str(exc)) from exc
