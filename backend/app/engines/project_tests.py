"""Runs a project's own test suite inside the sandbox.

Phases: (1) copy the workspace into a throw-away volume, (2) install dependencies with network
access, (3) run the tests with networking disabled. The stored project version is never modified.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.domain import Category, ResultStatus, Severity
from app.engines import sandbox
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft
from app.engines.sandbox import ContainerSpec, Mount
from app.services.settings import ExecutionSettings

_PYTEST_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_PYTEST_COUNTS = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped)")
_INSTALL = {
    "python": (
        "if [ -f requirements.txt ]; then pip install --no-cache-dir --disable-pip-version-check -q "
        "--target /work/.qai-deps -r requirements.txt; fi; "
        "pip install --no-cache-dir --disable-pip-version-check -q --target /work/.qai-deps pytest"
    ),
    "node": "npm ci --ignore-scripts --no-audit --no-fund || npm install --ignore-scripts --no-audit --no-fund",
}
_TEST = {
    "python": "python -m pytest -q -rfE --tb=short -p no:cacheprovider",
    "node": "npm test",
}
_ENV = {
    "python": {"PYTHONPATH": "/work/.qai-deps", "PYTHONDONTWRITEBYTECODE": "1", "PIP_CACHE_DIR": "/tmp/pip"},
    "node": {"CI": "1", "npm_config_cache": "/tmp/npm"},
}


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
    """Execute one component's tests. Any inability to run is BLOCKED, never passed."""
    image = sandbox.RUNTIME_IMAGES.get(runtime)
    if not image:
        return TestSuiteResult(ResultStatus.BLOCKED, f"no sandbox runtime for '{runtime}'")
    if not sandbox.image_present(image):
        return TestSuiteResult(ResultStatus.BLOCKED, f"sandbox image {image} is not pulled (docker pull {image})")
    workdir = f"/work/{component}".rstrip("/")
    limits = sandbox.Limits.from_settings(settings)
    try:
        volume = sandbox.prepare_work_volume(workspace, image)
    except sandbox.SandboxUnavailable as exc:
        return TestSuiteResult(ResultStatus.BLOCKED, str(exc))
    mounts = [Mount(volume, "/work", read_only=False, volume=True)]
    try:
        install = sandbox.run_container(
            ContainerSpec(image, ["sh", "-c", _INSTALL[runtime]], mounts, network=True, workdir=workdir,
                          env=_ENV[runtime], timeout_s=settings.sandbox_install_timeout_s, limits=limits)
        )  # fmt: skip
        if install.exit_code != 0:
            reason = "timed out" if install.timed_out else f"exit {install.exit_code}"
            return TestSuiteResult(ResultStatus.BLOCKED, f"dependency installation failed ({reason})",
                                   install_output=install.output)  # fmt: skip
        test = sandbox.run_container(
            ContainerSpec(image, ["sh", "-c", _TEST[runtime]], mounts, network=False, workdir=workdir,
                          env=_ENV[runtime], timeout_s=settings.sandbox_test_timeout_s, limits=limits)
        )  # fmt: skip
    except sandbox.SandboxUnavailable as exc:
        return TestSuiteResult(ResultStatus.BLOCKED, str(exc))
    finally:
        sandbox.remove_volume(volume)

    result = TestSuiteResult(ResultStatus.FAILED, "", output=test.output, install_output=install.output)
    if test.timed_out:
        result.status, result.message = (
            ResultStatus.BLOCKED,
            f"tests timed out after {settings.sandbox_test_timeout_s}s",
        )
        return result
    if runtime == "python":
        counts = {kind.rstrip("s"): int(n) for n, kind in _PYTEST_COUNTS.findall(test.stdout[-2000:])}
        result.passed = counts.get("passed", 0)
        result.failed = counts.get("failed", 0) + counts.get("error", 0)
        result.failing_tests = sorted(set(_PYTEST_FAILED.findall(test.stdout)))
        if test.exit_code == 5:
            result.status, result.message = ResultStatus.NOT_APPLICABLE, "pytest collected no tests"
            return result
        if test.exit_code not in (0, 1):
            result.status = ResultStatus.BLOCKED
            result.message = f"pytest could not run the suite (exit {test.exit_code})"
            return result
    if test.exit_code == 0:
        result.status, result.message = ResultStatus.PASSED, f"{result.passed or 'all'} tests passed"
    else:
        result.failed = result.failed or 1
        result.message = f"{result.failed} failed, {result.passed} passed"
    return result


def run_case(ctx: EngineContext, spec: dict[str, str]) -> CaseOutcome:
    if not ctx.workspace:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "project has no source files")
    component, runtime = spec.get("component", ""), spec.get("runtime", "")
    suite = run_suite(ctx.workspace, component, runtime, ctx.settings)
    evidence = [EvidenceDraft("tool_output", f"test output ({runtime})", suite.output or suite.install_output)]
    outcome = CaseOutcome(
        suite.status, suite.message, evidence=evidence,
        details={"passed": suite.passed, "failed": suite.failed, "failing_tests": suite.failing_tests,
                 "isolation": "docker sandbox: non-root, no capabilities, tests run with network disabled"},
    )  # fmt: skip
    if suite.status is ResultStatus.FAILED:
        outcome.findings.append(
            FindingDraft(
                key=f"tests:{component}:{','.join(suite.failing_tests) or 'suite'}",
                category=Category.FUNCTIONAL,
                severity=Severity.HIGH,
                title=f"{suite.failed} failing test(s) in {component or 'project root'}",
                description=(
                    "The project's own automated tests fail in a clean sandbox.\n\nFailing tests:\n"
                    + "\n".join(f"- {t}" for t in suite.failing_tests[:50])
                ),
                source_tool=f"project_tests:{runtime}",
                reproduction_steps=[
                    f"Open the component directory '{component or '.'}'.",
                    "Install the declared dependencies in a clean environment.",
                    f"Run `{_TEST[runtime]}`.",
                ],
                affected_component=component or ".",
                confidence=0.95,
                remediation="Fix the implementation so the failing assertions hold (do not weaken the tests).",
                facts={
                    "kind": "test_failure",
                    "component": component,
                    "runtime": runtime,
                    "failing_tests": suite.failing_tests,
                    "output_tail": suite.output[-6000:],
                },
                evidence=evidence,
            )  # fmt: skip
        )
    return outcome
