"""Independent review of an applied patch.

The deterministic policy is binding. The AI opinion is advisory only: a model can neither approve
a patch that violates policy nor mark anything as verified.
"""

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.ai.providers import ProviderError
from app.ai.service import AIUnavailable, run_json_task
from app.discovery.scanner import TEST_FILE
from app.models import RepairProposal
from app.security.archive import is_secret_file

MAX_CHANGED_LINES = 400
BASE_BRANCH = "main"


class GitError(Exception):
    pass


def git(workspace: Path, *args: str) -> str:
    """Run git in an isolated workspace, ignoring user/system configuration and hooks."""
    binary = shutil.which("git")
    if not binary:
        raise GitError("git is not installed")
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    completed = subprocess.run(
        [binary, "-c", f"core.hooksPath={os.devnull}", "-c", "core.autocrlf=false", "-c", "core.fsmonitor=false",
         "-c", "user.name=QA Intelligence", "-c", "user.email=qa-intelligence@localhost", *args],
        cwd=workspace, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env,
    )  # fmt: skip
    if completed.returncode != 0:
        raise GitError(f"git {args[0]} failed: {completed.stderr.strip()[:500]}")
    return completed.stdout


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def policy_review(workspace: Path, proposal: RepairProposal) -> list[dict[str, Any]]:
    changed = sorted(filter(None, git(workspace, "diff", "--name-only", f"{BASE_BRANCH}..HEAD").splitlines()))
    deleted = list(
        filter(
            None,
            git(workspace, "diff", "--name-only", "--diff-filter=D", f"{BASE_BRANCH}..HEAD").splitlines(),
        )
    )
    numstat = [line.split("\t") for line in git(workspace, "diff", "--numstat", f"{BASE_BRANCH}..HEAD").splitlines() if line]
    lines = sum(int(a) + int(d) for a, d, _ in numstat if a.isdigit() and d.isdigit())
    tests = [f for f in changed if TEST_FILE.search(f)]
    secrets = [f for f in changed if is_secret_file(Path(f).name)]
    checks = [
        _check(
            "only_declared_files",
            changed == sorted(proposal.affected_files),
            f"changed: {changed}; declared: {sorted(proposal.affected_files)}",
        ),
        _check(
            "no_tests_modified",
            not tests,
            f"test files touched: {tests}" if tests else "no test file was changed",
        ),
        _check("no_files_deleted", not deleted, f"deleted: {deleted}" if deleted else "nothing deleted"),
        _check("no_secret_files", not secrets, f"secret-like files touched: {secrets}" if secrets else "none"),
        _check("bounded_size", lines <= MAX_CHANGED_LINES, f"{lines} changed line(s), limit {MAX_CHANGED_LINES}"),
    ]
    if proposal.strategy == "dependency_bump":
        single = all(a == "1" and d == "1" for a, d, _ in numstat)
        checks.append(_check("single_pin_change", single, "exactly one line replaced per manifest" if single
                             else f"unexpected change size: {numstat}"))  # fmt: skip
    return checks


def ai_review(db: Session, proposal: RepairProposal, project_id: uuid.UUID) -> dict[str, Any]:
    schema = {
        "type": "object", "additionalProperties": False, "required": ["verdict", "notes"],
        "properties": {"verdict": {"type": "string", "enum": ["looks_correct", "concerns"]}, "notes": {"type": "string"}},
    }  # fmt: skip
    try:
        result = run_json_task(
            db, task_type="review", project_id=project_id, schema=schema, timeout_s=240, max_output_tokens=600,
            system="You are an independent code reviewer. Judge only the diff shown. Reply with JSON.",
            prompt=f"Stated intent: {proposal.rationale[:1500]}\n\nDiff:\n{proposal.diff[:8000]}\n\n"
                   "Does the diff do what it says without obvious side effects?",
        )  # fmt: skip
    except (AIUnavailable, ProviderError) as exc:
        return {"status": "unavailable", "reason": str(exc)[:300]}
    return {"status": "completed", "advisory": True, "model": result.label, **result.data}
