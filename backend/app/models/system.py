"""AI configuration and usage, agents, jobs, reports, integrations, settings and audit trail."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain import JobStatus
from app.models.base import Base, CreatedMixin, IdMixin, JsonDict, enum_check


class AIConfiguration(Base, IdMixin, CreatedMixin):
    """One row per provider. API keys are never stored here - they stay in the environment."""

    __tablename__ = "ai_configurations"

    provider: Mapped[str] = mapped_column(String(32), unique=True)  # ollama | anthropic | openai
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    default_model: Mapped[str | None] = mapped_column(String(120))
    # Explicit owner approval for paid usage, plus hard limits. 0 means "no spending allowed".
    paid_usage_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    monthly_budget_usd: Mapped[float] = mapped_column(Numeric(10, 4, asdecimal=False), default=0)
    per_project_budget_usd: Mapped[float] = mapped_column(Numeric(10, 4, asdecimal=False), default=0)
    # USD per million tokens, entered by the owner at approval time (prices are never guessed).
    input_price_per_mtok: Mapped[float | None] = mapped_column(Numeric(10, 4, asdecimal=False))
    output_price_per_mtok: Mapped[float | None] = mapped_column(Numeric(10, 4, asdecimal=False))


class AIUsage(Base, IdMixin, CreatedMixin):
    __tablename__ = "ai_usage"

    provider: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(120))
    task_type: Mapped[str] = mapped_column(String(60))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6, asdecimal=False), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)


class ModelEvaluation(Base, IdMixin, CreatedMixin):
    __tablename__ = "model_evaluations"

    batch_id: Mapped[uuid.UUID] = mapped_column(index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120))
    task_key: Mapped[str] = mapped_column(String(80))
    task_type: Mapped[str] = mapped_column(String(60))
    passed: Mapped[bool] = mapped_column(Boolean)
    structured_output_ok: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int] = mapped_column(Integer)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6, asdecimal=False), default=0)
    detail: Mapped[str] = mapped_column(Text, default="")


class AgentExecution(Base, IdMixin, CreatedMixin):
    __tablename__ = "agent_executions"

    agent: Mapped[str] = mapped_column(String(60), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20))  # succeeded | failed | timed_out
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_summary: Mapped[JsonDict] = mapped_column(default=dict)
    output_summary: Mapped[JsonDict] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Job(Base, IdMixin, CreatedMixin):
    """PostgreSQL-backed work queue (SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"
    __table_args__ = (enum_check("status", JobStatus),)

    kind: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[JsonDict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED.value, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Report(Base, IdMixin, CreatedMixin):
    __tablename__ = "reports"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300))
    content_markdown: Mapped[str] = mapped_column(Text)
    data: Mapped[JsonDict] = mapped_column(default=dict)
    path: Mapped[str | None] = mapped_column(String(512))


class Integration(Base, IdMixin, CreatedMixin):
    __tablename__ = "integrations"

    kind: Mapped[str] = mapped_column(String(40))  # github | gitlab | ... (none implemented in the MVP)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[JsonDict] = mapped_column(default=dict)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[JsonDict] = mapped_column(default=dict)


class AuditEvent(Base, IdMixin, CreatedMixin):
    __tablename__ = "audit_events"

    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[JsonDict] = mapped_column(default=dict)
