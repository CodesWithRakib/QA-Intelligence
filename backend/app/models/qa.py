"""Test plans, runs, results, findings and evidence."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain import (
    Category,
    Classification,
    Engine,
    FindingStatus,
    Origin,
    ResultStatus,
    RunStatus,
    Severity,
    VerificationStatus,
)
from app.models.base import Base, CreatedMixin, IdMixin, JsonDict, enum_check


class TestPlan(Base, IdMixin, CreatedMixin):
    __tablename__ = "test_plans"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    target_url: Mapped[str | None] = mapped_column(String(2048))
    notes: Mapped[list[str]] = mapped_column(default=list)
    # Owner opt-in: allow POST/PUT/PATCH/DELETE contract calls against the target.
    allow_mutations: Mapped[bool] = mapped_column(Boolean, default=False)

    cases: Mapped[list["TestCase"]] = relationship(back_populates="plan", cascade="all, delete-orphan", order_by="TestCase.key")


class TestCase(Base, IdMixin):
    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("plan_id", "key"),
        enum_check("category", Category),
        enum_check("engine", Engine),
        enum_check("origin", Origin),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_plans.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(32))
    engine: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 = highest
    origin: Mapped[str] = mapped_column(String(20), default=Origin.DETERMINISTIC.value)
    spec: Mapped[JsonDict] = mapped_column(default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    plan: Mapped[TestPlan] = relationship(back_populates="cases")


class TestRun(Base, IdMixin, CreatedMixin):
    __tablename__ = "test_runs"
    __table_args__ = (enum_check("status", RunStatus),)

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_plans.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED.value)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Owner asked to stop: the worker finishes the current case and marks the rest as skipped.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # What the worker is doing right now (case key and adapter); real progress, not an estimate.
    progress: Mapped[JsonDict] = mapped_column(default=dict, server_default="{}")
    # Readiness per case, computed immediately before execution.
    readiness: Mapped[JsonDict] = mapped_column(default=dict, server_default="{}")
    # Counts per ResultStatus, computed from stored results only.
    summary: Mapped[JsonDict] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    plan: Mapped[TestPlan] = relationship()
    results: Mapped[list["TestResult"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class TestResult(Base, IdMixin, CreatedMixin):
    __tablename__ = "test_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id"), enum_check("status", ResultStatus))

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[JsonDict] = mapped_column(default=dict)

    run: Mapped[TestRun] = relationship(back_populates="results")
    case: Mapped[TestCase] = relationship()
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="result")


class Finding(Base, IdMixin, CreatedMixin):
    __tablename__ = "findings"
    __table_args__ = (
        # One finding per distinct defect per project; re-detection updates last_seen_at.
        UniqueConstraint("project_id", "fingerprint"),
        enum_check("category", Category),
        enum_check("severity", Severity),
        enum_check("status", FindingStatus),
        enum_check("verification_status", VerificationStatus),
        enum_check("classification", Classification),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"))
    result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_results.id", ondelete="SET NULL"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str] = mapped_column(Text)
    reproduction_steps: Mapped[list[str]] = mapped_column(default=list)
    affected_component: Mapped[str] = mapped_column(String(512), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(20), default=FindingStatus.OPEN.value, index=True)
    remediation: Mapped[str] = mapped_column(Text, default="")
    verification_status: Mapped[str] = mapped_column(String(24), default=VerificationStatus.UNVERIFIED.value)
    source_tool: Mapped[str] = mapped_column(String(60))
    classification: Mapped[str] = mapped_column(
        String(24), default=Classification.POTENTIAL.value, server_default=Classification.POTENTIAL.value
    )
    expected_behavior: Mapped[str] = mapped_column(Text, default="", server_default="")
    actual_behavior: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Machine-readable facts used by repair strategies (package, versions, file, line, rule id...).
    facts: Mapped[JsonDict] = mapped_column(default=dict)
    # Optional AI explanation, shown separately from verified facts.
    ai_explanation: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    evidence: Mapped[list["Evidence"]] = relationship(back_populates="finding")


class Evidence(Base, IdMixin, CreatedMixin):
    __tablename__ = "evidence"

    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_results.id", ondelete="CASCADE"))
    finding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # screenshot | http_exchange | tool_output | log | diff
    label: Mapped[str] = mapped_column(String(300))
    # Either an on-disk artifact (relative to the data directory) or inline text content.
    path: Mapped[str | None] = mapped_column(String(512))
    content: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))

    result: Mapped[TestResult | None] = relationship(back_populates="evidence")
    finding: Mapped[Finding | None] = relationship(back_populates="evidence")


class VisualBaseline(Base, IdMixin, CreatedMixin):
    """Approved reference screenshot for one route / viewport / browser of a project."""

    __tablename__ = "visual_baselines"
    __table_args__ = (UniqueConstraint("project_id", "key"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(300))  # "<browser>|<viewport>|<path>"
    path: Mapped[str] = mapped_column(String(512))  # relative to the data directory
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"))
