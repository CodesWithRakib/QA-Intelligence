"""Static database checks derived from the architecture inventory (no database connection)."""

from typing import Any

from app.domain import Category, ResultStatus, Severity
from app.engines.base import CaseOutcome, EngineContext, FindingDraft


def run_case(ctx: EngineContext, _spec: dict[str, Any]) -> CaseOutcome:
    models = ctx.inventory.get("data_models") or []
    migrations = ctx.inventory.get("migrations") or []
    if not models:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "no ORM data models were detected")
    details = {"models": len(models), "migration_directories": migrations,
               "scope": "static only: live schema, constraints and query plans were not inspected"}  # fmt: skip
    if migrations:
        return CaseOutcome(ResultStatus.PASSED, f"{len(models)} model(s) with versioned migrations", details)
    files = sorted({m["file"] for m in models})
    return CaseOutcome(
        ResultStatus.FAILED, f"{len(models)} data model(s) but no migration directory", details,
        findings=[FindingDraft(
            key="db:no-migrations", category=Category.DATABASE, severity=Severity.MEDIUM,
            title="Data models exist without versioned schema migrations",
            description="ORM models were found in: " + ", ".join(files[:20]) + ". No Alembic, Django, Prisma or "
                        "Rails migration directory exists, so schema changes cannot be reproduced or rolled back.",
            source_tool="database-static", affected_component=files[0], confidence=0.7,
            reproduction_steps=["Search the repository for a migrations directory (alembic/versions, migrations/, prisma/migrations)."],
            remediation="Adopt a migration tool and commit an initial migration matching the current models.",
            facts={"kind": "no_migrations", "model_files": files[:50]},
        )],
    )  # fmt: skip
