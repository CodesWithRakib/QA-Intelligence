"""Runs a project's own commands (tests, type-check, lint, build) inside the sandbox.

One *workbench* per component and run: the workspace is copied into a throw-away volume and
dependencies are installed once (network enabled); every step then runs with networking disabled.
The stored project version is never modified, and nothing runs on the host.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain import Category, Classification, ResultStatus, Severity
from app.engines import sandbox
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft
from app.engines.sandbox import ContainerSpec, Limits, Mount
from app.services.settings import ExecutionSettings

_PYTEST_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_PYTEST_COUNTS = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped)")
_VITEST_FAILED = re.compile(r"^\s*(?:FAIL|×|✗)\s+(.+?)\s*$", re.MULTILINE)
_JS_COUNTS = re.compile(r"Tests?:?\s+(?:(\d+) failed[ ,|]+)?(?:(\d+) skipped[ ,|]+)?(\d+) passed", re.IGNORECASE)
_PM_SPEC = re.compile(r"^(pnpm|npm|yarn)@(\d+\.\d+\.\d+)")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Output that means "the step needs a service or secret that the sandbox does not provide".
_ENVIRONMENT_MARKERS = re.compile(
    r"ECONNREFUSED|ENOTFOUND|EAI_AGAIN|getaddrinfo|connect ETIMEDOUT|Connection refused|could not connect to server"
    r"|DATABASE_URL|Missing required environment|Invalid environment variables|is not defined in the environment"
    r"|Redis connection|ERR_SOCKET_CONNECTION_TIMEOUT|fetch failed"
    # Sandbox tooling problems: never evidence of a defect in the project.
    r"|Unable to find package manager binary|cannot find binary path|ERR_PNPM_IGNORED_BUILDS|EROFS: read-only file system"
    r"|ENOSPC|Cannot find native binding|Failed to load native binding|not found: (pnpm|npm|yarn|turbo)",
    re.IGNORECASE,
)
PYTHON_INSTALL = (
    "if [ -f requirements.txt ]; then pip install --no-cache-dir --disable-pip-version-check -q "
    "--target /work/.qai-deps -r requirements.txt; fi; "
    "pip install --no-cache-dir --disable-pip-version-check -q --target /work/.qai-deps pytest"
)
PYTHON_TEST = "python -m pytest -q -rfE --tb=short -p no:cacheprovider"
_ENV = {
    "python": {"PYTHONPATH": "/work/.qai-deps", "PYTHONDONTWRITEBYTECODE": "1", "PIP_CACHE_DIR": "/tmp/pip"},
    "node": {
        "CI": "1", "npm_config_cache": "/tmp/npm", "npm_config_update_notifier": "false", "NEXT_TELEMETRY_DISABLED": "1",
        "TURBO_TELEMETRY_DISABLED": "1", "DO_NOT_TRACK": "1", "NODE_OPTIONS": "--max-old-space-size=2048",
        "FORCE_COLOR": "0",
        # Build orchestrators (turbo, nx, lerna) spawn the package manager by name: it must be on PATH.
        "PATH": "/work/.qai-pm/node_modules/.bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    },
}  # fmt: skip
# pnpm refuses to finish when a dependency has a build script the project has not approved. In the sandbox that
# must not block the run: unapproved scripts stay un-run (safer) and are listed in the result notes instead.
PNPM_FLAGS = "--config.strict-dep-builds=false"
_IGNORED_BUILDS = re.compile(r"Ignored build scripts: (.+)")
# Node monorepos (Next.js / NestJS builds, turbo) need far more than the default sandbox limits.
NODE_LIMITS = {"memory_mb": 2560, "pids_limit": 1024, "tmpfs_mb": 512}
STEP_CATEGORY = {"test": Category.FUNCTIONAL, "typecheck": Category.BUILD, "lint": Category.BUILD, "build": Category.BUILD}


@dataclass
class Workbench:
    """A prepared sandbox volume with dependencies installed."""

    volume: str
    image: str
    runtime: str
    workdir: str
    limits: Limits
    package_manager: str = ""
    install_output: str = ""
    notes: list[str] = field(default_factory=list)

    def run(self, command: str, timeout_s: int) -> sandbox.ContainerResult:
        return sandbox.run_container(
            ContainerSpec(self.image, ["sh", "-c", command], [Mount(self.volume, "/work", read_only=False, volume=True)],
                          network=False, workdir=self.workdir, env=_ENV[self.runtime], timeout_s=timeout_s, limits=self.limits)
        )  # fmt: skip


class WorkbenchUnavailable(Exception):
    """The sandbox could not be prepared; the reason is reported as a blocked result."""

    def __init__(self, reason: str, output: str = "") -> None:
        super().__init__(reason)
        self.output = output


def _node_install(package_manager_spec: str, has_lockfile: bool) -> tuple[str, str, list[str]]:
    """Return (install command, package manager command, notes). Versions come from the manifest only."""
    match = _PM_SPEC.match(package_manager_spec or "")
    name, version = (match.group(1), match.group(2)) if match else ("npm", "")
    notes: list[str] = []
    if name == "pnpm":
        pm = "/work/.qai-pm/node_modules/.bin/pnpm"
        # pnpm is installed from the npm registry at the exact version the project declares.
        install = (
            f"npm install --no-audit --no-fund --loglevel=error --prefix /work/.qai-pm pnpm@{version} && "
            f"({pm} install --frozen-lockfile {PNPM_FLAGS} || (echo '--- QAI: frozen lockfile install failed, retrying without ---' && "
            f"{pm} install --no-frozen-lockfile {PNPM_FLAGS}))"
        )
        return install, pm, notes
    if name == "yarn":
        raise WorkbenchUnavailable("yarn workspaces are not supported by the sandbox runner yet")
    if not has_lockfile:
        notes.append("no lockfile: dependency versions are resolved at install time and are not reproducible")
    return "(npm ci --no-audit --no-fund || npm install --no-audit --no-fund)", "npm", notes


def workbench(ctx: EngineContext, component: str, runtime: str, node: dict[str, Any] | None = None) -> Workbench:
    """Prepare (or reuse within this run) the sandbox for one component."""
    key = f"workbench:{runtime}:{component}"
    cached = ctx.run_cache.get(key)
    if isinstance(cached, Workbench):
        return cached
    if isinstance(cached, WorkbenchUnavailable):
        raise cached
    try:
        bench = _prepare(ctx.workspace, component, runtime, ctx.settings, node or {})
    except WorkbenchUnavailable as exc:
        ctx.run_cache[key] = exc  # do not retry a failed install for every step of the run
        raise
    ctx.run_cache[key] = bench
    return bench


def _prepare(
    workspace: Path | None, component: str, runtime: str, settings: ExecutionSettings, node: dict[str, Any]
) -> Workbench:
    if workspace is None:
        raise WorkbenchUnavailable("project has no source files")
    image = sandbox.RUNTIME_IMAGES.get(runtime)
    if not image:
        raise WorkbenchUnavailable(f"no sandbox runtime for '{runtime}'")
    if not sandbox.docker_available():
        raise WorkbenchUnavailable("Docker is not running")
    if not sandbox.image_present(image):
        raise WorkbenchUnavailable(f"sandbox image {image} is not pulled (docker pull {image})")
    limits = Limits.from_settings(settings)
    notes: list[str] = []
    if runtime == "node":
        limits = Limits(cpus=max(settings.sandbox_cpus, 2.0), **NODE_LIMITS)
        install, pm, notes = _node_install(str(node.get("package_manager_spec", "")), bool(node.get("has_lockfile")))
    else:
        install, pm = PYTHON_INSTALL, ""
    try:
        volume = sandbox.prepare_work_volume(workspace, image)
    except sandbox.SandboxUnavailable as exc:
        raise WorkbenchUnavailable(str(exc)) from exc
    workdir = f"/work/{component}".rstrip("/")
    result = sandbox.run_container(
        ContainerSpec(image, ["sh", "-c", install], [Mount(volume, "/work", read_only=False, volume=True)], network=True,
                      workdir=workdir, env=_ENV[runtime], timeout_s=settings.sandbox_install_timeout_s, limits=limits)
    )  # fmt: skip
    output = _ANSI.sub("", result.output)
    if result.exit_code != 0:
        sandbox.remove_volume(volume)
        reason = f"timed out after {settings.sandbox_install_timeout_s}s" if result.timed_out else f"exit {result.exit_code}"
        raise WorkbenchUnavailable(f"dependency installation failed ({reason})", output)
    if ignored := sorted({name.strip(" .") for line in _IGNORED_BUILDS.findall(output) for name in line.split(",")}):
        notes.append(f"dependency build scripts not approved by the project were not run: {', '.join(ignored)[:300]}")
    if "QAI: frozen lockfile install failed" in output:
        notes.append("the lockfile is out of date with package.json: installed without --frozen-lockfile")
    return Workbench(volume, image, runtime, workdir, limits, pm, output[-6000:], notes)


def cleanup(ctx: EngineContext) -> None:
    """Remove every sandbox volume created during the run."""
    for key in [k for k in ctx.run_cache if k.startswith("workbench:")]:
        bench = ctx.run_cache.pop(key)
        if isinstance(bench, Workbench):
            sandbox.remove_volume(bench.volume)


# ---------------------------------------------------------------- python test suite (also used by repair)


@dataclass
class TestSuiteResult:
    status: ResultStatus
    message: str
    passed: int = 0
    failed: int = 0
    failing_tests: list[str] = field(default_factory=list)
    output: str = ""
    install_output: str = ""


def run_suite(workspace: Path, component: str, runtime: str, settings: ExecutionSettings) -> TestSuiteResult:
    """Execute one Python component's pytest suite in a fresh workbench. Inability to run is BLOCKED."""
    if runtime != "python":
        return TestSuiteResult(ResultStatus.BLOCKED, f"regression runner supports python only (got '{runtime}')")
    try:
        bench = _prepare(workspace, component, runtime, settings, {})
    except WorkbenchUnavailable as exc:
        return TestSuiteResult(ResultStatus.BLOCKED, str(exc), install_output=exc.output)
    try:
        test = bench.run(PYTHON_TEST, settings.sandbox_test_timeout_s)
    except sandbox.SandboxUnavailable as exc:
        return TestSuiteResult(ResultStatus.BLOCKED, str(exc))
    finally:
        sandbox.remove_volume(bench.volume)
    return _pytest_result(test, settings, bench.install_output)


def _pytest_result(test: sandbox.ContainerResult, settings: ExecutionSettings, install_output: str) -> TestSuiteResult:
    result = TestSuiteResult(ResultStatus.FAILED, "", output=test.output, install_output=install_output)
    if test.timed_out:
        result.status, result.message = ResultStatus.BLOCKED, f"tests timed out after {settings.sandbox_test_timeout_s}s"
        return result
    counts = {kind.rstrip("s"): int(n) for n, kind in _PYTEST_COUNTS.findall(test.stdout[-2000:])}
    result.passed = counts.get("passed", 0)
    result.failed = counts.get("failed", 0) + counts.get("error", 0)
    result.failing_tests = sorted(set(_PYTEST_FAILED.findall(test.stdout)))
    if test.exit_code == 5:
        result.status, result.message = ResultStatus.NOT_APPLICABLE, "pytest collected no tests"
    elif test.exit_code not in (0, 1):
        result.status, result.message = ResultStatus.BLOCKED, f"pytest could not run the suite (exit {test.exit_code})"
    elif test.exit_code == 0:
        result.status, result.message = ResultStatus.PASSED, f"{result.passed or 'all'} tests passed"
    else:
        result.failed = result.failed or 1
        result.message = f"{result.failed} failed, {result.passed} passed"
    return result


# ---------------------------------------------------------------- engine entry point


def _isolation(bench: Workbench) -> str:
    return (f"docker sandbox ({bench.image}): non-root, no capabilities, read-only root, "
            f"{bench.limits.memory_mb} MB / {bench.limits.cpus} CPU, step executed with network disabled")  # fmt: skip


def _python_case(ctx: EngineContext, bench: Workbench, component: str) -> CaseOutcome:
    suite = _pytest_result(bench.run(PYTHON_TEST, ctx.settings.sandbox_test_timeout_s), ctx.settings, bench.install_output)
    evidence = [EvidenceDraft("tool_output", "test output (python)", suite.output or suite.install_output)]
    outcome = CaseOutcome(suite.status, suite.message, evidence=evidence,
                          details={"passed": suite.passed, "failed": suite.failed, "failing_tests": suite.failing_tests,
                                   "isolation": _isolation(bench)})  # fmt: skip
    if suite.status is ResultStatus.FAILED:
        outcome.findings.append(FindingDraft(
            key=f"tests:{component}:{','.join(suite.failing_tests) or 'suite'}", category=Category.FUNCTIONAL,
            severity=Severity.HIGH, title=f"{suite.failed} failing test(s) in {component or 'project root'}",
            description="The project's own automated tests fail in a clean sandbox.\n\nFailing tests:\n"
                        + "\n".join(f"- {t}" for t in suite.failing_tests[:50]),
            source_tool="project_tests:python", affected_component=component or ".", confidence=0.95,
            reproduction_steps=[f"Open the component directory '{component or '.'}'.",
                                "Install the declared dependencies in a clean environment.", f"Run `{PYTHON_TEST}`."],
            remediation="Fix the implementation so the failing assertions hold (do not weaken the tests).",
            facts={"kind": "test_failure", "component": component, "runtime": "python",
                   "failing_tests": suite.failing_tests, "output_tail": suite.output[-6000:]},
            evidence=evidence, classification=Classification.CONFIRMED,
            expected="All tests pass.", actual=suite.message,
        ))  # fmt: skip
    return outcome


def _node_case(ctx: EngineContext, bench: Workbench, component: str, step: str, script: str) -> CaseOutcome:
    command = f"{bench.package_manager} run {script}"
    timeout = ctx.settings.sandbox_test_timeout_s * (3 if step == "build" else 2)
    result = bench.run(command, timeout)
    output = _ANSI.sub("", result.output)
    evidence = [EvidenceDraft("tool_output", f"`{script}` output", output[-50_000:])]
    details: dict[str, Any] = {"command": f"{bench.package_manager.rsplit('/', 1)[-1]} run {script}", "exit_code": result.exit_code,
                               "duration_ms": result.duration_ms, "isolation": _isolation(bench), "notes": bench.notes}  # fmt: skip
    counts = _JS_COUNTS.findall(output[-4000:])
    if counts:
        failed, _skipped, passed = counts[-1]
        details |= {"tests_failed": int(failed or 0), "tests_passed": int(passed)}
    if result.timed_out:
        return CaseOutcome(ResultStatus.BLOCKED, f"`{script}` timed out after {timeout}s in the sandbox", details, evidence)
    if result.exit_code == 137:
        return CaseOutcome(ResultStatus.BLOCKED, f"`{script}` was killed: it needs more than the sandbox memory limit "
                                                 f"({bench.limits.memory_mb} MB)", details, evidence)  # fmt: skip
    if result.exit_code == 0:
        return CaseOutcome(ResultStatus.PASSED, f"`{script}` succeeded", details, evidence)
    tail = "\n".join(output.strip().splitlines()[-40:])
    failing = sorted(set(_VITEST_FAILED.findall(output)))[:40]
    environment = bool(_ENVIRONMENT_MARKERS.search(output))
    details |= {"failing": failing, "environment_dependent": environment}
    title = f"`{script}` fails in a clean sandbox" + (f" ({component})" if component else "")
    finding = FindingDraft(
        key=f"step:{component}:{step}", category=STEP_CATEGORY.get(step, Category.BUILD),
        severity=Severity.HIGH if step in ("build", "test") else Severity.MEDIUM, title=title,
        description=f"Exit code {result.exit_code}.\n\nLast lines of output:\n{tail}"
                    + ("\n\nFailing entries:\n" + "\n".join(f"- {f}" for f in failing) if failing else ""),
        source_tool=f"project_pipeline:{step}", affected_component=component or ".",
        reproduction_steps=["Clone the repository into a clean directory (no local .env files).",
                            "Install dependencies with the declared package manager.", f"Run `{details['command']}`."],
        remediation="Fix the reported errors; do not suppress or delete the failing checks.",
        facts={"kind": "pipeline_step", "step": step, "script": script, "exit_code": result.exit_code, "failing": failing},
        evidence=evidence, expected=f"`{details['command']}` exits with code 0.", actual=f"exit code {result.exit_code}",
    )  # fmt: skip
    if environment:
        # The step reached for a database/service/secret that the isolated sandbox does not provide: this is
        # not evidence of a defect in the project, and is reported as blocked rather than failed.
        finding.classification, finding.severity, finding.confidence = Classification.ENVIRONMENT, Severity.INFO, 0.5
        finding.title = f"`{script}` needs services or configuration that the sandbox does not provide"
        finding.remediation = "Provide an isolated test environment (database/Redis/test secrets) to run this step."
        return CaseOutcome(ResultStatus.BLOCKED, "step depends on external services or configuration (see output)",
                           details, evidence, [finding])  # fmt: skip
    finding.classification, finding.confidence = Classification.CONFIRMED, 0.9
    return CaseOutcome(ResultStatus.FAILED, f"`{script}` exited with code {result.exit_code}", details, evidence, [finding])


def run_case(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    if not ctx.workspace:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "project has no source files")
    component, runtime = str(spec.get("component", "")), str(spec.get("runtime", ""))
    try:
        bench = workbench(ctx, component, runtime, spec.get("node"))
    except WorkbenchUnavailable as exc:
        evidence = [EvidenceDraft("tool_output", "dependency installation output", exc.output[-50_000:])] if exc.output else []
        return CaseOutcome(ResultStatus.BLOCKED, str(exc), evidence=evidence)
    try:
        if runtime == "python":
            return _python_case(ctx, bench, component)
        return _node_case(ctx, bench, component, str(spec.get("step", "test")), str(spec.get("script", "test")))
    except sandbox.SandboxUnavailable as exc:
        return CaseOutcome.blocked(str(exc))
