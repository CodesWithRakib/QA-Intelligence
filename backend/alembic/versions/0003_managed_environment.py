"""managed test environment

Adds the per-plan flag for dynamic checks against a platform-started local copy, and allows the
`environment` engine. Additive: safe to apply while older code is running.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENGINES = [
    "browser", "api", "dependency_scan", "static_analysis", "project_tests", "database_static", "lighthouse", "load_test",
    "dast", "android_static", "android_dynamic", "manual",
]  # fmt: skip
_CHECK = op.f("ck_test_cases_engine_valid")


def _engine_check(values: list[str]) -> None:
    op.drop_constraint(_CHECK, "test_cases", type_="check")
    op.create_check_constraint(_CHECK, "test_cases", f"engine IN ({', '.join(repr(v) for v in values)})")


def upgrade() -> None:
    op.add_column("test_plans", sa.Column("managed_environment", sa.Boolean(), server_default="false", nullable=False))
    _engine_check([*_ENGINES, "environment"])


def downgrade() -> None:
    """Fails (by design) while test cases still use the `environment` engine."""
    _engine_check(_ENGINES)
    op.drop_column("test_plans", "managed_environment")
