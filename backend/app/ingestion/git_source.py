"""Git ingestion: shallow clone of a local repository or a public HTTPS repository.

Safety: only the committed HEAD is fetched (depth 1); no hooks, no credential helper, no prompts,
no LFS download, no symlink materialisation. Stored credentials on this PC are never used, so only
public remote repositories can be cloned. Remote hosts must be on the owner's allowlist.
The clone is temporary: its files then pass through the normal safe copy (limits, secret exclusion).
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_REMOTE = re.compile(r"^https://([A-Za-z0-9.-]+)/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
CLONE_TIMEOUT_S = 300


class GitSourceError(Exception):
    """The repository could not be cloned; the message is safe to show to the owner."""


@dataclass(frozen=True)
class CloneInfo:
    location: str
    commit: str
    branch: str
    remote: bool


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("git")
    if not binary:
        raise GitSourceError("git is not installed")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo", "GIT_LFS_SKIP_SMUDGE": "1",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}  # fmt: skip
    return subprocess.run(
        [binary, "-c", "credential.helper=", "-c", f"core.hooksPath={os.devnull}", "-c", "core.symlinks=false",
         "-c", "core.autocrlf=false", "-c", "protocol.file.allow=always", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=CLONE_TIMEOUT_S, env=env,
    )  # fmt: skip


def resolve(source: str, allowed_hosts: list[str]) -> tuple[str, bool]:
    """Validate the source and return (clone URL or path, is_remote)."""
    source = source.strip()
    if match := _REMOTE.match(source):
        host = match.group(1).lower()
        if host not in {h.lower() for h in allowed_hosts}:
            raise GitSourceError(f"host '{host}' is not on the allowed git hosts list (Execution settings)")
        return f"https://{host}/{match.group(2)}/{match.group(3)}.git", True
    if "://" in source or source.startswith("git@"):
        raise GitSourceError("only https://<host>/<owner>/<repository> URLs or local repository folders are supported")
    path = Path(source).expanduser()
    if not path.is_absolute() or not (path / ".git").exists():
        raise GitSourceError("provide an absolute path to a local git repository, or an https repository URL")
    return str(path.resolve()), False


def clone(source: str, destination: Path, allowed_hosts: list[str]) -> CloneInfo:
    location, remote = resolve(source, allowed_hosts)
    try:
        result = _git(["clone", "--depth", "1", "--single-branch", "--no-tags", location, str(destination)])
    except subprocess.TimeoutExpired as exc:
        raise GitSourceError(f"clone did not finish within {CLONE_TIMEOUT_S}s") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1][:300] if result.stderr.strip() else "unknown error"
        hint = " (private repositories are not supported: no credentials are used)" if remote else ""
        raise GitSourceError(f"git clone failed: {detail}{hint}")
    commit = _git(["rev-parse", "HEAD"], cwd=destination).stdout.strip()
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=destination).stdout.strip()
    return CloneInfo(location, commit, branch, remote)
