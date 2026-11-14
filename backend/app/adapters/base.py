"""The adapter contract.

Every testing discipline implements the same six operations, so the orchestrator (planner and run
service) never needs to know what a particular tool does:

    capabilities  -> what this adapter can do for THIS project (empty = not applicable)
    environment   -> are the tools it needs installed and running
    plan          -> concrete test cases derived from the project's real inventory
    readiness     -> can one specific case be executed right now, and if not, exactly why
    execute       -> run the case and return a normalised outcome with evidence
    cleanup       -> release whatever the run acquired (volumes, devices, servers)

Adding a discipline = adding one Adapter subclass and registering it in registry.py.
"""

import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.domain import Category, Engine, Readiness
from app.engines import sandbox
from app.engines.base import CaseOutcome, EngineContext
from app.models import ArchitectureSnapshot
from app.security.ssrf import TargetNotAllowed, validate_target
from app.services.settings import ExecutionSettings


@dataclass
class ReadinessReport:
    status: Readiness = Readiness.READY
    reasons: list[str] = field(default_factory=list)  # precise, user-facing explanation when not ready
    requirements: list[str] = field(default_factory=list)  # resources the case will use

    @property
    def runnable(self) -> bool:
        return self.status in (Readiness.READY, Readiness.PARTIALLY_READY)

    def block(self, status: Readiness, reason: str) -> "ReadinessReport":
        """Record a problem. The first blocking status wins; every reason is kept."""
        if self.runnable:
            self.status = status
        self.reasons.append(reason)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "reasons": self.reasons, "requirements": self.requirements}


@dataclass
class PlanContext:
    """Facts about the project that planning may use. Built from stored data only."""

    snapshot: ArchitectureSnapshot
    workspace: Path | None
    target_url: str | None
    settings: ExecutionSettings
    source_kind: str
    has_git_history: bool = False
    allow_mutations: bool = False
    project_config: dict[str, Any] = field(default_factory=dict)
    # The platform starts an isolated local copy of the project for the run.
    managed_environment: bool = False

    @property
    def has_target(self) -> bool:
        return bool(self.target_url) or self.managed_environment

    @property
    def target_label(self) -> str:
        return "managed local environment" if self.managed_environment else str(self.target_url)

    @property
    def inventory(self) -> dict[str, Any]:
        return self.snapshot.inventory

    @property
    def is_apk(self) -> bool:
        """An uploaded APK has no source tree: source scanners, builds and web checks do not apply."""
        return self.source_kind == "apk"

    @property
    def frameworks(self) -> set[str]:
        return set(self.inventory.get("frameworks") or [])


@dataclass
class PlannedCase:
    prefix: str
    title: str
    category: Category
    engine: Engine
    spec: dict[str, Any] = field(default_factory=dict)
    priority: int = 2


@dataclass
class PlanResult:
    cases: list[PlannedCase] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Adapter:
    """Base class with safe defaults. Subclasses override what applies to them."""

    name: str = ""
    title: str = ""
    agent: str = ""  # agent registry entry that executions are recorded under
    needs_workspace: bool = False
    needs_target: bool = False
    docker_images: tuple[str, ...] = ()

    def capabilities(self, plan: PlanContext) -> list[str]:
        raise NotImplementedError

    def environment(self, settings: ExecutionSettings) -> ReadinessReport:
        report = ReadinessReport()
        if self.docker_images:
            if not sandbox.docker_available():
                return report.block(Readiness.BLOCKED, "Docker is not running")
            for image in self.docker_images:
                if not sandbox.image_present(image):
                    report.block(Readiness.BLOCKED, f"container image {image} is not pulled")
        return report

    def plan(self, plan: PlanContext) -> PlanResult:
        raise NotImplementedError

    def readiness(self, ctx: EngineContext, spec: dict[str, Any]) -> ReadinessReport:
        report = self.environment(ctx.settings)
        if self.needs_workspace and ctx.workspace is None:
            report.block(Readiness.REQUIRES_SOURCE, "this check needs the project's source files; only a URL was imported")
        if self.needs_target:
            target_readiness(ctx, report)
        return report

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        raise NotImplementedError

    def cleanup(self, ctx: EngineContext) -> dict[str, Any]:
        return {}


def target_readiness(ctx: EngineContext, report: ReadinessReport) -> ReadinessReport:
    """Shared rule for anything that talks to a running application."""
    if not ctx.target_url:
        failure = ctx.run_cache.get("managed_environment_error")
        reason = (
            f"the managed test environment did not start: {failure}"
            if failure
            else "no running target URL is configured for this plan"
        )
        return report.block(Readiness.REQUIRES_ENVIRONMENT, reason)
    try:
        validate_target(ctx.target_url, ctx.settings.allowed_targets)
    except TargetNotAllowed as exc:
        return report.block(Readiness.REQUIRES_CONFIGURATION, f"target is not authorised: {exc}")
    reachable = ctx.run_cache.get("target_reachable")
    if reachable is None:
        parts = urlsplit(ctx.target_url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            with socket.create_connection((parts.hostname or "", port), timeout=4):
                reachable = True
        except OSError as exc:
            reachable = f"{type(exc).__name__}: {exc}"
        ctx.run_cache["target_reachable"] = reachable
    if reachable is not True:
        report.block(Readiness.REQUIRES_ENVIRONMENT,
                     f"the target {parts_label(ctx.target_url)} is not reachable ({reachable}); start the application first")  # fmt: skip
    return report


def parts_label(url: str) -> str:
    parts = urlsplit(url)
    return parts.netloc or url
