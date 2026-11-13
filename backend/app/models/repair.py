"""Repair proposals, isolated executions and their regression evidence."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain import ExecutionStatus, ProposalStatus, ResultStatus
from app.models.base import Base, CreatedMixin, IdMixin, JsonDict, enum_check


class RepairProposal(Base, IdMixin, CreatedMixin):
    __tablename__ = "repair_proposals"
    __table_args__ = (enum_check("status", ProposalStatus),)

    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(40))  # dependency_bump | ai_patch
    generated_by: Mapped[str] = mapped_column(String(120))  # "deterministic" or provider:model
    status: Mapped[str] = mapped_column(String(20), default=ProposalStatus.PROPOSED.value, index=True)
    rationale: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text)
    # [{"path": ..., "new_content": ...}] - full replacement content for each touched file.
    changes: Mapped[list[JsonDict]] = mapped_column(JSONB, default=list)
    affected_files: Mapped[list[str]] = mapped_column(default=list)
    # True when the patch touches auth/payment/crypto-like paths: needs an explicit extra confirmation.
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)

    finding = relationship("Finding")
    executions: Mapped[list["RepairExecution"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan", order_by="RepairExecution.created_at"
    )


class RepairExecution(Base, IdMixin, CreatedMixin):
    __tablename__ = "repair_executions"
    __table_args__ = (enum_check("status", ExecutionStatus),)

    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repair_proposals.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=ExecutionStatus.QUEUED.value)
    # Isolated copy of the project version (relative to the data directory) and its git branch.
    workspace_path: Mapped[str | None] = mapped_column(String(512))
    branch: Mapped[str | None] = mapped_column(String(120))
    review: Mapped[JsonDict] = mapped_column(default=dict)
    outcome: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    proposal: Mapped[RepairProposal] = relationship(back_populates="executions")
    regression_results: Mapped[list["RegressionResult"]] = relationship(
        cascade="all, delete-orphan", order_by="RegressionResult.created_at"
    )


class RegressionResult(Base, IdMixin, CreatedMixin):
    """One check (targeted re-scan, project tests, ...) measured before or after the patch."""

    __tablename__ = "regression_results"
    __table_args__ = (enum_check("status", ResultStatus),)

    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repair_executions.id", ondelete="CASCADE"), index=True)
    phase: Mapped[str] = mapped_column(String(10))  # before | after
    check_name: Mapped[str] = mapped_column(String(60))  # targeted | project_tests
    status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[JsonDict] = mapped_column(default=dict)
