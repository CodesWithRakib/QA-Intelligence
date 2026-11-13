"""Deterministic QA plan generation from the architecture inventory.

Only cases that an engine can actually execute are planned as automated. Areas the platform cannot
automate yet are added as MANUAL cases, which always report "not tested" rather than a false pass.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.domain import Category, Engine, Origin
from app.engines.api import MUTATING
from app.models import ArchitectureSnapshot, ProjectVersion, TestCase, TestPlan
from app.services import audit

MAX_API_OPERATIONS = 60


@dataclass
class _Builder:
    cases: list[TestCase] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _counters: dict[str, int] = field(default_factory=dict)

    def add(self, prefix: str, title: str, category: Category, engine: Engine, spec: dict[str, Any] | None = None,
            priority: int = 2) -> None:  # fmt: skip
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        self.cases.append(TestCase(
            key=f"{prefix}-{self._counters[prefix]:03d}", title=title[:300], category=category.value,
            engine=engine.value, priority=priority, origin=Origin.DETERMINISTIC.value, spec=spec or {},
        ))  # fmt: skip


def _static_cases(builder: _Builder, inventory: dict[str, Any]) -> None:
    builder.add("SEC-DEP", "Known vulnerabilities in declared dependencies (Trivy)", Category.SECURITY,
                Engine.DEPENDENCY_SCAN, priority=1)  # fmt: skip
    builder.add("SEC-SAST", "Static application security testing (Semgrep, p/default rules)", Category.SECURITY,
                Engine.STATIC_ANALYSIS, {"tool": "semgrep"}, priority=1)  # fmt: skip
    builder.add("SEC-SECRET", "Committed secrets in known token formats (Trivy)", Category.SECURITY,
                Engine.STATIC_ANALYSIS, {"tool": "trivy-secret"}, priority=1)  # fmt: skip
    builder.add("SEC-SECRET", "Hard-coded credential assignments (built-in check)", Category.SECURITY,
                Engine.STATIC_ANALYSIS, {"tool": "builtin-secrets"})  # fmt: skip
    containers = inventory.get("containers") or {}
    if containers.get("dockerfiles") or containers.get("compose_services"):
        builder.add("SEC-CONF", "Container and infrastructure configuration (Trivy misconfig)", Category.SECURITY,
                    Engine.STATIC_ANALYSIS, {"tool": "trivy-config"})  # fmt: skip
    else:
        builder.notes.append("No Dockerfile/compose file detected: infrastructure configuration scan not planned.")
    commands = inventory.get("test_commands") or []
    for command in commands:
        builder.add("FUNC-TEST", f"Project test suite in sandbox: `{command['command']}` in {command['component'] or '.'}",
                    Category.FUNCTIONAL, Engine.PROJECT_TESTS, command, priority=1)  # fmt: skip
    if not commands:
        builder.notes.append("No runnable test command was derived from the manifests: the project's own tests are not executed.")
    if inventory.get("data_models"):
        builder.add(
            "DB-STATIC",
            "Data models have versioned schema migrations",
            Category.DATABASE,
            Engine.DATABASE_STATIC,
        )
    else:
        builder.notes.append("No ORM models detected: database checks not planned.")


def _api_cases(builder: _Builder, snapshot: ArchitectureSnapshot) -> None:
    operations = [e for e in snapshot.api_endpoints if e.origin == "openapi"]
    if not operations:
        code_only = sum(1 for e in snapshot.api_endpoints if e.origin == "code")
        builder.notes.append(
            f"No OpenAPI specification found ({code_only} endpoint(s) were discovered in code): contract tests "
            "need a spec, so only generic API checks are planned."
        )
    if len(operations) > MAX_API_OPERATIONS:
        builder.notes.append(f"Spec has {len(operations)} operations; only the first {MAX_API_OPERATIONS} are planned.")
    first_get: str | None = None
    for endpoint in operations[:MAX_API_OPERATIONS]:
        base = {"spec_file": endpoint.source_file, "method": endpoint.method, "path": endpoint.path}
        builder.add("API-CONTRACT", f"{endpoint.method} {endpoint.path}: documented status and response schema",
                    Category.API, Engine.API, {"check": "operation", **base}, priority=1)  # fmt: skip
        has_path_param = "{" in endpoint.path
        if endpoint.method == "GET" and has_path_param and "404" in endpoint.spec.get("responses", []):
            builder.add("API-NEG", f"GET {endpoint.path}: unknown resource returns 404", Category.API, Engine.API,
                        {"check": "negative_not_found", **base})  # fmt: skip
        if endpoint.method in MUTATING and endpoint.spec.get("has_request_body") and not has_path_param:
            builder.add("API-NEG", f"{endpoint.method} {endpoint.path}: invalid body is rejected with 4xx",
                        Category.API, Engine.API, {"check": "negative_invalid_body", **base})  # fmt: skip
        if endpoint.method == "GET" and not has_path_param and first_get is None:
            first_get = endpoint.path
        if endpoint.spec.get("secured"):
            builder.notes.append(
                f"{endpoint.method} {endpoint.path} requires authentication: authenticated testing is not implemented."
            )
    builder.add("API-NEG", "Unknown route returns a clean 404", Category.API, Engine.API, {"check": "unknown_route"})
    builder.add("SEC-HDR", "Recommended HTTP security headers are present", Category.SECURITY, Engine.API,
                {"check": "security_headers"})  # fmt: skip
    if first_get:
        builder.add("PERF-API", f"Latency sample for GET {first_get}", Category.PERFORMANCE, Engine.API,
                    {"check": "latency", "path": first_get}, priority=3)  # fmt: skip


def _browser_cases(builder: _Builder) -> None:
    builder.add("BRW", "Home page loads without console or network errors", Category.BROWSER, Engine.BROWSER,
                {"check": "page_health", "path": "/"}, priority=1)  # fmt: skip
    builder.add("BRW", "Same-origin links resolve (bounded crawl)", Category.BROWSER, Engine.BROWSER,
                {"check": "crawl", "path": "/"})  # fmt: skip
    builder.add("BRW", "No horizontal overflow on mobile and tablet viewports", Category.BROWSER, Engine.BROWSER,
                {"check": "responsive", "path": "/"})  # fmt: skip
    builder.add("A11Y", "Automated accessibility rules (axe-core) on home and linked pages", Category.ACCESSIBILITY,
                Engine.BROWSER, {"check": "accessibility", "path": "/"}, priority=1)  # fmt: skip
    builder.add("PERF-WEB", "Page load timing and Largest Contentful Paint", Category.PERFORMANCE, Engine.BROWSER,
                {"check": "web_performance", "path": "/"}, priority=3)  # fmt: skip


def _manual_cases(builder: _Builder, has_target: bool) -> None:
    manual = [
        ("MAN-AUTH", "Authentication and session handling review", Category.SECURITY),
        ("MAN-AUTH", "Authorization: roles and access-denied scenarios", Category.SECURITY),
        ("MAN-FUNC", "Critical user journeys (multi-step, CRUD, forms)", Category.FUNCTIONAL),
    ]
    if has_target:
        manual += [
            ("MAN-A11Y", "Keyboard-only navigation and screen-reader review", Category.ACCESSIBILITY),
            (
                "MAN-UX",
                "Empty, loading and error states; onboarding and navigation clarity",
                Category.PRODUCT,
            ),
        ]
    for prefix, title, category in manual:
        builder.add(prefix, title, category, Engine.MANUAL,
                    {"reason": "No automated engine for this area in the MVP; requires manual review."}, priority=2)  # fmt: skip


def generate_plan(db: Session, version: ProjectVersion, *, target_url: str | None, name: str | None = None,
                  allow_mutations: bool = False) -> TestPlan:  # fmt: skip
    snapshot = version.snapshot
    if snapshot is None:
        raise ValueError("version has no architecture snapshot")
    builder = _Builder()
    if version.workspace_path:
        _static_cases(builder, snapshot.inventory)
    else:
        builder.notes.append("No source files: static analysis, dependency scanning and project tests are not applicable.")
    if target_url:
        _browser_cases(builder)
        _api_cases(builder, snapshot)
    else:
        builder.notes.append("No target URL: browser, API, accessibility and performance checks are not planned.")
    _manual_cases(builder, bool(target_url))
    builder.notes += [f"Unsupported: {item}" for item in snapshot.unsupported]
    if allow_mutations:
        builder.notes.append("State-changing API requests are ENABLED for this plan (owner opted in).")

    plan = TestPlan(
        project_id=version.project_id, version_id=version.id, target_url=target_url,
        name=name or f"QA plan for version {version.number}", notes=builder.notes,
        allow_mutations=allow_mutations,
    )  # fmt: skip
    plan.cases = builder.cases
    db.add(plan)
    db.flush()
    audit.record(db, "plan.generated", "test_plan", plan.id, cases=len(builder.cases),
                 target_url=target_url, allow_mutations=allow_mutations)  # fmt: skip
    return plan
