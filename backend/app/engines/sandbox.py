"""Docker sandbox for running untrusted project code and trusted scanner tools.

Imported code never runs on the host. Every container gets: no capabilities, no-new-privileges,
CPU / memory / PID limits, a wall-clock timeout, a read-only root filesystem and only the mounts
passed explicitly (never the Docker socket, never host credentials or environment).
Residual risks are documented in docs/SECURITY_MODEL.md.
"""

import logging
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.services.settings import ExecutionSettings

log = logging.getLogger(__name__)
SANDBOX_USER = "10001:10001"
MAX_OUTPUT_CHARS = 400_000
RUNTIME_IMAGES = {"python": "python:3.13-slim", "node": "node:22-slim"}


class SandboxUnavailable(Exception):
    """Docker is not installed or the daemon is not reachable."""


@dataclass
class Mount:
    source: str  # absolute host path or named volume
    target: str
    read_only: bool = True
    volume: bool = False

    def as_arg(self) -> str:
        kind = "volume" if self.volume else "bind"
        suffix = ",readonly" if self.read_only else ""
        return f"type={kind},source={self.source},target={self.target}{suffix}"


@dataclass
class ContainerResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr.strip() else "")).strip()


@dataclass(frozen=True)
class Limits:
    cpus: float = 1.0
    memory_mb: int = 768
    pids_limit: int = 256
    tmpfs_mb: int = 256

    @classmethod
    def from_settings(cls, settings: ExecutionSettings, memory_mb: int | None = None) -> "Limits":
        return cls(
            cpus=settings.sandbox_cpus,
            memory_mb=memory_mb or settings.sandbox_memory_mb,
            pids_limit=settings.sandbox_pids_limit,
            tmpfs_mb=settings.sandbox_tmpfs_mb,
        )


@dataclass
class ContainerSpec:
    image: str
    command: list[str]
    mounts: list[Mount] = field(default_factory=list)
    network: bool = False
    workdir: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    user: str = SANDBOX_USER
    timeout_s: int = 300
    limits: Limits = field(default_factory=Limits)
    extra_caps: list[str] = field(default_factory=list)
    # Only for trusted tools that must write to their own image filesystem (e.g. OWASP ZAP). Never for project code.
    read_only_root: bool = True


def _docker(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("docker")
    if not binary:
        raise SandboxUnavailable("docker CLI not found")
    return subprocess.run([binary, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def docker_available() -> bool:
    try:
        return _docker(["info", "--format", "{{.ServerVersion}}"], timeout=15).returncode == 0
    except (SandboxUnavailable, subprocess.TimeoutExpired, OSError):
        return False


def image_present(image: str) -> bool:
    try:
        return _docker(["image", "inspect", image, "--format", "{{.Id}}"], timeout=20).returncode == 0
    except (SandboxUnavailable, subprocess.TimeoutExpired, OSError):
        return False


def run_container(spec: ContainerSpec) -> ContainerResult:
    """Run one container to completion with all isolation flags applied."""
    if not docker_available():
        raise SandboxUnavailable("Docker daemon is not running")
    name = f"qai-{uuid.uuid4().hex[:12]}"
    args = [
        "run", "--rm", "--name", name,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        *(["--read-only"] if spec.read_only_root else []),
        "--tmpfs", f"/tmp:rw,nosuid,nodev,size={spec.limits.tmpfs_mb}m",
        "--cpus", str(spec.limits.cpus), "--memory", f"{spec.limits.memory_mb}m",
        "--memory-swap", f"{spec.limits.memory_mb}m",
        "--pids-limit", str(spec.limits.pids_limit),
        "--network", "bridge" if spec.network else "none",
        "--user", spec.user,
        "--env", "HOME=/tmp",
    ]  # fmt: skip
    for cap in spec.extra_caps:
        args += ["--cap-add", cap]
    for key, value in spec.env.items():
        args += ["--env", f"{key}={value}"]
    for mount in spec.mounts:
        args += ["--mount", mount.as_arg()]
    if spec.workdir:
        args += ["--workdir", spec.workdir]
    args += [spec.image, *spec.command]

    started = time.monotonic()
    timed_out = False
    try:
        completed = _docker(args, timeout=spec.timeout_s)
        code, out, err = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        try:
            _docker(["kill", name], timeout=30)
        except (subprocess.TimeoutExpired, OSError):
            log.error("failed to kill timed-out container", extra={"container": name})
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        err = f"timed out after {spec.timeout_s}s"
        code = 124
    duration = int((time.monotonic() - started) * 1000)
    log.info("sandbox run", extra={"image": spec.image, "exit": code, "ms": duration, "network": spec.network})
    return ContainerResult(code, out[-MAX_OUTPUT_CHARS:], err[-MAX_OUTPUT_CHARS:], timed_out, duration)


def create_volume() -> str:
    name = f"qai-work-{uuid.uuid4().hex[:12]}"
    result = _docker(["volume", "create", name])
    if result.returncode != 0:
        raise SandboxUnavailable(f"cannot create volume: {result.stderr.strip()}")
    return name


def remove_volume(name: str) -> None:
    try:
        _docker(["volume", "rm", "-f", name])
    except (subprocess.TimeoutExpired, OSError, SandboxUnavailable):
        log.warning("could not remove sandbox volume", extra={"volume": name})


def prepare_work_volume(source: Path, image: str) -> str:
    """Copy a workspace into a fresh volume owned by the sandbox user.

    Runs only trusted commands (cp/chown) from a trusted image, with no network. CHOWN is the single
    capability added, and only for this step.
    """
    volume = create_volume()
    result = run_container(
        ContainerSpec(
            image=image,
            command=["sh", "-c", "cp -a /src/. /work/ && chown -R 10001:10001 /work"],
            mounts=[Mount(str(source), "/src"), Mount(volume, "/work", read_only=False, volume=True)],
            user="0:0",
            extra_caps=["CHOWN", "DAC_OVERRIDE", "FOWNER"],
            timeout_s=180,
        )
    )
    if result.exit_code != 0:
        remove_volume(volume)
        raise SandboxUnavailable(f"could not prepare sandbox workspace: {result.output[-500:]}")
    return volume
