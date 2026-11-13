"""Users, projects, immutable project versions and the discovered architecture inventory."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain import SourceKind
from app.models.base import Base, CreatedMixin, IdMixin, JsonDict, enum_check


class User(Base, IdMixin, CreatedMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="owner")


class Project(Base, IdMixin, CreatedMixin):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    target_url: Mapped[str | None] = mapped_column(String(2048))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sources: Mapped[list["ProjectSource"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    versions: Mapped[list["ProjectVersion"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectVersion.number"
    )


class ProjectSource(Base, IdMixin, CreatedMixin):
    __tablename__ = "project_sources"
    __table_args__ = (enum_check("kind", SourceKind),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    location: Mapped[str] = mapped_column(String(2048))
    config: Mapped[JsonDict] = mapped_column(default=dict)

    project: Mapped[Project] = relationship(back_populates="sources")


class ProjectVersion(Base, IdMixin, CreatedMixin):
    """An immutable snapshot of imported content. Analysis always refers to a version."""

    __tablename__ = "project_versions"
    __table_args__ = (UniqueConstraint("project_id", "number"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_sources.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    # Path relative to the data directory; None for sources without files (URL targets).
    workspace_path: Mapped[str | None] = mapped_column(String(512))
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)

    project: Mapped[Project] = relationship(back_populates="versions")
    source: Mapped[ProjectSource] = relationship()
    snapshot: Mapped["ArchitectureSnapshot | None"] = relationship(
        back_populates="version", cascade="all, delete-orphan", uselist=False
    )


class ArchitectureSnapshot(Base, IdMixin, CreatedMixin):
    __tablename__ = "architecture_snapshots"

    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"), unique=True)
    # Deterministic facts: languages, frameworks, package managers, scripts, env var names, ...
    inventory: Mapped[JsonDict] = mapped_column(default=dict)
    unsupported: Mapped[list[str]] = mapped_column(default=list)
    # Optional AI narrative. Always labelled as AI-generated, never treated as fact.
    ai_summary: Mapped[str | None] = mapped_column(Text)
    ai_model: Mapped[str | None] = mapped_column(String(120))

    version: Mapped[ProjectVersion] = relationship(back_populates="snapshot")
    components: Mapped[list["Component"]] = relationship(cascade="all, delete-orphan")
    routes: Mapped[list["Route"]] = relationship(cascade="all, delete-orphan")
    api_endpoints: Mapped[list["ApiEndpoint"]] = relationship(cascade="all, delete-orphan")


class Component(Base, IdMixin):
    __tablename__ = "components"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("architecture_snapshots.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))  # frontend | backend | library | service | database | unknown
    path: Mapped[str] = mapped_column(String(512))
    languages: Mapped[list[str]] = mapped_column(default=list)
    frameworks: Mapped[list[str]] = mapped_column(default=list)
    details: Mapped[JsonDict] = mapped_column(default=dict)


class Route(Base, IdMixin):
    __tablename__ = "routes"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("architecture_snapshots.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(512))
    framework: Mapped[str] = mapped_column(String(60))
    source_file: Mapped[str] = mapped_column(String(512))


class ApiEndpoint(Base, IdMixin):
    __tablename__ = "api_endpoints"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("architecture_snapshots.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(512))
    origin: Mapped[str] = mapped_column(String(20))  # openapi | code
    source_file: Mapped[str] = mapped_column(String(512))
    operation_id: Mapped[str | None] = mapped_column(String(200))
    spec: Mapped[JsonDict] = mapped_column(default=dict)
