"""API engine: contract, negative, header and latency checks against an authorised target."""

import json
import statistics
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.discovery.routes import load_openapi
from app.domain import Category, ResultStatus, Severity
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft
from app.engines.http_client import Exchange, PolicyClient, RequestBudgetExceeded
from app.security.ssrf import TargetNotAllowed

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
SECURITY_HEADERS = {
    "content-security-policy": (Severity.MEDIUM, "Mitigates XSS and content injection."),
    "x-content-type-options": (Severity.LOW, "Prevents MIME sniffing (expected value: nosniff)."),
    "x-frame-options": (Severity.LOW, "Clickjacking protection (or CSP frame-ancestors)."),
    "referrer-policy": (Severity.LOW, "Limits referrer leakage."),
}
_MAX_REF_DEPTH = 30


def _deref(node: Any, spec: dict[str, Any], depth: int = 0) -> Any:
    """Inline local $refs and translate OpenAPI 3.0 `nullable` so jsonschema can validate."""
    if depth > _MAX_REF_DEPTH:
        return {}
    if isinstance(node, list):
        return [_deref(item, spec, depth + 1) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        target: Any = spec
        for part in ref[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(target, dict) else {}
        return _deref(target, spec, depth + 1)
    resolved = {key: _deref(value, spec, depth + 1) for key, value in node.items()}
    if resolved.pop("nullable", False) and isinstance(resolved.get("type"), str):
        resolved["type"] = [resolved["type"], "null"]
    return resolved


def _sample(schema: dict[str, Any]) -> Any:
    for key in ("example", "default"):
        if key in schema:
            return schema[key]
    if schema.get("enum"):
        return schema["enum"][0]
    return {"integer": 1, "number": 1, "boolean": True}.get(str(schema.get("type")), "test")


def _load_spec(ctx: EngineContext, spec_file: str) -> dict[str, Any] | None:
    if not ctx.workspace:
        return None
    path = (ctx.workspace / spec_file).resolve()
    if not path.is_relative_to(ctx.workspace.resolve()) or not path.is_file():
        return None
    return load_openapi(path)


def _build_url(base: str, path: str, operation: dict[str, Any], spec: dict[str, Any],
               overrides: dict[str, Any] | None = None) -> str:  # fmt: skip
    query: dict[str, Any] = {}
    for raw in operation.get("parameters", []):
        param = _deref(raw, spec)
        name, schema = param.get("name", ""), param.get("schema") or {}
        value = (overrides or {}).get(name, param.get("example", _sample(schema)))
        if param.get("in") == "path":
            path = path.replace("{" + name + "}", quote(str(value), safe=""))
        elif param.get("in") == "query" and param.get("required"):
            query[name] = value
    url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    return f"{url}?{urlencode(query)}" if query else url


def _finding(key: str, severity: Severity, title: str, description: str, exchange: Exchange,
             remediation: str, category: Category = Category.API) -> FindingDraft:  # fmt: skip
    return FindingDraft(
        key=key, category=category, severity=severity, title=title, description=description,
        source_tool="api-engine", affected_component=f"{exchange.method} {exchange.url}", confidence=0.9,
        reproduction_steps=[f"Send `{exchange.method} {exchange.url}`"
                            + (f" with JSON body `{json.dumps(exchange.request_body)}`." if exchange.request_body is not None else "."),
                            f"Observe HTTP {exchange.status}."],
        remediation=remediation,
        facts={"kind": "api", "method": exchange.method, "url": exchange.url, "status": exchange.status},
        evidence=[EvidenceDraft("http_exchange", f"{exchange.method} {exchange.url}", exchange.transcript())],
    )  # fmt: skip


def _operation(ctx: EngineContext, client: PolicyClient, case: dict[str, Any]) -> CaseOutcome:
    """Call one documented operation and verify status code and response schema."""
    spec = _load_spec(ctx, case["spec_file"])
    if spec is None:
        return CaseOutcome.blocked(f"OpenAPI file {case['spec_file']} could not be loaded")
    method, path = case["method"], case["path"]
    operation = spec["paths"][path][method.lower()]
    body = None
    if method in MUTATING:
        if not ctx.allow_mutations:
            return CaseOutcome(
                ResultStatus.SKIPPED,
                "state-changing request; enable 'allow state-changing requests' for this plan",
            )
        media = _deref(operation.get("requestBody", {}), spec).get("content", {}).get("application/json", {})
        body = media.get("example", (media.get("schema") or {}).get("example"))
        if operation.get("requestBody") and body is None:
            return CaseOutcome.blocked("the spec has no example request body, so a valid request cannot be built")
    assert ctx.target_url
    exchange = client.request(method, _build_url(ctx.target_url, path, operation, spec), json_body=body)
    evidence = [EvidenceDraft("http_exchange", f"{method} {path}", exchange.transcript())]
    responses = operation.get("responses") or {}
    documented_success = sorted(code for code in responses if str(code).startswith("2"))
    details = {
        "status": exchange.status,
        "elapsed_ms": round(exchange.elapsed_ms, 1),
        "documented": sorted(map(str, responses)),
    }
    ident = f"{method} {path}"

    if str(exchange.status) not in documented_success:
        severity = Severity.HIGH if exchange.status >= 500 else Severity.MEDIUM
        return CaseOutcome(
            ResultStatus.FAILED, f"expected {'/'.join(documented_success) or '2xx'}, got {exchange.status}", details, evidence,
            [_finding(f"status:{ident}", severity, f"{ident} returns {exchange.status} instead of a documented success status",
                      f"The OpenAPI contract documents {documented_success} for a valid request.", exchange,
                      "Fix the handler or correct the contract so they agree.")],
        )  # fmt: skip
    schema = _deref(responses[str(exchange.status)], spec).get("content", {}).get("application/json", {}).get("schema")
    if not schema:
        return CaseOutcome(ResultStatus.PASSED, f"HTTP {exchange.status} (no response schema documented)", details, evidence)
    invalid_json = False
    try:
        payload = exchange.json()
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(map(str, e.path)))
    except json.JSONDecodeError:
        errors, invalid_json = [], True
    except SchemaError as exc:
        return CaseOutcome.blocked(f"the documented response schema is itself invalid: {exc.message}")
    problems = (
        ["response body is not valid JSON"]
        if invalid_json
        else [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors[:20]]
    )
    if problems:
        details["schema_errors"] = problems
        return CaseOutcome(
            ResultStatus.FAILED, f"response violates the documented schema ({len(problems)} problem(s))", details, evidence,
            [_finding(f"schema:{ident}", Severity.MEDIUM, f"{ident} response does not match its OpenAPI schema",
                      "Schema violations:\n" + "\n".join(f"- {p}" for p in problems), exchange,
                      "Return the documented types or update the contract; clients generated from the spec will break.")],
        )  # fmt: skip
    return CaseOutcome(ResultStatus.PASSED, f"HTTP {exchange.status}, schema valid", details, evidence)


def _negative_not_found(ctx: EngineContext, client: PolicyClient, case: dict[str, Any]) -> CaseOutcome:
    spec = _load_spec(ctx, case["spec_file"])
    if spec is None:
        return CaseOutcome.blocked(f"OpenAPI file {case['spec_file']} could not be loaded")
    path = case["path"]
    operation = spec["paths"][path]["get"]
    overrides = {}
    for raw in operation.get("parameters", []):
        param = _deref(raw, spec)
        if param.get("in") == "path":
            numeric = (param.get("schema") or {}).get("type") in {"integer", "number"}
            overrides[param["name"]] = 987654321 if numeric else "qai-does-not-exist-987654321"
    assert ctx.target_url
    exchange = client.request("GET", _build_url(ctx.target_url, path, operation, spec, overrides))
    evidence = [EvidenceDraft("http_exchange", f"GET {path} (unknown id)", exchange.transcript())]
    if exchange.status == 404:
        return CaseOutcome(ResultStatus.PASSED, "unknown resource returns 404", {"status": 404}, evidence)
    severity = Severity.HIGH if exchange.status >= 500 else Severity.MEDIUM
    return CaseOutcome(
        ResultStatus.FAILED, f"expected 404 for an unknown resource, got {exchange.status}", {"status": exchange.status}, evidence,
        [_finding(f"notfound:GET {path}", severity, f"GET {path} returns {exchange.status} for an unknown resource",
                  "The contract documents 404 for unknown resources. A 5xx here usually means an unhandled exception.",
                  exchange, "Handle the missing resource explicitly and return 404.")],
    )  # fmt: skip


def _negative_invalid_body(ctx: EngineContext, client: PolicyClient, case: dict[str, Any]) -> CaseOutcome:
    assert ctx.target_url
    url = urljoin(ctx.target_url.rstrip("/") + "/", case["path"].lstrip("/"))
    exchange = client.request(case["method"], url, json_body={"__qai_invalid__": True})
    evidence = [EvidenceDraft("http_exchange", f"{case['method']} {case['path']} (invalid body)", exchange.transcript())]
    if 400 <= exchange.status < 500:
        return CaseOutcome(
            ResultStatus.PASSED,
            f"invalid body rejected with {exchange.status}",
            {"status": exchange.status},
            evidence,
        )
    severity = Severity.HIGH if exchange.status >= 500 else Severity.MEDIUM
    return CaseOutcome(
        ResultStatus.FAILED, f"invalid body was not rejected with 4xx (got {exchange.status})", {"status": exchange.status}, evidence,
        [_finding(f"validation:{case['method']} {case['path']}", severity,
                  f"{case['method']} {case['path']} does not reject an invalid request body",
                  "A body missing all required fields must be rejected with a 4xx status.", exchange,
                  "Validate request bodies at the API boundary.")],
    )  # fmt: skip


def _unknown_route(ctx: EngineContext, client: PolicyClient, _case: dict[str, Any]) -> CaseOutcome:
    assert ctx.target_url
    exchange = client.request("GET", urljoin(ctx.target_url.rstrip("/") + "/", "qai-nonexistent-route-987654321"))
    evidence = [EvidenceDraft("http_exchange", "GET unknown route", exchange.transcript())]
    leaked = any(marker in exchange.body[:20000] for marker in (b"Traceback (most recent call last)", b"at Object.<anonymous>"))
    if exchange.status == 404 and not leaked:
        return CaseOutcome(ResultStatus.PASSED, "unknown route returns 404 without internals", {"status": 404}, evidence)
    return CaseOutcome(
        ResultStatus.FAILED, f"unknown route returned {exchange.status}" + (" and leaks a stack trace" if leaked else ""),
        {"status": exchange.status, "stack_trace_leaked": leaked}, evidence,
        [_finding("unknown-route", Severity.MEDIUM, "Unknown routes are not handled with a clean 404",
                  "Error responses must not expose internals and unknown routes should return 404.", exchange,
                  "Add a catch-all 404 handler and disable debug error pages.")],
    )  # fmt: skip


def _security_headers(ctx: EngineContext, client: PolicyClient, _case: dict[str, Any]) -> CaseOutcome:
    assert ctx.target_url
    exchange = client.request("GET", ctx.target_url)
    present = {name.lower() for name in exchange.headers}
    missing = [name for name in SECURITY_HEADERS if name not in present]
    if ctx.target_url.startswith("https://") and "strict-transport-security" not in present:
        missing.append("strict-transport-security")
    evidence = [EvidenceDraft("http_exchange", "response headers", exchange.transcript())]
    findings = [
        FindingDraft(
            key=f"header:{name}",
            category=Category.SECURITY,
            severity=SECURITY_HEADERS.get(name, (Severity.MEDIUM, ""))[0],
            title=f"Missing security header: {name}",
            description=SECURITY_HEADERS.get(name, (None, "Enforces HTTPS on subsequent visits."))[1],
            source_tool="api-engine",
            affected_component=ctx.target_url,
            confidence=0.95,
            reproduction_steps=[f"Run `curl -I {ctx.target_url}` and note that `{name}` is absent."],
            remediation=f"Send the `{name}` response header from the application or reverse proxy.",
            facts={"kind": "security_header", "header": name},
            evidence=evidence,
        )  # fmt: skip
        for name in missing
    ]
    status = ResultStatus.FAILED if missing else ResultStatus.PASSED
    return CaseOutcome(status, f"{len(missing)} recommended header(s) missing", {"missing": missing}, evidence, findings)


def _latency(ctx: EngineContext, client: PolicyClient, case: dict[str, Any]) -> CaseOutcome:
    """Sequential, bounded latency sample (not a load test)."""
    assert ctx.target_url
    url = urljoin(ctx.target_url.rstrip("/") + "/", case["path"].lstrip("/"))
    samples, statuses = [], set()
    for _ in range(ctx.settings.perf_samples):
        exchange = client.request("GET", url)
        samples.append(exchange.elapsed_ms)
        statuses.add(exchange.status)
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    details = {"samples": len(samples), "median_ms": round(statistics.median(samples), 1), "p95_ms": round(p95, 1),
               "max_ms": round(ordered[-1], 1), "statuses": sorted(statuses), "threshold_ms": ctx.settings.perf_slow_threshold_ms}  # fmt: skip
    evidence = [EvidenceDraft("log", f"latency samples GET {case['path']}", json.dumps(details, indent=2))]
    if any(status >= 500 for status in statuses):
        return CaseOutcome.blocked("endpoint returned 5xx during sampling; latency is not meaningful")
    if p95 <= ctx.settings.perf_slow_threshold_ms:
        return CaseOutcome(ResultStatus.PASSED, f"p95 {p95:.0f} ms (median {details['median_ms']} ms)", details, evidence)
    return CaseOutcome(
        ResultStatus.FAILED, f"p95 {p95:.0f} ms exceeds {ctx.settings.perf_slow_threshold_ms} ms", details, evidence,
        [FindingDraft(
            key=f"slow:GET {case['path']}", category=Category.PERFORMANCE, severity=Severity.MEDIUM,
            title=f"Slow endpoint: GET {case['path']} (p95 {p95:.0f} ms)",
            description=f"{len(samples)} sequential requests from the QA host. Median {details['median_ms']} ms, max {details['max_ms']} ms.",
            source_tool="api-engine", affected_component=f"GET {case['path']}", confidence=0.7,
            reproduction_steps=[f"Request GET {url} {len(samples)} times sequentially and record the timings."],
            remediation="Profile the handler; check for missing indexes, N+1 queries or synchronous external calls.",
            facts={"kind": "latency", **details}, evidence=evidence,
        )],
    )  # fmt: skip


_CHECKS = {
    "operation": _operation,
    "negative_not_found": _negative_not_found,
    "negative_invalid_body": _negative_invalid_body,
    "unknown_route": _unknown_route,
    "security_headers": _security_headers,
    "latency": _latency,
}


def run_case(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    if not ctx.target_url:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "no target URL configured for this plan")
    check = _CHECKS.get(spec.get("check", ""))
    if not check:
        return CaseOutcome.blocked(f"unknown API check '{spec.get('check')}'")
    try:
        with PolicyClient(ctx.settings.allowed_targets, ctx.settings.api_request_timeout_s,
                          ctx.settings.api_max_requests_per_run) as client:  # fmt: skip
            return check(ctx, client, spec)
    except TargetNotAllowed as exc:
        return CaseOutcome.blocked(f"target not authorised: {exc}")
    except RequestBudgetExceeded as exc:
        return CaseOutcome.blocked(str(exc))
    except httpx.HTTPError as exc:
        return CaseOutcome.blocked(f"target unreachable: {type(exc).__name__}: {exc}")
