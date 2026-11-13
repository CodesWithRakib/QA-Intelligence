"""API engine against a real local HTTP server, plus scanner result parsing."""

import json
from pathlib import Path

import pytest

from app.domain import ResultStatus
from app.engines import api, security_scan
from app.engines.base import EngineContext
from app.security.ssrf import normalise_allow_entry
from app.services.settings import ExecutionSettings

SPEC = {
    "openapi": "3.0.3", "info": {"title": "t", "version": "1"},
    "paths": {
        "/items": {"get": {"responses": {"200": {"description": "ok", "content": {"application/json": {
            "schema": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}}}}}},
                   "post": {"requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                            "responses": {"201": {"description": "created"}}}},
        "/bad-items": {"get": {"responses": {"200": {"description": "ok", "content": {"application/json": {
            "schema": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}}}}}}},
        "/items/{item_id}": {"get": {
            "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "integer", "example": 1}}],
            "responses": {"200": {"description": "ok"}, "404": {"description": "missing"}}}},
    },
    "components": {"schemas": {"Item": {"type": "object", "required": ["id", "name"], "properties": {
        "id": {"type": "integer"}, "name": {"type": "string", "nullable": True}}}}},
}  # fmt: skip


@pytest.fixture
def ctx(tmp_path: Path, target_server: str) -> EngineContext:
    (tmp_path / "openapi.json").write_text(json.dumps(SPEC))
    settings = ExecutionSettings(allowed_targets=[normalise_allow_entry(target_server)], perf_samples=3)
    return EngineContext(workspace=tmp_path, target_url=target_server, settings=settings,
                         evidence_dir=tmp_path / "evidence", inventory={})  # fmt: skip


def _operation(method: str, path: str) -> dict[str, str]:
    return {"check": "operation", "spec_file": "openapi.json", "method": method, "path": path}


def test_contract_check_passes_for_conforming_endpoint(ctx: EngineContext) -> None:
    outcome = api.run_case(ctx, _operation("GET", "/items"))
    assert outcome.status is ResultStatus.PASSED and outcome.evidence[0].kind == "http_exchange"


def test_schema_violation_is_a_failure_with_reproducible_finding(ctx: EngineContext) -> None:
    outcome = api.run_case(ctx, _operation("GET", "/bad-items"))
    assert outcome.status is ResultStatus.FAILED
    finding = outcome.findings[0]
    assert "is not of type 'integer'" in finding.description and finding.reproduction_steps
    assert "GET" in finding.evidence[0].content


def test_unknown_resource_returning_500_fails(ctx: EngineContext) -> None:
    outcome = api.run_case(ctx, {"check": "negative_not_found", "spec_file": "openapi.json", "path": "/items/{item_id}"})
    assert outcome.status is ResultStatus.FAILED and outcome.findings[0].severity.value == "high"


def test_invalid_body_rejected_and_unknown_route_clean(ctx: EngineContext) -> None:
    body = api.run_case(ctx, {"check": "negative_invalid_body", "method": "POST", "path": "/items"})
    route = api.run_case(ctx, {"check": "unknown_route"})
    assert body.status is ResultStatus.PASSED and route.status is ResultStatus.PASSED


def test_missing_security_headers_and_latency(ctx: EngineContext) -> None:
    headers = api.run_case(ctx, {"check": "security_headers"})
    assert headers.status is ResultStatus.FAILED and "content-security-policy" in headers.details["missing"]
    latency = api.run_case(ctx, {"check": "latency", "path": "/items"})
    assert latency.status is ResultStatus.PASSED and latency.details["samples"] == 3


def test_mutating_call_is_skipped_unless_owner_opted_in(ctx: EngineContext) -> None:
    assert api.run_case(ctx, _operation("POST", "/items")).status is ResultStatus.SKIPPED
    ctx.allow_mutations = True
    blocked = api.run_case(ctx, _operation("POST", "/items"))
    assert blocked.status is ResultStatus.BLOCKED and "no example request body" in blocked.message


def test_unauthorised_target_and_redirect_escape_are_blocked(ctx: EngineContext) -> None:
    escaped = api.run_case(ctx, {"check": "latency", "path": "/redirect-out"})
    assert escaped.status is ResultStatus.BLOCKED and "not authorised" in escaped.message
    ctx.settings = ExecutionSettings()
    assert api.run_case(ctx, {"check": "unknown_route"}).status is ResultStatus.BLOCKED


def test_unreachable_target_is_blocked_not_failed(ctx: EngineContext) -> None:
    ctx.target_url = "http://127.0.0.1:9"
    ctx.settings = ExecutionSettings(allowed_targets=["127.0.0.1:9"], api_request_timeout_s=2)
    outcome = api.run_case(ctx, {"check": "unknown_route"})
    assert outcome.status is ResultStatus.BLOCKED and "unreachable" in outcome.message


def test_trivy_report_is_grouped_and_fix_version_resolves_all_advisories() -> None:
    report = {"Results": [{"Target": "requirements.txt", "Type": "pip", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-1", "PkgName": "jinja2", "InstalledVersion": "3.1.2", "FixedVersion": "3.1.3", "Severity": "MEDIUM"},
        {"VulnerabilityID": "CVE-2", "PkgName": "jinja2", "InstalledVersion": "3.1.2", "FixedVersion": "2.11.9, 3.1.5", "Severity": "HIGH"},
        {"VulnerabilityID": "CVE-3", "PkgName": "other", "InstalledVersion": "1.0", "FixedVersion": "", "Severity": "LOW"},
    ]}]}  # fmt: skip
    packages = security_scan.vulnerable_packages(report)
    jinja = packages[("requirements.txt", "jinja2")]
    assert jinja["fixed_version"] == "3.1.5" and jinja["fully_fixable"] and len(jinja["advisories"]) == 2
    other = packages[("requirements.txt", "other")]
    assert other["fixed_version"] is None and not other["fully_fixable"]
