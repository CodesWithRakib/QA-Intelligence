"""Static security checks: Trivy (dependencies, secrets, config), Semgrep (SAST), built-in secrets.

Scanners are trusted tools that only *read* the untrusted workspace (mounted read-only). They still
run in containers with dropped capabilities and resource limits.
"""

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from app.config import get_settings
from app.domain import Category, ResultStatus, Severity
from app.engines import sandbox
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft, mask_secret
from app.engines.sandbox import ContainerSpec, Limits, Mount
from app.security.archive import EXCLUDED_DIRS
from app.services.settings import ExecutionSettings

TRIVY_IMAGE = "aquasec/trivy:latest"
SEMGREP_IMAGE = "semgrep/semgrep:latest"
GITLEAKS_IMAGE = "zricethezav/gitleaks:latest"
TRIVY_CACHE_VOLUME = "qai-trivy-cache"
SCANNER_MEMORY_MB = 1536
_SEVERITY = {
    "CRITICAL": Severity.CRITICAL, "HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM, "LOW": Severity.LOW,
    "ERROR": Severity.HIGH, "WARNING": Severity.MEDIUM, "INFO": Severity.LOW, "UNKNOWN": Severity.INFO,
}  # fmt: skip
_ORDER = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
_HARDCODED = re.compile(
    r"""(?ix)\b([a-z0-9_]*(?:password|passwd|secret|api_?key|access_?token|private_?key)[a-z0-9_]*)
        \s*[:=]\s*["']([^"'\s]{8,})["']"""
)
_PLACEHOLDER = re.compile(r"(?i)(change[-_]?me|example|placeholder|your[-_]|xxx|<.+>|\$\{|\{\{|dummy|test)")
_BUILTIN_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".java",
    ".rb",
    ".php",
    ".cs",
    ".yml",
    ".yaml",
}


def _scanner_spec(image: str, command: list[str], workspace: Path, settings: ExecutionSettings,
                  extra: list[Mount] | None = None, env: dict[str, str] | None = None) -> ContainerSpec:  # fmt: skip
    return ContainerSpec(
        image=image, command=command, user="0:0", network=True,  # network: vulnerability DB / rule download
        mounts=[Mount(str(workspace), "/src"), *(extra or [])],
        timeout_s=settings.scanner_timeout_s,
        env=env or {},
        limits=Limits.from_settings(settings, memory_mb=SCANNER_MEMORY_MB),
    )  # fmt: skip


def _precheck(ctx: EngineContext, image: str) -> CaseOutcome | None:
    if not ctx.workspace:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "project has no source files")
    if not sandbox.docker_available():
        return CaseOutcome.blocked("Docker is not running; scanner could not be executed")
    if not sandbox.image_present(image):
        return CaseOutcome.blocked(f"scanner image {image} is not pulled (docker pull {image})")
    return None


def run_trivy(workspace: Path, scanners: str, settings: ExecutionSettings) -> tuple[dict[str, Any] | None, str]:
    """Return (parsed report, raw output). The report is None when the scan did not complete."""
    result = sandbox.run_container(
        _scanner_spec(
            TRIVY_IMAGE,
            ["fs", "--cache-dir", "/cache", "--format", "json", "--quiet", "--scanners", scanners, "/src"],
            workspace,
            settings,
            [Mount(TRIVY_CACHE_VOLUME, "/cache", read_only=False, volume=True)],
            # The vulnerability DB (hundreds of MB) is unpacked via TMPDIR: keep it on the cache volume,
            # not on the small in-memory /tmp.
            {"TMPDIR": "/cache"},
        )  # fmt: skip
    )
    if result.exit_code != 0:
        return None, result.output
    try:
        return json.loads(result.stdout), result.stdout
    except json.JSONDecodeError:
        return None, result.output


def _best_fix(installed: str, fixed_candidates: list[str]) -> str | None:
    """Smallest published fix that is newer than the installed version."""
    try:
        current = Version(installed)
    except InvalidVersion:
        return None
    newer = []
    for candidate in fixed_candidates:
        try:
            version = Version(candidate.strip())
        except InvalidVersion:
            continue
        if version > current:
            newer.append(version)
    return str(min(newer)) if newer else None


def vulnerable_packages(report: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Group Trivy vulnerabilities by (manifest file, package)."""
    packages: dict[tuple[str, str], dict[str, Any]] = {}
    for target in report.get("Results") or []:
        for vuln in target.get("Vulnerabilities") or []:
            key = (str(target.get("Target", "")), str(vuln.get("PkgName", "")))
            entry = packages.setdefault(key, {
                "manifest": key[0], "package": key[1], "ecosystem": target.get("Type", ""),
                "installed_version": vuln.get("InstalledVersion", ""), "advisories": [], "fixes": [],
            })  # fmt: skip
            entry["advisories"].append({
                "id": vuln.get("VulnerabilityID", ""), "severity": vuln.get("Severity", "UNKNOWN"),
                "title": str(vuln.get("Title", ""))[:300], "fixed_version": vuln.get("FixedVersion", ""),
                "url": vuln.get("PrimaryURL", ""),
            })  # fmt: skip
            fix = _best_fix(entry["installed_version"], str(vuln.get("FixedVersion", "")).split(","))
            if fix:
                entry["fixes"].append(fix)
    for entry in packages.values():
        # The version that resolves every advisory is the highest of the per-advisory minimum fixes.
        fixes = entry.pop("fixes")
        entry["fixed_version"] = str(max(map(Version, fixes))) if fixes else None
        entry["fully_fixable"] = len(fixes) == len(entry["advisories"])
    return packages


def dependency_scan(ctx: EngineContext, _spec: dict[str, Any]) -> CaseOutcome:
    if blocked := _precheck(ctx, TRIVY_IMAGE):
        return blocked
    assert ctx.workspace
    report, raw = run_trivy(ctx.workspace, "vuln", ctx.settings)
    if report is None:
        return CaseOutcome.blocked(f"Trivy did not complete: {raw[-400:]}")
    scanned = [str(t.get("Target")) for t in report.get("Results") or []]
    packages = vulnerable_packages(report)
    findings = []
    for entry in packages.values():
        worst = max((_SEVERITY.get(a["severity"], Severity.INFO) for a in entry["advisories"]), key=_ORDER.index)
        ids = [a["id"] for a in entry["advisories"]]
        fix = entry["fixed_version"]
        findings.append(
            FindingDraft(
                key=f"{entry['manifest']}:{entry['package']}",
                category=Category.SECURITY,
                severity=worst,
                title=f"{entry['package']} {entry['installed_version']} has {len(ids)} known vulnerabilit"
                + ("y" if len(ids) == 1 else "ies"),
                description="\n".join(f"- {a['id']} ({a['severity']}): {a['title']} {a['url']}" for a in entry["advisories"]),
                source_tool="trivy",
                reproduction_steps=[f"Run `trivy fs --scanners vuln .` in the project and inspect {entry['manifest']}."],
                affected_component=entry["manifest"],
                confidence=0.9,
                remediation=f"Upgrade {entry['package']} to {fix} or later." if fix else "No fixed version is published yet.",
                facts={"kind": "vulnerable_dependency", **entry},
                evidence=[EvidenceDraft("tool_output", "trivy advisories", json.dumps(entry, indent=2))],
            )
        )
    if not scanned:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "Trivy found no supported dependency manifest or lockfile")
    status = ResultStatus.FAILED if findings else ResultStatus.PASSED
    message = f"{len(findings)} vulnerable package(s) in {len(scanned)} manifest(s)"
    return CaseOutcome(status, message, {"manifests": scanned, "tool": TRIVY_IMAGE}, findings=findings)


def _trivy_static(ctx: EngineContext, scanner: str) -> CaseOutcome:
    if blocked := _precheck(ctx, TRIVY_IMAGE):
        return blocked
    assert ctx.workspace
    report, raw = run_trivy(ctx.workspace, scanner, ctx.settings)
    if report is None:
        return CaseOutcome.blocked(f"Trivy did not complete: {raw[-400:]}")
    findings = []
    for target in report.get("Results") or []:
        file = str(target.get("Target", ""))
        for secret in target.get("Secrets") or []:
            line = secret.get("StartLine", 0)
            findings.append(FindingDraft(
                key=f"secret:{file}:{secret.get('RuleID')}:{line}", category=Category.SECURITY,
                severity=_SEVERITY.get(secret.get("Severity", ""), Severity.HIGH),
                title=f"Secret detected: {secret.get('Title', secret.get('RuleID'))}",
                description=f"A credential pattern ({secret.get('Category', 'secret')}) is committed in {file}:{line}.",
                source_tool="trivy-secret", affected_component=f"{file}:{line}", confidence=0.8,
                reproduction_steps=[f"Open {file} at line {line}."],
                remediation="Remove the secret from source, rotate it, and load it from the environment.",
                facts={"kind": "secret", "file": file, "line": line, "rule": secret.get("RuleID")},
            ))  # fmt: skip
        for issue in target.get("Misconfigurations") or []:
            line = (issue.get("CauseMetadata") or {}).get("StartLine", 0)
            findings.append(FindingDraft(
                key=f"config:{file}:{issue.get('ID')}", category=Category.SECURITY,
                severity=_SEVERITY.get(issue.get("Severity", ""), Severity.LOW),
                title=f"{issue.get('ID')}: {issue.get('Title', '')}"[:400],
                description=f"{issue.get('Description', '')}\n\n{issue.get('Message', '')}".strip(),
                source_tool="trivy-config", affected_component=f"{file}:{line}" if line else file, confidence=0.85,
                reproduction_steps=[f"Run `trivy fs --scanners misconfig .` and inspect {file}."],
                remediation=str(issue.get("Resolution", "")),
                facts={"kind": "misconfiguration", "file": file, "rule": issue.get("ID")},
            ))  # fmt: skip
    status = ResultStatus.FAILED if findings else ResultStatus.PASSED
    return CaseOutcome(status, f"{len(findings)} issue(s) reported by trivy ({scanner})", findings=findings)


def _semgrep(ctx: EngineContext) -> CaseOutcome:
    if blocked := _precheck(ctx, SEMGREP_IMAGE):
        return blocked
    assert ctx.workspace
    result = sandbox.run_container(
        _scanner_spec(
            SEMGREP_IMAGE,
            [
                "semgrep",
                "scan",
                "--config",
                "p/default",
                "--json",
                "--quiet",
                "--metrics=off",
                "--timeout",
                "30",
                "--max-memory",
                "1200",
                "/src",
            ],
            ctx.workspace,
            ctx.settings,
        )  # fmt: skip
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return CaseOutcome.blocked(f"Semgrep did not complete (exit {result.exit_code}): {result.output[-400:]}")
    findings = []
    for item in report.get("results") or []:
        extra = item.get("extra") or {}
        file = str(item.get("path", "")).removeprefix("/src/")
        line = (item.get("start") or {}).get("line", 0)
        rule = str(item.get("check_id", ""))
        metadata = extra.get("metadata") or {}
        confidence = {"HIGH": 0.85, "MEDIUM": 0.65, "LOW": 0.4}.get(str(metadata.get("confidence", "")).upper(), 0.6)
        findings.append(FindingDraft(
            key=f"{rule}:{file}:{line}", category=Category.SECURITY,
            severity=_SEVERITY.get(str(extra.get("severity", "")).upper(), Severity.MEDIUM),
            title=f"{rule.split('.')[-1]} in {file}:{line}"[:400],
            description=str(extra.get("message", "")),
            source_tool="semgrep", affected_component=f"{file}:{line}", confidence=confidence,
            reproduction_steps=[f"Run `semgrep scan --config p/default` and open {file} at line {line}."],
            remediation=str(metadata.get("fix", "")) or "Follow the rule guidance: " + str(metadata.get("source", rule)),
            facts={"kind": "sast", "file": file, "line": line, "rule": rule,
                   "cwe": metadata.get("cwe"), "owasp": metadata.get("owasp")},
        ))  # fmt: skip
    errors = [str(e.get("message", ""))[:200] for e in report.get("errors") or []][:10]
    status = ResultStatus.FAILED if findings else ResultStatus.PASSED
    scanned = len((report.get("paths") or {}).get("scanned") or [])
    return CaseOutcome(status, f"{len(findings)} finding(s) in {scanned} scanned file(s)",
                       {"files_scanned": scanned, "tool_errors": errors}, findings=findings)  # fmt: skip


def _builtin_secrets(ctx: EngineContext) -> CaseOutcome:
    """Generic hard-coded credential assignments (complements token-format based scanners)."""
    if not ctx.workspace:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "project has no source files")
    findings, scanned = [], 0
    for path in sorted(ctx.workspace.rglob("*")):
        relative = path.relative_to(ctx.workspace)
        if (path.suffix not in _BUILTIN_SUFFIXES or not path.is_file() or path.stat().st_size > 512 * 1024
                or EXCLUDED_DIRS & set(relative.parts) or "test" in relative.as_posix().lower()):  # fmt: skip
            continue
        scanned += 1
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            match = _HARDCODED.search(line)
            if not match or _PLACEHOLDER.search(match.group(2)):
                continue
            rel = relative.as_posix()
            masked = line.replace(match.group(2), mask_secret(match.group(2))).strip()[:300]
            findings.append(FindingDraft(
                key=f"hardcoded:{rel}:{match.group(1).lower()}", category=Category.SECURITY, severity=Severity.HIGH,
                title=f"Hard-coded credential '{match.group(1)}' in {rel}:{number}",
                description="A credential-like value is assigned as a string literal in source code.",
                source_tool="builtin-secrets", affected_component=f"{rel}:{number}", confidence=0.6,
                reproduction_steps=[f"Open {rel} at line {number}."],
                remediation="Load the value from the environment or a secret store and rotate the exposed value.",
                facts={"kind": "secret", "file": rel, "line": number, "name": match.group(1)},
                evidence=[EvidenceDraft("log", "matched line (value masked)", f"{rel}:{number}: {masked}")],
            ))  # fmt: skip
    status = ResultStatus.FAILED if findings else ResultStatus.PASSED
    return CaseOutcome(status, f"{len(findings)} hard-coded credential(s) in {scanned} scanned file(s)", findings=findings)


def _gitleaks(ctx: EngineContext, history: bool) -> CaseOutcome:
    """Secret scan with gitleaks. Values are redacted by the tool; the container has no network.

    ``history=True`` scans the git history of the original local repository (mounted read-only).
    """
    if blocked := _precheck(ctx, GITLEAKS_IMAGE):
        return blocked
    source = ctx.source_path if history else ctx.workspace
    if history and (source is None or not (source / ".git").exists()):
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "the project was not imported from a local git repository")
    assert source
    work = get_settings().cache_dir / "gitleaks" / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        result = sandbox.run_container(
            ContainerSpec(
                image=GITLEAKS_IMAGE,
                command=["git" if history else "dir", "/src", "--report-format", "json", "--report-path", "/out/report.json",
                         "--redact", "--exit-code", "0", "--no-banner", "--log-level", "error"],
                user="0:0",
                network=False,
                mounts=[Mount(str(source), "/src"), Mount(str(work), "/out", read_only=False)],
                timeout_s=ctx.settings.scanner_timeout_s,
                limits=Limits.from_settings(ctx.settings, memory_mb=1024),
                # The repository belongs to another user id; git refuses to read it without this.
                env={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"},
            )
        )  # fmt: skip
        report = work / "report.json"
        try:
            leaks = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
        except json.JSONDecodeError:
            leaks = None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if leaks is None:
        return CaseOutcome.blocked(f"gitleaks did not complete (exit {result.exit_code}): {result.output[-400:]}")
    findings = []
    for leak in leaks:
        file, line, rule = str(leak.get("File", "")).removeprefix("/src/"), leak.get("StartLine", 0), str(leak.get("RuleID", ""))
        commit = str(leak.get("Commit", ""))[:12]
        where = f"{file}:{line}" + (f" (commit {commit})" if commit else "")
        still_present = bool(commit) and ctx.workspace is not None and (ctx.workspace / file).is_file()
        findings.append(FindingDraft(
            key=f"gitleaks:{rule}:{file}:{leak.get('Fingerprint') or line}", category=Category.SECURITY,
            severity=Severity.HIGH, title=f"Possible secret ({rule}) in {where}"[:400],
            description=f"{leak.get('Description', 'Secret pattern matched')}. The value is redacted here."
                        + (f" Found in git history (commit {commit}, {str(leak.get('Date', ''))[:10]})." if commit else "")
                        + (" The file still exists in the current tree." if still_present else ""),
            source_tool="gitleaks-history" if history else "gitleaks", affected_component=where, confidence=0.7,
            reproduction_steps=[f"Run `gitleaks detect --redact{'' if history else ' --no-git'}` in the repository.",
                                f"Inspect {where}."],
            remediation="If this is a real credential: rotate it first, then remove it from the code"
                        + (" and purge it from git history (git filter-repo)." if history else "."),
            facts={"kind": "secret", "file": file, "line": line, "rule": rule, "commit": commit, "in_history": history},
        ))  # fmt: skip
    status = ResultStatus.FAILED if findings else ResultStatus.PASSED
    scope = "git history" if history else "working tree"
    return CaseOutcome(status, f"{len(findings)} possible secret(s) in the {scope}", {"tool": GITLEAKS_IMAGE}, findings=findings)


def static_analysis(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    tool = spec.get("tool")
    if tool == "gitleaks":
        return _gitleaks(ctx, history=False)
    if tool == "gitleaks-history":
        return _gitleaks(ctx, history=True)
    if tool == "semgrep":
        return _semgrep(ctx)
    if tool == "trivy-secret":
        return _trivy_static(ctx, "secret")
    if tool == "trivy-config":
        return _trivy_static(ctx, "misconfig")
    if tool == "builtin-secrets":
        return _builtin_secrets(ctx)
    return CaseOutcome.blocked(f"unknown static analysis tool '{tool}'")
