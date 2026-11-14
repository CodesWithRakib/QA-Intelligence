"""Managed test environment: an isolated, throw-away copy of a project running in containers.

Purpose: dynamic testing (browser, API, accessibility, performance) of a source project without
touching anything real. The environment is built only from the imported workspace, which never
contains the project's `.env` files, plus a profile of NON-SECRET settings:

* backing services (database, cache) from a small allowlist of official images, with generated
  passwords, on a network that exists only for this run;
* application processes started from the already prepared sandbox volume, non-root, capability-less;
* ports published on 127.0.0.1 only, so the host browser and HTTP client can reach the app.

Nothing here can reach the owner's real database or production services unless the project's own
committed code points at them. Residual risk: app containers have outbound network access (they are
on a bridge network so that ports can be published).
"""

import logging
import re
import secrets
import socket
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from app.domain import Category, Classification, ResultStatus, Severity
from app.engines import project_tests, sandbox
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft
from app.engines.sandbox import ContainerSpec, Limits, Mount

log = logging.getLogger(__name__)
CACHE_KEY = "managed_environment"
ERROR_KEY = "managed_environment_error"
# Official database images start as root and drop to their own user: they need exactly these capabilities.
SERVICE_CAPS = ["CHOWN", "SETUID", "SETGID", "FOWNER", "DAC_OVERRIDE"]
ALLOWED_SERVICE_IMAGES = re.compile(r"^(postgres|redis|mysql|mariadb|mongo|valkey/valkey):[\w.-]+$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
_ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_PLACEHOLDER = re.compile(r"\$\{(URL|HOSTPORT|SECRET):([a-z0-9_-]+)\}")
# How to tell that a backing service accepts connections (exec inside its own container).
_READY = {
    "postgres": ["pg_isready", "-q"], "redis": ["redis-cli", "ping"], "valkey/valkey": ["valkey-cli", "ping"],
    "mysql": ["mysqladmin", "ping", "--silent"], "mariadb": ["healthcheck.sh", "--connect"],
    "mongo": ["mongosh", "--quiet", "--eval", "db.runCommand({ping:1}).ok"],
}  # fmt: skip


class ManagedEnvironmentError(Exception):
    """The environment could not be started; the message (with log tail) is shown to the owner."""


class ServiceProfile(BaseModel):
    name: str
    image: str
    env: dict[str, str] = Field(default_factory=dict)
    memory_mb: int = Field(default=384, ge=64, le=1024)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("service names must be lower-case letters, digits and dashes")
        return value

    @field_validator("image")
    @classmethod
    def _image(cls, value: str) -> str:
        if not ALLOWED_SERVICE_IMAGES.match(value):
            raise ValueError("only official postgres, redis, valkey, mysql, mariadb and mongo images are allowed")
        return value


class AppProfile(BaseModel):
    name: str
    command: str = Field(min_length=1, max_length=500)
    port: int = Field(ge=1, le=65535)
    health_path: str = Field(default="/", max_length=200)
    memory_mb: int = Field(default=1024, ge=128, le=3072)
    startup_timeout_s: int = Field(default=240, ge=10, le=900)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("app names must be lower-case letters, digits and dashes")
        return value


class EnvironmentProfile(BaseModel):
    """Non-secret description of how to run the project locally. Stored in the project's test_config."""

    runtime: str = Field(default="node", pattern="^(node|python)$")
    component: str = Field(default="", max_length=200)
    services: list[ServiceProfile] = Field(default_factory=list, max_length=6)
    # Values may use ${URL:<app>} (http://127.0.0.1:<host port>), ${HOSTPORT:<app>} and ${SECRET:<name>} (generated per run).
    env: dict[str, str] = Field(default_factory=dict)
    setup: list[str] = Field(default_factory=list, max_length=10)
    apps: list[AppProfile] = Field(min_length=1, max_length=4)
    target: str  # name of the app that the browser tests open
    setup_timeout_s: int = Field(default=900, ge=30, le=1800)

    @field_validator("env")
    @classmethod
    def _env(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if not _ENV_KEY.match(key):
                raise ValueError(f"invalid environment variable name: {key}")
        return value


@dataclass
class ManagedEnvironment:
    network: str
    containers: list[str] = field(default_factory=list)
    urls: dict[str, str] = field(default_factory=dict)
    target_url: str = ""
    log: list[str] = field(default_factory=list)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolve(value: str, urls: dict[str, str], generated: dict[str, str]) -> str:
    def substitute(match: re.Match[str]) -> str:
        kind, name = match.group(1), match.group(2)
        if kind == "SECRET":
            return generated.setdefault(name, secrets.token_hex(24))
        if name not in urls:
            raise ManagedEnvironmentError(f"placeholder refers to an unknown app '{name}'")
        return urls[name] if kind == "URL" else urls[name].removeprefix("http://")

    return _PLACEHOLDER.sub(substitute, value)


def _wait_service(container: str, image: str) -> bool:
    command = _READY.get(image.split(":")[0])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if not sandbox.service_running(container):
            return False
        if command is None or sandbox.exec_in(container, command) == 0:
            return True
        time.sleep(2)
    return False


def _wait_http(url: str, container: str, timeout_s: int) -> str | None:
    """None when the app answers; otherwise the reason."""
    deadline = time.monotonic() + timeout_s
    last = "no response"
    while time.monotonic() < deadline:
        if not sandbox.service_running(container):
            return "the process exited"
        try:
            status = httpx.get(url, timeout=5, trust_env=False, follow_redirects=False).status_code
            if status < 500:
                return None
            last = f"HTTP {status}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(3)
    return f"not ready after {timeout_s}s ({last})"


def up(ctx: EngineContext, profile: EnvironmentProfile) -> ManagedEnvironment:
    """Start services, run setup commands, start the apps. Raises EnvironmentError with the log tail."""
    cached = ctx.run_cache.get(CACHE_KEY)
    if isinstance(cached, ManagedEnvironment):
        return cached
    try:
        bench = project_tests.workbench(ctx, profile.component, profile.runtime, _node_spec(ctx))
    except project_tests.WorkbenchUnavailable as exc:
        raise ManagedEnvironmentError(f"dependencies could not be installed: {exc}") from exc
    environment = ManagedEnvironment(network=sandbox.create_network())
    ctx.run_cache[CACHE_KEY] = environment
    urls = {app.name: f"http://127.0.0.1:{_free_port()}" for app in profile.apps}
    generated: dict[str, str] = {}
    env = {key: _resolve(value, urls, generated) for key, value in profile.env.items()}
    try:
        for service in profile.services:
            service_env = {key: _resolve(value, urls, generated) for key, value in service.env.items()}
            name = sandbox.start_service(
                ContainerSpec(service.image, [], network_name=environment.network, env=service_env, user="",
                              read_only_root=False, extra_caps=SERVICE_CAPS,
                              limits=Limits(cpus=1.0, memory_mb=service.memory_mb, pids_limit=256)),
                alias=service.name,
            )  # fmt: skip
            environment.containers.append(name)
            if not _wait_service(name, service.image):
                raise ManagedEnvironmentError(f"service '{service.name}' did not become ready:\n{sandbox.service_logs(name, 30)}")
            environment.log.append(f"service {service.name} ({service.image}) ready")
        mounts = [Mount(bench.volume, "/work", read_only=False, volume=True)]
        base_env = {**project_tests._ENV[profile.runtime], **env}
        for command in profile.setup:
            result = sandbox.run_container(ContainerSpec(
                bench.image, ["sh", "-c", _with_pm(command, bench)], mounts, network_name=environment.network, workdir=bench.workdir,
                env=base_env, timeout_s=profile.setup_timeout_s, limits=bench.limits,
            ))  # fmt: skip
            if result.exit_code != 0:
                reason = "timed out" if result.timed_out else f"exit {result.exit_code}"
                raise ManagedEnvironmentError(f"setup command `{command}` failed ({reason}):\n{result.output[-2500:]}")
            environment.log.append(f"setup `{command}` ok ({result.duration_ms // 1000}s)")
        for app in profile.apps:
            host_port = int(urls[app.name].rsplit(":", 1)[1])
            name = sandbox.start_service(
                ContainerSpec(bench.image, ["sh", "-c", _with_pm(app.command, bench)], mounts, network_name=environment.network,
                              workdir=bench.workdir, env={**base_env, "PORT": str(app.port), "HOSTNAME": "0.0.0.0"},  # noqa: S104
                              limits=Limits(cpus=2.0, memory_mb=app.memory_mb, pids_limit=1024, tmpfs_mb=512)),
                alias=app.name, publish=(host_port, app.port),
            )  # fmt: skip
            environment.containers.append(name)
            problem = _wait_http(urls[app.name] + app.health_path, name, app.startup_timeout_s)
            if problem:
                raise ManagedEnvironmentError(f"app '{app.name}' {problem}:\n{sandbox.service_logs(name, 40)}")
            environment.log.append(f"app {app.name} ready at {urls[app.name]}")
    except (ManagedEnvironmentError, sandbox.SandboxUnavailable):
        down(ctx)
        raise
    environment.urls = urls
    environment.target_url = urls[profile.target] if profile.target in urls else next(iter(urls.values()))
    return environment


def _node_spec(ctx: EngineContext) -> dict[str, Any]:
    return dict(ctx.inventory.get("root_node") or {})


def _with_pm(command: str, bench: project_tests.Workbench) -> str:
    """`pnpm ...` in a profile means the project's own pinned package manager inside the sandbox."""
    if bench.package_manager and command.startswith("pnpm "):
        return f"{bench.package_manager} {command.removeprefix('pnpm ')}"
    return command


def down(ctx: EngineContext) -> dict[str, Any]:
    environment = ctx.run_cache.pop(CACHE_KEY, None)
    if not isinstance(environment, ManagedEnvironment):
        return {}
    for container in reversed(environment.containers):
        sandbox.stop_service(container)
    sandbox.remove_network(environment.network)
    return {"containers_removed": len(environment.containers), "network_removed": True}


def start_case(ctx: EngineContext) -> CaseOutcome:
    """Engine entry point: bring the environment up and point the rest of the run at it."""
    try:
        profile = EnvironmentProfile.model_validate(ctx.project_config.get("environment") or {})
    except ValueError as exc:
        return CaseOutcome.blocked(f"the stored environment profile is invalid: {str(exc)[:300]}")
    try:
        environment = up(ctx, profile)
    except (ManagedEnvironmentError, sandbox.SandboxUnavailable) as exc:
        message = str(exc)
        ctx.run_cache[ERROR_KEY] = message.splitlines()[0][:300]
        evidence = [EvidenceDraft("tool_output", "environment start-up log", message)]
        finding = FindingDraft(
            key="environment:start", category=Category.BUILD, severity=Severity.HIGH,
            title="Project does not start from a clean copy with the documented example configuration",
            description=message[:3000], source_tool="managed-environment", affected_component=profile.component or ".",
            confidence=0.6, evidence=evidence,
            reproduction_steps=["Clone the repository into a clean directory.", "Start empty database/cache services.",
                                "Use the committed example configuration and run the setup and start commands in the profile."],
            remediation="Make the documented setup work from a clean checkout, or correct the environment profile.",
            facts={"kind": "environment_start"},
            # May be a project defect (broken migration, missing default) or an incomplete profile: not established.
            classification=Classification.POTENTIAL,
            expected="Services, migrations/seed and application processes start.", actual=message.splitlines()[0][:300],
        )  # fmt: skip
        return CaseOutcome(ResultStatus.FAILED, message.splitlines()[0][:300], {"profile_apps": [a.name for a in profile.apps]},
                           evidence, [finding])  # fmt: skip
    ctx.target_url = environment.target_url
    ctx.run_cache.pop("target_reachable", None)
    hosts = {url.removeprefix("http://") for url in environment.urls.values()}
    ctx.settings.allowed_targets = sorted({*ctx.settings.allowed_targets, *hosts})
    details = {"urls": environment.urls, "target": environment.target_url, "steps": environment.log,
               "isolation": "dedicated container network, generated secrets, own database; ports bound to 127.0.0.1"}  # fmt: skip
    return CaseOutcome(ResultStatus.PASSED, f"environment running at {environment.target_url}", details,
                       [EvidenceDraft("log", "environment start-up", "\n".join(environment.log))])  # fmt: skip
