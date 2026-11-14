"""External web tools: Lighthouse (performance audit), k6 (bounded load test), OWASP ZAP (passive scan).

All three only run against a target that passed the outbound policy. k6 and ZAP send traffic of
their own, so they are planned only for local targets unless the owner explicitly widens the scope.
"""

import ipaddress
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app import tooling
from app.config import get_settings
from app.domain import Category, Classification, ResultStatus, Severity
from app.engines import sandbox
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft
from app.engines.sandbox import ContainerSpec, Limits, Mount
from app.security.ssrf import TargetNotAllowed, validate_target

K6_IMAGE = "grafana/k6:latest"
ZAP_IMAGE = "ghcr.io/zaproxy/zaproxy:stable"
LIGHTHOUSE_CATEGORIES = ("performance", "accessibility", "best-practices", "seo")
_METRICS = {"largest-contentful-paint": "lcp_ms", "cumulative-layout-shift": "cls", "total-blocking-time": "tbt_ms",
            "first-contentful-paint": "fcp_ms", "speed-index": "speed_index_ms", "interactive": "tti_ms",
            "server-response-time": "ttfb_ms", "total-byte-weight": "total_bytes"}  # fmt: skip
_TAG = re.compile(r"<[^>]+>")
_ZAP_RISK = {"3": Severity.HIGH, "2": Severity.MEDIUM, "1": Severity.LOW, "0": Severity.INFO}


def is_local_target(url: str) -> bool:
    """True for loopback/private hosts: the only targets load tests and scans touch by default."""
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def container_url(url: str) -> str:
    """Rewrite a loopback URL so a container can reach the host (Docker Desktop's host gateway)."""
    parts = urlsplit(url)
    if parts.hostname in {"127.0.0.1", "localhost"}:
        netloc = f"host.docker.internal:{parts.port}" if parts.port else "host.docker.internal"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    return url


def _authorised(ctx: EngineContext) -> CaseOutcome | None:
    if not ctx.target_url:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "no target URL configured for this plan")
    try:
        validate_target(ctx.target_url, ctx.settings.allowed_targets)
    except TargetNotAllowed as exc:
        return CaseOutcome.blocked(f"target not authorised: {exc}")
    return None


def chrome_binary() -> Path | None:
    """A Chromium installed by Playwright (full build preferred, headless shell otherwise)."""
    import os

    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    for pattern in (
        "chromium-*/chrome-win*/chrome.exe",
        "chromium_headless_shell-*/chrome-headless-shell-win*/chrome-headless-shell.exe",
    ):
        found = sorted(root.glob(pattern), reverse=True)
        if found:
            return found[0]
    return None


# ---------------------------------------------------------------- Lighthouse


def lighthouse(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    if blocked := _authorised(ctx):
        return blocked
    node, chrome = tooling.node_binary(), chrome_binary()
    if not (tooling.lighthouse().available and node):
        return CaseOutcome.blocked(f"Lighthouse is not installed ({tooling.lighthouse().install_hint})")
    if chrome is None:
        return CaseOutcome.blocked("no Chromium build was found for Lighthouse (run `playwright install chromium`)")
    assert ctx.target_url
    url = ctx.target_url.rstrip("/") + "/" + str(spec.get("path", "/")).lstrip("/")
    env = tooling.android_env() | {"CHROME_PATH": str(chrome)}
    try:
        result = tooling.run(
            [node, tooling.lighthouse().path, url, "--output=json", "--output-path=stdout", "--quiet",
             f"--only-categories={','.join(LIGHTHOUSE_CATEGORIES)}", "--chrome-flags=--headless=new --disable-gpu",
             "--max-wait-for-load=45000"],
            timeout=240, env=env,
        )  # fmt: skip
        report = json.loads(result.stdout[result.stdout.index("{") :])
    except (ValueError, OSError) as exc:
        return CaseOutcome.blocked(f"Lighthouse did not produce a report: {type(exc).__name__}")
    if report.get("runtimeError"):
        return CaseOutcome.blocked(f"Lighthouse could not load the page: {report['runtimeError'].get('message', '')[:200]}")
    scores = {name: (round(cat["score"] * 100) if cat.get("score") is not None else None)
              for name, cat in report.get("categories", {}).items()}  # fmt: skip
    audits = report.get("audits", {})
    metrics = {label: round(audits[audit]["numericValue"], 3) for audit, label in _METRICS.items()
               if isinstance(audits.get(audit, {}).get("numericValue"), int | float)}  # fmt: skip
    opportunities = sorted(
        ({"audit": key, "title": audit.get("title", ""), "savings_ms": round(audit["details"].get("overallSavingsMs", 0))}
         for key, audit in audits.items()
         if isinstance(audit.get("details"), dict) and audit["details"].get("overallSavingsMs", 0) > 100),
        key=lambda item: -item["savings_ms"],
    )[:8]  # fmt: skip
    details = {"url": url, "scores": scores, "metrics": metrics, "opportunities": opportunities,
               "lighthouse_version": report.get("lighthouseVersion"), "form_factor": "mobile emulation, simulated throttling",
               "note": "Single lab run on this PC; scores vary between runs and are not field data. INP cannot be measured in a lab run."}  # fmt: skip
    evidence = [EvidenceDraft("tool_output", "Lighthouse summary", json.dumps(details, indent=2))]
    findings = []
    performance = scores.get("performance")
    if performance is not None and performance < 50:
        top = "\n".join(f"- {o['title']} (~{o['savings_ms']} ms)" for o in opportunities) or "No specific opportunity reported."
        findings.append(FindingDraft(
            key=f"lighthouse:performance:{spec.get('path', '/')}", category=Category.PERFORMANCE, severity=Severity.MEDIUM,
            title=f"Low Lighthouse performance score ({performance}/100) on {spec.get('path', '/')}",
            description=f"Metrics: {json.dumps(metrics)}\n\nLargest opportunities:\n{top}",
            source_tool="lighthouse", affected_component=url, confidence=0.6,
            reproduction_steps=[f"Run `npx lighthouse {url} --only-categories=performance`."],
            remediation="Address the listed opportunities, starting with the largest estimated saving.",
            facts={"kind": "lighthouse", "scores": scores, "metrics": metrics}, evidence=evidence,
            classification=Classification.POTENTIAL,
        ))  # fmt: skip
    if performance is None:
        return CaseOutcome.blocked("Lighthouse returned no performance score for this page")
    verdict = ResultStatus.FAILED if findings else ResultStatus.PASSED
    summary = ", ".join(f"{name} {score}" for name, score in scores.items())
    return CaseOutcome(verdict, f"Lighthouse scores: {summary}", details, evidence, findings)


# ---------------------------------------------------------------- k6


_K6_SCRIPT = """import http from 'k6/http';
import { sleep } from 'k6';
export const options = { vus: %(vus)d, duration: '%(duration)ds' };
export default function () { http.get('%(url)s'); sleep(0.2); }
export function handleSummary(data) { return { stdout: JSON.stringify(data.metrics) }; }
"""


def load_test(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    if blocked := _authorised(ctx):
        return blocked
    assert ctx.target_url
    if not is_local_target(ctx.target_url) and not spec.get("external_load_authorised"):
        return CaseOutcome.blocked(
            "load testing an external system needs explicit authorisation and scope; only local targets run by default"
        )
    if not sandbox.docker_available() or not sandbox.image_present(K6_IMAGE):
        return CaseOutcome.blocked(f"k6 image {K6_IMAGE} is not available (scripts\\install-phase2-tools.ps1)")
    url = ctx.target_url.rstrip("/") + "/" + str(spec.get("path", "/")).lstrip("/")
    vus, duration = ctx.settings.load_test_vus, ctx.settings.load_test_duration_s
    work = get_settings().cache_dir / "k6" / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        (work / "script.js").write_text(
            _K6_SCRIPT % {"vus": vus, "duration": duration, "url": container_url(url)}, encoding="utf-8"
        )
        result = sandbox.run_container(ContainerSpec(
            K6_IMAGE, ["run", "--quiet", "--no-usage-report", "/job/script.js"], [Mount(str(work), "/job")], network=True,
            user="12345:12345", timeout_s=duration + 90, limits=Limits.from_settings(ctx.settings, memory_mb=512),
        ))  # fmt: skip
    finally:
        shutil.rmtree(work, ignore_errors=True)
    try:
        metrics = json.loads(result.stdout[result.stdout.index("{") :])
    except ValueError:
        return CaseOutcome.blocked(f"k6 did not complete (exit {result.exit_code}): {result.output[-300:]}")
    duration_values = metrics.get("http_req_duration", {}).get("values", {})
    requests = metrics.get("http_reqs", {}).get("values", {})
    failed_rate = metrics.get("http_req_failed", {}).get("values", {}).get("rate", 0)
    p95 = duration_values.get("p(95)", 0)
    details = {"url": url, "virtual_users": vus, "duration_s": duration, "requests": int(requests.get("count", 0)),
               "requests_per_second": round(requests.get("rate", 0), 1), "error_rate": round(failed_rate, 4),
               "avg_ms": round(duration_values.get("avg", 0), 1), "p95_ms": round(p95, 1), "max_ms": round(duration_values.get("max", 0), 1),
               "threshold_p95_ms": ctx.settings.perf_slow_threshold_ms,
               "note": "Small bounded load from this PC through the Docker gateway; not a capacity test."}  # fmt: skip
    evidence = [EvidenceDraft("tool_output", "k6 summary", json.dumps(details, indent=2))]
    problems = []
    if failed_rate > 0.01:
        problems.append(f"{failed_rate:.1%} of requests failed")
    if p95 > ctx.settings.perf_slow_threshold_ms:
        problems.append(f"p95 {p95:.0f} ms exceeds {ctx.settings.perf_slow_threshold_ms} ms")
    if not problems:
        return CaseOutcome(ResultStatus.PASSED, f"{details['requests']} requests at {vus} VUs: p95 {p95:.0f} ms, "
                                                f"{failed_rate:.1%} errors", details, evidence)  # fmt: skip
    return CaseOutcome(
        ResultStatus.FAILED, "; ".join(problems), details, evidence,
        [FindingDraft(key=f"load:{spec.get('path', '/')}", category=Category.PERFORMANCE, severity=Severity.MEDIUM,
                      title=f"Degrades under light load ({vus} concurrent users): {'; '.join(problems)}",
                      description=json.dumps(details, indent=2), source_tool="k6", affected_component=url, confidence=0.7,
                      reproduction_steps=[f"Run k6 with {vus} VUs for {duration}s against {url}."],
                      remediation="Profile the endpoint under concurrency: connection pool size, blocking I/O, missing caching.",
                      facts={"kind": "load", **details}, evidence=evidence, classification=Classification.CONFIRMED)],
    )  # fmt: skip


# ---------------------------------------------------------------- OWASP ZAP (passive baseline)


def zap_baseline(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    """Spider + passive rules only. No active attack requests are sent."""
    if blocked := _authorised(ctx):
        return blocked
    assert ctx.target_url
    if not is_local_target(ctx.target_url) and not spec.get("external_scan_authorised"):
        return CaseOutcome.blocked("scanning an external system needs explicit authorisation; only local targets run by default")
    if not sandbox.docker_available() or not sandbox.image_present(ZAP_IMAGE):
        return CaseOutcome.blocked(f"OWASP ZAP image {ZAP_IMAGE} is not available (scripts\\install-phase2-tools.ps1)")
    if (free := _docker_memory_gb()) is not None and free < 2.5:
        return CaseOutcome.blocked(f"OWASP ZAP needs about 2 GB of container memory; the Docker VM has {free:.1f} GB in total")
    work = get_settings().cache_dir / "zap" / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        result = sandbox.run_container(ContainerSpec(
            ZAP_IMAGE, ["zap-baseline.py", "-t", container_url(ctx.target_url), "-J", "report.json", "-m", "1", "-T", "5", "-I"],
            [Mount(str(work), "/zap/wrk", read_only=False)], network=True, user="0:0", read_only_root=False,
            timeout_s=ctx.settings.scanner_timeout_s, limits=Limits(cpus=2.0, memory_mb=2048, pids_limit=512, tmpfs_mb=256),
        ))  # fmt: skip
        report_file = work / "report.json"
        report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.is_file() else None
    except ValueError:
        report = None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if report is None:
        return CaseOutcome.blocked(f"OWASP ZAP did not produce a report (exit {result.exit_code}): {result.output[-300:]}")
    findings = []
    for site in report.get("site", []):
        for alert in site.get("alerts", []):
            severity = _ZAP_RISK.get(str(alert.get("riskcode", "0")), Severity.INFO)
            if severity is Severity.INFO:
                continue
            urls = [str(i.get("uri", "")).replace("host.docker.internal", urlsplit(ctx.target_url).hostname or "")
                    for i in alert.get("instances", [])][:8]  # fmt: skip
            findings.append(FindingDraft(
                key=f"zap:{alert.get('pluginid')}", category=Category.SECURITY, severity=severity,
                title=f"{alert.get('name', 'ZAP alert')} (passive scan)"[:400],
                description=_TAG.sub("", str(alert.get("desc", "")))[:1500] + "\n\nSeen on:\n" + "\n".join(f"- {u}" for u in urls),
                source_tool="owasp-zap", affected_component=urls[0] if urls else ctx.target_url,
                confidence={"3": 0.8, "2": 0.6, "1": 0.4}.get(str(alert.get("confidence", "1")), 0.4),
                reproduction_steps=[f"Run `zap-baseline.py -t {ctx.target_url}` and open alert {alert.get('pluginid')}."],
                remediation=_TAG.sub("", str(alert.get("solution", "")))[:800],
                facts={"kind": "dast", "plugin": alert.get("pluginid"), "cwe": alert.get("cweid"), "count": alert.get("count")},
                classification=Classification.POTENTIAL,
            ))  # fmt: skip
    details = {"alerts_reported": len(findings), "mode": "spider (1 minute) + passive rules; no active attacks",
               "tool": ZAP_IMAGE}  # fmt: skip
    verdict = ResultStatus.FAILED if findings else ResultStatus.PASSED
    return CaseOutcome(verdict, f"{len(findings)} passive alert(s) of low risk or higher", details, findings=findings)


def _docker_memory_gb() -> float | None:
    try:
        output = sandbox._docker(["info", "--format", "{{.MemTotal}}"], timeout=15).stdout.strip()
        return int(output) / (1024**3)
    except (ValueError, OSError, sandbox.SandboxUnavailable):
        return None
