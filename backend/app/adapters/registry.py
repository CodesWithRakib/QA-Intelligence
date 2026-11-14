"""Adapter registry. The planner and the run service only ever talk to this module."""

from typing import Any

from app.adapters.android import (
    AndroidAccessibilityAdapter,
    AndroidDetectionAdapter,
    AndroidDynamicAdapter,
    AndroidPerformanceAdapter,
    ApkStaticAdapter,
)
from app.adapters.base import Adapter
from app.adapters.web import (
    AccessibilityAdapter,
    ApiAdapter,
    DatabaseAdapter,
    EnvironmentAdapter,
    ProjectPipelineAdapter,
    VisualAdapter,
    WebDetectionAdapter,
    WebFunctionalAdapter,
    WebPerformanceAdapter,
    WebSecurityAdapter,
)
from app.domain import Engine

# Order = planning order and, within one priority, execution order (cheap static work first).
ADAPTERS: list[Adapter] = [
    WebDetectionAdapter(),
    AndroidDetectionAdapter(),
    WebSecurityAdapter(),
    ProjectPipelineAdapter(),
    DatabaseAdapter(),
    ApkStaticAdapter(),
    EnvironmentAdapter(),
    WebFunctionalAdapter(),
    ApiAdapter(),
    VisualAdapter(),
    AccessibilityAdapter(),
    WebPerformanceAdapter(),
    AndroidDynamicAdapter(),
    AndroidAccessibilityAdapter(),
    AndroidPerformanceAdapter(),
]
_BY_NAME = {adapter.name: adapter for adapter in ADAPTERS}
# Plans created before adapters existed carry no adapter name: route them by engine.
_LEGACY = {
    Engine.BROWSER.value: "web_functional", Engine.API.value: "api", Engine.DEPENDENCY_SCAN.value: "web_security",
    Engine.STATIC_ANALYSIS.value: "web_security", Engine.PROJECT_TESTS.value: "project_pipeline",
    Engine.DATABASE_STATIC.value: "database",
}  # fmt: skip


def adapter_for(engine: str, spec: dict[str, Any]) -> Adapter | None:
    return _BY_NAME.get(str(spec.get("adapter") or _LEGACY.get(engine, "")))


def describe() -> list[dict[str, Any]]:
    return [{"name": a.name, "title": a.title, "agent": a.agent, "needs_target": a.needs_target,
             "needs_source": a.needs_workspace} for a in ADAPTERS]  # fmt: skip
