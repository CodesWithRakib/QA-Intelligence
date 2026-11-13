"""ORM models. Import from here so Alembic sees the full metadata."""

from app.models.base import Base
from app.models.project import (
    ApiEndpoint,
    ArchitectureSnapshot,
    Component,
    Project,
    ProjectSource,
    ProjectVersion,
    Route,
    User,
)
from app.models.qa import Evidence, Finding, TestCase, TestPlan, TestResult, TestRun
from app.models.repair import RegressionResult, RepairExecution, RepairProposal
from app.models.system import (
    AgentExecution,
    AIConfiguration,
    AIUsage,
    AuditEvent,
    Integration,
    Job,
    ModelEvaluation,
    Report,
    Setting,
)

__all__ = [
    "AIConfiguration",
    "AIUsage",
    "AgentExecution",
    "ApiEndpoint",
    "ArchitectureSnapshot",
    "AuditEvent",
    "Base",
    "Component",
    "Evidence",
    "Finding",
    "Integration",
    "Job",
    "ModelEvaluation",
    "Project",
    "ProjectSource",
    "ProjectVersion",
    "RegressionResult",
    "RepairExecution",
    "RepairProposal",
    "Report",
    "Route",
    "Setting",
    "TestCase",
    "TestPlan",
    "TestResult",
    "TestRun",
    "User",
]
