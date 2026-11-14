"""HTTP API: authentication, ingestion, planning, run lifecycle and honest result states."""

import io
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.base import ReadinessReport
from app.adapters.web import ProjectPipelineAdapter, WebSecurityAdapter
from app.domain import Readiness, ResultStatus
from app.engines.base import CaseOutcome, EngineContext
from app.main import app
from app.models import AuditEvent, Finding, Job, TestRun
from app.runs import service as runs
from tests.conftest import DEMO_PROJECT


def _import_demo(client: TestClient, **extra: str) -> dict:
    response = client.post("/api/projects/import", data={"name": "Demo", "kind": "local_folder",
                                                         "local_path": str(DEMO_PROJECT), **extra})  # fmt: skip
    assert response.status_code == 201, response.text
    return response.json()


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}])
def test_api_rejects_missing_or_wrong_token(headers: dict[str, str]) -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/projects", headers=headers).status_code == 401
        assert anonymous.put("/api/settings/execution", json={}, headers=headers).status_code == 401
        assert anonymous.get("/health").status_code == 200  # liveness only, no data


def test_unknown_host_header_is_rejected(client: TestClient) -> None:
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 400


def test_import_local_folder_builds_architecture_and_audit(client: TestClient, db) -> None:
    project = _import_demo(client)
    assert project["versions"][0]["file_count"] > 5 and project["sources"][0]["kind"] == "local_folder"
    architecture = client.get(f"/api/projects/{project['id']}/architecture").json()
    assert "FastAPI" in architecture["inventory"]["frameworks"]
    assert any(e["path"] == "/api/orders" for e in architecture["api_endpoints"])
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "project.imported")) is not None


def test_import_validation_errors(client: TestClient) -> None:
    bad_path = client.post("/api/projects/import", data={"name": "X", "kind": "local_folder", "local_path": "relative/dir"})
    assert bad_path.status_code == 422 and "absolute" in bad_path.json()["detail"]
    missing_file = client.post("/api/projects/import", data={"name": "X", "kind": "zip"})
    assert missing_file.status_code == 422
    git = client.post("/api/projects/import", data={"name": "X", "kind": "git"})
    assert git.status_code == 422 and "local git repository" in git.json()["detail"]
    assert client.get("/api/projects").json()["total"] == 0


def test_zip_upload_safe_and_unsafe(client: TestClient) -> None:
    good = _zip_bytes({"requirements.txt": "flask==3.0.0\n", "app.py": "from flask import Flask\n", ".env": "TOKEN=abc"})
    created = client.post("/api/projects/import", data={"name": "Zip", "kind": "zip"},
                          files={"file": ("project.zip", good, "application/zip")})  # fmt: skip
    assert created.status_code == 201
    assert created.json()["sources"][0]["config"]["excluded_secret_files"] == [".env"]
    evil = _zip_bytes({"../../escape.txt": "x"})
    rejected = client.post("/api/projects/import", data={"name": "Evil", "kind": "zip"},
                           files={"file": ("evil.zip", evil, "application/zip")})  # fmt: skip
    assert rejected.status_code == 422 and "unsafe path" in rejected.json()["detail"]
    not_zip = client.post("/api/projects/import", data={"name": "Bad", "kind": "zip"},
                          files={"file": ("x.zip", b"not a zip", "application/zip")})  # fmt: skip
    assert not_zip.status_code == 422
    assert client.get("/api/projects").json()["total"] == 1


def test_target_must_be_authorised_before_planning(client: TestClient) -> None:
    project = _import_demo(client)
    body = {"project_id": project["id"], "target_url": "http://127.0.0.1:8100"}
    denied = client.post("/api/plans", json=body)
    assert denied.status_code == 422 and "not an authorised target" in denied.json()["detail"]
    settings = client.get("/api/settings/execution").json()
    settings["allowed_targets"] = ["http://127.0.0.1:8100"]
    assert client.put("/api/settings/execution", json=settings).json()["allowed_targets"] == ["127.0.0.1:8100"]
    plan = client.post("/api/plans", json=body).json()
    engines = {case["engine"] for case in plan["cases"]}
    assert {"browser", "api", "dependency_scan", "static_analysis", "project_tests", "manual"} <= engines
    contract = [c for c in plan["cases"] if c["spec"].get("check") == "operation"]
    assert len(contract) == 4  # one per OpenAPI operation in the demo spec


def test_settings_validation_and_pagination_bounds(client: TestClient) -> None:
    settings = client.get("/api/settings/execution").json()
    assert client.put("/api/settings/execution", json=settings | {"sandbox_memory_mb": 999_999}).status_code == 422
    assert client.put("/api/settings/execution", json=settings | {"allowed_targets": ["no-port"]}).status_code == 422
    assert client.get("/api/findings", params={"limit": 500}).status_code == 422
    assert client.get(f"/api/projects/{uuid.uuid4()}").status_code == 404


def _stub_adapters(monkeypatch: pytest.MonkeyPatch, *, crash_dependency_scan: bool = False) -> None:
    """Replace Docker-backed execution with deterministic stand-ins. The built-in secret check stays real,
    so result persistence, evidence masking and finding handling are exercised with genuine output."""
    real_execute = WebSecurityAdapter.execute

    def security(self: WebSecurityAdapter, ctx: EngineContext, spec: dict) -> CaseOutcome:
        if spec.get("tool") == "builtin-secrets":
            return real_execute(self, ctx, spec)
        if spec["_engine"] == "dependency_scan" and crash_dependency_scan:
            raise RuntimeError("scanner exploded")
        return CaseOutcome.blocked("stubbed")

    for adapter in (WebSecurityAdapter, ProjectPipelineAdapter):
        monkeypatch.setattr(adapter, "readiness", lambda self, ctx, spec: ReadinessReport())
    monkeypatch.setattr(WebSecurityAdapter, "execute", security)
    monkeypatch.setattr(ProjectPipelineAdapter, "execute", lambda self, ctx, spec: CaseOutcome.blocked("Docker is not running"))


def test_run_reports_only_real_outcomes(client: TestClient, db, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _import_demo(client)
    plan = client.post("/api/plans", json={"project_id": project["id"]}).json()
    by_tool = {c["spec"].get("tool"): c["key"] for c in plan["cases"]}
    sast = next(c for c in plan["cases"] if c["spec"].get("tool") == "semgrep")
    assert client.patch(f"/api/plans/{plan['id']}/cases/{sast['id']}", json={"enabled": False}).json()["enabled"] is False
    _stub_adapters(monkeypatch, crash_dependency_scan=True)

    queued = client.post(f"/api/plans/{plan['id']}/runs")
    assert queued.status_code == 202 and queued.json()["status"] == "queued"
    job = db.scalar(select(Job).where(Job.kind == "test_run"))
    runs.execute_run(uuid.UUID(job.payload["run_id"]))

    run = client.get(f"/api/runs/{queued.json()['id']}").json()
    states = {r["case"]["key"]: r["status"] for r in run["results"]}
    assert run["status"] == "completed" and len(run["results"]) == run["planned_cases"]
    assert states["SEC-DEP-001"] == "blocked"  # crashed engine: never passed, never hidden
    assert "scanner exploded" in next(r for r in run["results"] if r["case"]["key"] == "SEC-DEP-001")["message"]
    assert states[by_tool["semgrep"]] == "skipped"  # disabled by the owner
    assert all(states[k] == "not_tested" for k in states if k.startswith("MAN-"))
    assert states[by_tool["builtin-secrets"]] == "failed"  # built-in check really found the seeded credential
    assert run["summary"]["passed"] == sum(1 for s in states.values() if s == "passed")
    assert run["summary"]["total"] == len(states) and set(run["summary"]) == {*[s.value for s in ResultStatus], "total"}
    assert run["readiness"]["SEC-DEP-001"]["status"] == "ready" and run["progress"]["cleanup"] is not None

    finding = db.scalar(select(Finding).where(Finding.source_tool == "builtin-secrets"))
    detail = client.get(f"/api/findings/{finding.id}").json()
    assert detail["severity"] == "high" and detail["reproduction_steps"] and detail["verification_status"] == "unverified"
    assert detail["classification"] == "potential_defect"  # a heuristic match is not a confirmed defect
    assert "demo-admin-12345" not in str(detail["evidence"]) and "de**" in detail["evidence"][0]["content"]

    report = client.post(f"/api/runs/{run['id']}/report").json()
    assert "must not be read as passing" in report["content_markdown"]
    assert report["data"]["counts"]["blocked"] == run["summary"]["blocked"]


def test_rerun_does_not_duplicate_findings_and_runs_can_be_compared(
    client: TestClient, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _import_demo(client)
    plan = client.post("/api/plans", json={"project_id": project["id"]}).json()
    _stub_adapters(monkeypatch)
    run_ids = []
    for _ in range(2):
        run_ids.append(client.post(f"/api/plans/{plan['id']}/runs").json()["id"])
        runs.execute_run(uuid.UUID(run_ids[-1]))
    assert len(db.scalars(select(TestRun)).all()) == 2
    assert len(db.scalars(select(Finding).where(Finding.source_tool == "builtin-secrets")).all()) == 1
    listed = client.get("/api/findings", params={"category": "security", "q": "credential"}).json()
    assert listed["total"] == 1
    comparison = client.get(f"/api/runs/{run_ids[1]}/compare/{run_ids[0]}").json()
    assert comparison["changes"] == [] and comparison["regressions"] == 0 and comparison["unchanged"] == len(plan["cases"])


def test_blocked_readiness_is_reported_with_the_exact_reason(client: TestClient, db, monkeypatch: pytest.MonkeyPatch) -> None:
    """A case that cannot run is never executed and never passes: the reason is stored with the result."""
    project = _import_demo(client)
    plan = client.post("/api/plans", json={"project_id": project["id"]}).json()
    _stub_adapters(monkeypatch)
    monkeypatch.setattr(
        WebSecurityAdapter,
        "readiness",
        lambda self, ctx, spec: ReadinessReport().block(
            Readiness.BLOCKED, "Docker is not running (the scanner runs in a container)"
        ),
    )
    executed = []
    monkeypatch.setattr(WebSecurityAdapter, "execute", lambda self, ctx, spec: executed.append(spec) or CaseOutcome.blocked("x"))
    preview = client.get(f"/api/plans/{plan['id']}/readiness").json()
    assert preview["cases"]["SEC-DEP-001"] == {
        "status": "blocked",
        "requirements": [],
        "reasons": ["Docker is not running (the scanner runs in a container)"],
    }
    run_id = client.post(f"/api/plans/{plan['id']}/runs").json()["id"]
    runs.execute_run(uuid.UUID(run_id))
    result = next(r for r in client.get(f"/api/runs/{run_id}").json()["results"] if r["case"]["key"] == "SEC-DEP-001")
    assert result["status"] == "blocked" and "Docker is not running" in result["message"] and executed == []


def test_run_can_be_cancelled_and_remaining_cases_are_skipped_not_passed(
    client: TestClient, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _import_demo(client)
    plan = client.post("/api/plans", json={"project_id": project["id"]}).json()
    _stub_adapters(monkeypatch)
    run_id = client.post(f"/api/plans/{plan['id']}/runs").json()["id"]
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 202
    runs.execute_run(uuid.UUID(run_id))
    run = client.get(f"/api/runs/{run_id}").json()
    assert (
        run["status"] == "cancelled"
        and run["summary"]["skipped"] == run["summary"]["total"] > 0
        and run["summary"]["passed"] == 0
    )
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 422  # already finished
