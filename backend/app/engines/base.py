"""Shared engine contracts."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain import Category, ResultStatus, Severity
from app.services.settings import ExecutionSettings

MAX_INLINE_EVIDENCE = 60_000


@dataclass
class EvidenceDraft:
    kind: str  # screenshot | http_exchange | tool_output | log | diff
    label: str
    content: str | None = None
    file: Path | None = None  # absolute path of an artifact already written under the evidence dir

    def __post_init__(self) -> None:
        if self.content and len(self.content) > MAX_INLINE_EVIDENCE:
            self.content = self.content[:MAX_INLINE_EVIDENCE] + "\n...[truncated]"


@dataclass
class FindingDraft:
    key: str  # stable identity of the defect within the project (used for the fingerprint)
    category: Category
    severity: Severity
    title: str
    description: str
    source_tool: str
    reproduction_steps: list[str] = field(default_factory=list)
    affected_component: str = ""
    confidence: float = 0.8
    remediation: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceDraft] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.category.value}|{self.source_tool}|{self.key}".encode()).hexdigest()


@dataclass
class CaseOutcome:
    status: ResultStatus
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceDraft] = field(default_factory=list)
    findings: list[FindingDraft] = field(default_factory=list)

    @classmethod
    def blocked(cls, reason: str) -> "CaseOutcome":
        """The check could not be executed. Never reported as passed or failed."""
        return cls(ResultStatus.BLOCKED, reason)


@dataclass
class EngineContext:
    workspace: Path | None
    target_url: str | None
    settings: ExecutionSettings
    evidence_dir: Path  # per-run directory for artifacts
    inventory: dict[str, Any]
    allow_mutations: bool = False


def mask_secret(value: str) -> str:
    """Keep evidence useful without persisting the secret itself."""
    value = value.strip()
    return f"{value[:2]}{'*' * max(len(value) - 2, 4)}" if value else ""
