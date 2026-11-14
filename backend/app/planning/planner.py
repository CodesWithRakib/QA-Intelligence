"""Test plan generation.

The planner asks every adapter what it can do for this specific project and collects the cases it
proposes. Nothing generic is copied between projects: cases come from the project's own inventory
(routes, OpenAPI operations, package scripts, APK). Areas without automation become MANUAL cases,
which always report "not tested".
"""

from pathlib import Path

from sqlalchemy.orm import Session

from app.adapters.base import PlanContext, PlannedCase
from app.adapters.registry import ADAPTERS
from app.config import get_settings
from app.domain import Category, Engine, Origin, SourceKind
from app.models import ProjectVersion, TestCase, TestPlan
from app.services import audit
from app.services.settings import get_execution_settings


def plan_context(db: Session, version: ProjectVersion, *, target_url: str | None, allow_mutations: bool = False) -> PlanContext:
    snapshot = version.snapshot
    if snapshot is None:
        raise ValueError("version has no architecture snapshot")
    workspace = (get_settings().data_dir / version.workspace_path) if version.workspace_path else None
    source = version.source
    has_git = source.kind == SourceKind.LOCAL_FOLDER.value and (Path(source.location) / ".git").exists()
    return PlanContext(
        snapshot=snapshot, workspace=workspace, target_url=target_url, settings=get_execution_settings(db),
        source_kind=source.kind, has_git_history=has_git, allow_mutations=allow_mutations,
        project_config=dict(version.project.test_config or {}),
    )  # fmt: skip


def _manual_cases(context: PlanContext) -> list[PlannedCase]:
    reason = {"reason": "No automated engine for this area yet; requires manual review."}
    credentials = {"reason": "Requires dedicated test accounts, which are not configured; not automated."}
    manual = []
    if context.workspace is not None or context.target_url:
        manual += [
            PlannedCase("MAN-AUTH", "Authentication, session expiry and logout", Category.SECURITY, Engine.MANUAL, credentials),
            PlannedCase("MAN-AUTH", "Authorization: roles, protected routes and access-denied scenarios", Category.SECURITY,
                        Engine.MANUAL, credentials),
            PlannedCase("MAN-FUNC", "End-to-end user journeys (registration, CRUD, search/filter, uploads, payments in sandbox)",
                        Category.FUNCTIONAL, Engine.MANUAL, credentials),
        ]  # fmt: skip
    if context.target_url:
        manual += [
            PlannedCase("MAN-A11Y", "Keyboard-only navigation, focus visibility and screen-reader review", Category.ACCESSIBILITY,
                        Engine.MANUAL, reason),
            PlannedCase("MAN-UX", "Empty, loading and error states; onboarding and navigation clarity", Category.PRODUCT,
                        Engine.MANUAL, reason),
        ]  # fmt: skip
    return manual


def generate_plan(db: Session, version: ProjectVersion, *, target_url: str | None, name: str | None = None,
                  allow_mutations: bool = False) -> TestPlan:  # fmt: skip
    context = plan_context(db, version, target_url=target_url, allow_mutations=allow_mutations)
    planned: list[PlannedCase] = []
    notes: list[str] = []
    for adapter in ADAPTERS:
        capabilities = adapter.capabilities(context)
        if not capabilities:
            notes.append(f"{adapter.title}: not applicable to this project ({_why_not(adapter.name, context)}).")
            continue
        result = adapter.plan(context)
        planned += result.cases
        notes += result.notes
    planned += _manual_cases(context)
    notes += [f"Unsupported: {item}" for item in context.snapshot.unsupported]
    if allow_mutations:
        notes.append("State-changing API requests are ENABLED for this plan (owner opted in).")

    counters: dict[str, int] = {}
    cases = []
    for item in planned:
        counters[item.prefix] = counters.get(item.prefix, 0) + 1
        cases.append(TestCase(
            key=f"{item.prefix}-{counters[item.prefix]:03d}", title=item.title[:300], category=item.category.value,
            engine=item.engine.value, priority=item.priority, origin=Origin.DETERMINISTIC.value, spec=item.spec,
        ))  # fmt: skip
    plan = TestPlan(project_id=version.project_id, version_id=version.id, target_url=target_url,
                    name=name or f"QA plan for version {version.number}", notes=notes, allow_mutations=allow_mutations)  # fmt: skip
    plan.cases = cases
    db.add(plan)
    db.flush()
    audit.record(db, "plan.generated", "test_plan", plan.id, cases=len(cases), target_url=target_url,
                 allow_mutations=allow_mutations)  # fmt: skip
    return plan


def _why_not(adapter: str, context: PlanContext) -> str:
    if adapter.startswith(("android", "apk")):
        return "no APK or Android project was found"
    if context.target_url is None and adapter in {"web_functional", "visual", "accessibility", "web_performance", "api"}:
        return "no running target URL was provided"
    if context.workspace is None:
        return "no source files were imported"
    return "nothing in the project inventory matches"
