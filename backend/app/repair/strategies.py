"""Repair strategies. Deterministic fixes are preferred; the AI patch is a fallback for failing tests."""

import ast
import difflib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.ai.service import run_json_task
from app.discovery.scanner import TEST_FILE
from app.models import Finding
from app.security.archive import EXCLUDED_DIRS

SENSITIVE_PATH = re.compile(
    r"(auth|login|passw|permission|role|payment|billing|checkout|crypto|encrypt|secret|token|session|oauth|jwt)",
    re.IGNORECASE,
)
MAX_CONTEXT_BYTES = 24_000
MAX_CONTEXT_FILES = 20


class RepairNotPossible(Exception):
    """No safe automated repair exists for this finding (the reason is shown to the owner)."""


@dataclass
class ProposalDraft:
    strategy: str
    generated_by: str
    rationale: str
    changes: list[dict[str, str]] = field(default_factory=list)  # {"path", "new_content"}

    @property
    def affected_files(self) -> list[str]:
        return sorted(change["path"] for change in self.changes)

    @property
    def sensitive(self) -> bool:
        return any(SENSITIVE_PATH.search(path) for path in self.affected_files)

    def diff(self, workspace: Path) -> str:
        chunks = []
        for change in self.changes:
            old = (workspace / change["path"]).read_text(encoding="utf-8").splitlines(keepends=True)
            new = change["new_content"].splitlines(keepends=True)
            chunks.append("".join(difflib.unified_diff(old, new, f"a/{change['path']}", f"b/{change['path']}")))
        return "\n".join(chunks)


def _inside(workspace: Path, relative: str) -> Path:
    path = (workspace / relative).resolve()
    if not path.is_relative_to(workspace.resolve()) or not path.is_file():
        raise RepairNotPossible(f"file not found in the project version: {relative}")
    return path


def _dependency_bump(finding: Finding, workspace: Path) -> ProposalDraft:
    facts = finding.facts
    manifest, package = str(facts.get("manifest", "")), str(facts.get("package", ""))
    installed, fixed = str(facts.get("installed_version", "")), facts.get("fixed_version")
    if not fixed:
        raise RepairNotPossible("no fixed version has been published for this package")
    if not re.fullmatch(r"requirements[\w.-]*\.txt", Path(manifest).name):
        raise RepairNotPossible(
            f"automatic upgrades are only implemented for pinned requirements.txt files (found {manifest}); "
            "lockfile-based ecosystems need their package manager to regenerate the lock"
        )
    path = _inside(workspace, manifest)
    name = re.escape(package).replace(r"\-", "[-_.]").replace("_", "[-_.]")
    pattern = re.compile(rf"^(\s*{name}\s*(?:\[[^\]]*\])?\s*==\s*){re.escape(installed)}(\s*(?:[;#].*)?)$", re.IGNORECASE)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    replaced = 0
    for index, line in enumerate(lines):
        match = pattern.match(line.rstrip("\r\n"))
        if match:
            ending = line[len(line.rstrip("\r\n")) :]
            lines[index] = f"{match.group(1)}{fixed}{match.group(2)}{ending}"
            replaced += 1
    if replaced != 1:
        raise RepairNotPossible(f"could not find a single `{package}=={installed}` pin in {manifest}")
    ids = ", ".join(a["id"] for a in facts.get("advisories", [])[:10])
    caveat = "" if facts.get("fully_fixable", True) else " Some advisories have no published fix and will remain."
    return ProposalDraft(
        "dependency_bump",
        "deterministic",
        f"Upgrade {package} from {installed} to {fixed}, the lowest version that resolves the reported advisories "
        f"({ids}).{caveat} Verification re-runs the dependency scan and the project's tests before and after.",
        [{"path": manifest, "new_content": "".join(lines)}],
    )


def _collect_context(workspace: Path, component: str, failing_tests: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    root = _inside_dir(workspace, component)
    test_files: dict[str, str] = {}
    sources: dict[str, str] = {}
    budget = MAX_CONTEXT_BYTES
    for test_id in failing_tests:
        relative = test_id.split("::")[0]
        path = root / relative
        if path.is_file() and relative not in test_files:
            test_files[relative] = path.read_text(encoding="utf-8", errors="replace")[:8000]
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if EXCLUDED_DIRS & set(path.relative_to(root).parts) or TEST_FILE.search(relative) or "test" in relative.lower():
            continue
        size = path.stat().st_size
        if size == 0 or size > budget or len(sources) >= MAX_CONTEXT_FILES:
            continue
        sources[relative] = path.read_text(encoding="utf-8", errors="replace")
        budget -= size
    return test_files, sources


def _inside_dir(workspace: Path, component: str) -> Path:
    root = (workspace / component).resolve()
    if not root.is_relative_to(workspace.resolve()) or not root.is_dir():
        raise RepairNotPossible(f"component directory not found: {component or '.'}")
    return root


def _ai_test_fix(db: Session, finding: Finding, workspace: Path) -> ProposalDraft:
    facts = finding.facts
    if facts.get("runtime") != "python":
        raise RepairNotPossible("AI patches are only implemented for Python projects with pytest suites")
    component = str(facts.get("component", ""))
    tests, sources = _collect_context(workspace, component, list(facts.get("failing_tests", [])))
    if not sources:
        raise RepairNotPossible("no source files small enough to give to the model were found")
    prompt = (
        "Some tests fail. Find the defect in the SOURCE files and fix it with the smallest possible change. "
        "Never modify tests. Return the full new content of each source file you change.\n\n"
        f"## Test output\n{str(facts.get('output_tail', ''))[-3000:]}\n\n"
        f"## Failing test files (read-only)\n{json.dumps(tests, indent=1)}\n\n"
        f"## Source files (editable)\n{json.dumps(sources, indent=1)}"
    )
    schema: dict[str, Any] = {
        "type": "object", "additionalProperties": False, "required": ["root_cause", "files"],
        "properties": {
            "root_cause": {"type": "string"},
            "files": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
                "type": "object", "additionalProperties": False, "required": ["path", "new_content"],
                "properties": {"path": {"type": "string", "enum": sorted(sources)}, "new_content": {"type": "string"}}}},
        },
    }  # fmt: skip
    result = run_json_task(
        db, task_type="repair", project_id=finding.project_id, schema=schema, prompt=prompt, max_output_tokens=4096,
        system="You are a careful software engineer fixing a bug. Reply with JSON matching the schema.", timeout_s=420,
    )  # fmt: skip
    changes = []
    for item in result.data["files"]:
        relative, content = item["path"], item["new_content"]
        if relative not in sources:
            raise RepairNotPossible(f"the model tried to edit a file outside the allowed set: {relative}")
        if content.strip() == sources[relative].strip():
            continue
        try:
            ast.parse(content)
        except SyntaxError as exc:
            raise RepairNotPossible(f"the model produced invalid Python for {relative}: {exc.msg}") from exc
        if not content.endswith("\n"):
            content += "\n"
        changes.append({"path": f"{component}/{relative}".lstrip("/"), "new_content": content})
    if not changes:
        raise RepairNotPossible("the model did not propose any change")
    return ProposalDraft(
        "ai_patch",
        result.label,
        f"[AI-generated, unverified until executed] {result.data['root_cause']}",
        changes,
    )


def propose(db: Session, finding: Finding, workspace: Path) -> ProposalDraft:
    kind = finding.facts.get("kind")
    if kind == "vulnerable_dependency":
        return _dependency_bump(finding, workspace)
    if kind == "test_failure":
        return _ai_test_fix(db, finding, workspace)
    raise RepairNotPossible(
        f"no automated repair strategy exists yet for '{kind or finding.category}' findings; "
        "use the remediation guidance to fix it manually"
    )


def new_branch_name(execution_id: uuid.UUID) -> str:
    return f"qai/repair-{execution_id.hex[:8]}"
