"""Managed test environment: profile validation, placeholder resolution and failure handling."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.base import ReadinessReport, target_readiness
from app.domain import Classification, Readiness, ResultStatus
from app.engines import environment, project_tests
from app.engines.base import EngineContext
from app.engines.environment import EnvironmentProfile
from app.services.settings import ExecutionSettings
from tests.conftest import REPO_ROOT

PROFILE = {
    "services": [{"name": "postgres", "image": "postgres:16-alpine", "env": {"POSTGRES_PASSWORD": "${SECRET:db}"}}],
    "env": {"DATABASE_URL": "postgresql://app:${SECRET:db}@postgres:5432/app", "NEXT_PUBLIC_API_URL": "${URL:api}/v1",
            "CORS_ORIGIN": "${URL:web}", "API_HOST": "${HOSTPORT:api}"},
    "setup": ["pnpm --filter api run build"],
    "apps": [{"name": "api", "command": "pnpm --filter api run start", "port": 4000, "health_path": "/health"},
             {"name": "web", "command": "pnpm --filter web exec next dev", "port": 3000}],
    "target": "web",
}  # fmt: skip


def _ctx(tmp_path: Path, config: dict | None = None) -> EngineContext:
    return EngineContext(workspace=tmp_path, target_url=None, settings=ExecutionSettings(), evidence_dir=tmp_path / "e",
                         inventory={}, project_config=config or {})  # fmt: skip


def test_profile_accepts_only_official_service_images_and_safe_names() -> None:
    assert EnvironmentProfile.model_validate(PROFILE).target == "web"
    for image in ("evil/postgres:16", "postgres", "ghcr.io/someone/tool:1", "postgres:16 --privileged"):
        with pytest.raises(ValidationError):
            EnvironmentProfile.model_validate({**PROFILE, "services": [{"name": "db", "image": image}]})
    with pytest.raises(ValidationError):
        EnvironmentProfile.model_validate({**PROFILE, "env": {"bad key": "x"}})
    with pytest.raises(ValidationError):
        EnvironmentProfile.model_validate({**PROFILE, "apps": []})
    with pytest.raises(ValidationError):
        EnvironmentProfile.model_validate({**PROFILE, "apps": [{"name": "Web App", "command": "x", "port": 3000}]})


def test_placeholders_resolve_to_local_urls_and_per_run_secrets() -> None:
    urls = {"api": "http://127.0.0.1:41000", "web": "http://127.0.0.1:41001"}
    generated: dict[str, str] = {}
    resolved = {key: environment._resolve(value, urls, generated) for key, value in PROFILE["env"].items()}
    assert resolved["NEXT_PUBLIC_API_URL"] == "http://127.0.0.1:41000/v1" and resolved["API_HOST"] == "127.0.0.1:41000"
    secret = generated["db"]
    assert len(secret) == 48 and resolved["DATABASE_URL"] == f"postgresql://app:{secret}@postgres:5432/app"
    # The same name yields the same value within a run (service and app must agree), a new run gets a new one.
    assert environment._resolve("${SECRET:db}", urls, generated) == secret
    assert environment._resolve("${SECRET:db}", urls, {}) != secret
    with pytest.raises(environment.ManagedEnvironmentError, match="unknown app"):
        environment._resolve("${URL:missing}", urls, generated)


def test_profile_commands_use_the_projects_pinned_package_manager() -> None:
    bench = project_tests.Workbench(
        "v", "node:22-slim", "node", "/work", project_tests.sandbox.Limits(), "/work/.qai-pm/node_modules/.bin/pnpm"
    )
    assert (
        environment._with_pm("pnpm --filter api run start", bench)
        == "/work/.qai-pm/node_modules/.bin/pnpm --filter api run start"
    )
    assert environment._with_pm("node dist/main.js", bench) == "node dist/main.js"


def test_failed_start_is_a_potential_finding_and_blocks_dynamic_checks_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, {"environment": PROFILE})

    def fail(*_: object) -> None:
        raise environment.ManagedEnvironmentError(
            "setup command `pnpm --filter api run build` failed (exit 2):\nTS2304: Cannot find name 'x'"
        )

    monkeypatch.setattr(environment, "up", fail)
    outcome = environment.start_case(ctx)
    assert outcome.status is ResultStatus.FAILED and "setup command" in outcome.message
    assert outcome.findings[0].classification is Classification.POTENTIAL  # project defect or incomplete profile: not established
    report = target_readiness(ctx, ReadinessReport())
    assert (
        report.status is Readiness.REQUIRES_ENVIRONMENT
        and "managed test environment did not start: setup command" in report.reasons[0]
    )


def test_invalid_stored_profile_blocks_instead_of_running(tmp_path: Path) -> None:
    outcome = environment.start_case(_ctx(tmp_path, {"environment": {"apps": []}}))
    assert outcome.status is ResultStatus.BLOCKED and "invalid" in outcome.message


def test_successful_start_points_the_run_at_the_local_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, {"environment": PROFILE})
    started = environment.ManagedEnvironment("net", ["c1"], {"api": "http://127.0.0.1:41000", "web": "http://127.0.0.1:41001"},
                                             "http://127.0.0.1:41001", ["app web ready"])  # fmt: skip
    monkeypatch.setattr(environment, "up", lambda *_: started)
    outcome = environment.start_case(ctx)
    assert outcome.status is ResultStatus.PASSED and ctx.target_url == "http://127.0.0.1:41001"
    assert {"127.0.0.1:41000", "127.0.0.1:41001"} <= set(ctx.settings.allowed_targets)  # only for this run's context


def test_managed_plan_requires_a_profile_and_plans_dynamic_checks_without_a_url(client: TestClient) -> None:
    project = client.post("/api/projects/import", data={"name": "Next fixture", "kind": "local_folder",
                                                        "local_path": str(REPO_ROOT / "examples" / "fixture-next")}).json()  # fmt: skip
    body = {"project_id": project["id"], "managed_environment": True}
    assert client.post("/api/plans", json=body).status_code == 422
    assert (
        client.put(
            f"/api/projects/{project['id']}/environment", json={**PROFILE, "services": [{"name": "x", "image": "nginx:1"}]}
        ).status_code
        == 422
    )
    saved = client.put(f"/api/projects/{project['id']}/environment", json=PROFILE)
    assert saved.status_code == 200 and saved.json()["profile"]["target"] == "web"
    plan = client.post("/api/plans", json=body).json()
    assert plan["managed_environment"] is True and plan["target_url"] is None
    by_engine = {case["engine"] for case in plan["cases"]}
    assert {"environment", "browser", "api", "lighthouse", "load_test", "dast"} <= by_engine
    order = [c["key"] for c in sorted(plan["cases"], key=lambda c: c["priority"])]
    assert order.index("ENV-001") < order.index("BRW-001")  # the environment starts before anything that needs it
    readiness = client.get(f"/api/plans/{plan['id']}/readiness").json()["cases"]
    assert readiness["BRW-001"]["status"] == "requires_test_environment"  # not running yet: only a run starts it
