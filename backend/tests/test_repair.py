"""Repair strategies, approval rules, policy review and the verification verdict."""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain import ResultStatus
from app.models import Finding, Job, RepairExecution, RepairProposal
from app.repair import review, strategies
from app.repair import service as repair
from app.repair.service import Check, _verdict
from tests.conftest import DEMO_PROJECT

P, F, B = ResultStatus.PASSED, ResultStatus.FAILED, ResultStatus.BLOCKED


def _finding(**facts: object) -> Finding:
    return Finding(project_id=uuid.uuid4(), version_id=uuid.uuid4(), fingerprint="x", category="security",
                   severity="high", title="t", description="d", source_tool="trivy", facts=facts)  # fmt: skip


def _jinja_facts(**override: object) -> dict[str, object]:
    return {"kind": "vulnerable_dependency", "manifest": "requirements.txt", "package": "Jinja2",
            "installed_version": "3.1.2", "fixed_version": "3.1.6", "advisories": [{"id": "CVE-1"}]} | override  # fmt: skip


def test_dependency_bump_changes_exactly_one_pin(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("fastapi==0.115.6\njinja2==3.1.2  # templating\npytest==8.3.4\n")
    draft = strategies.propose(None, _finding(**_jinja_facts()), tmp_path)  # type: ignore[arg-type]
    assert draft.strategy == "dependency_bump" and draft.generated_by == "deterministic"
    assert draft.changes[0]["new_content"] == "fastapi==0.115.6\njinja2==3.1.6  # templating\npytest==8.3.4\n"
    diff = draft.diff(tmp_path)
    assert "-jinja2==3.1.2" in diff and "+jinja2==3.1.6" in diff and not draft.sensitive


@pytest.mark.parametrize(
    ("facts", "content", "reason"),
    [
        (_jinja_facts(fixed_version=None), "jinja2==3.1.2\n", "no fixed version"),
        (_jinja_facts(manifest="package-lock.json"), "{}", "only implemented for pinned requirements.txt"),
        (_jinja_facts(), "jinja2>=3.0\n", "could not find a single"),
        (_jinja_facts(manifest="../outside/requirements.txt"), "jinja2==3.1.2\n", "not found in the project"),
        ({"kind": "axe"}, "", "no automated repair strategy"),
    ],
)
def test_repair_is_refused_when_it_cannot_be_done_safely(tmp_path: Path, facts: dict, content: str, reason: str) -> None:
    (tmp_path / "requirements.txt").write_text(content)
    (tmp_path / "package-lock.json").write_text(content)
    with pytest.raises(strategies.RepairNotPossible, match=reason):
        strategies.propose(None, _finding(**facts), tmp_path)  # type: ignore[arg-type]


def test_sensitive_paths_are_flagged() -> None:
    draft = strategies.ProposalDraft("ai_patch", "m", "r", [{"path": "app/auth/login.py", "new_content": "x"}])
    assert draft.sensitive


def _checks(targeted: ResultStatus, tests: ResultStatus) -> dict[str, Check]:
    return {"targeted": Check("targeted", targeted, "t"), "project_tests": Check("project_tests", tests, "p")}


@pytest.mark.parametrize(
    ("before", "after", "policy", "fail_before", "fail_after", "verified", "expected"),
    [
        (_checks(F, F), _checks(P, F), True, {"t::a"}, {"t::a"}, True, "pre-existing test failure"),
        (_checks(F, P), _checks(P, P), True, set(), set(), True, "no newly failing tests"),
        (_checks(P, P), _checks(P, P), True, set(), set(), False, "not reproduced"),
        (_checks(F, P), _checks(F, P), True, set(), set(), False, "did not resolve"),
        (_checks(F, P), _checks(P, F), True, set(), {"t::new"}, False, "newly failing"),
        (_checks(F, B), _checks(P, B), True, set(), set(), False, "regression could not be verified"),
        (_checks(F, ResultStatus.NOT_TESTED), _checks(P, ResultStatus.NOT_TESTED), True, set(), set(), False, "could not be verified"),
        (_checks(F, P), _checks(P, P), False, set(), set(), False, "policy review rejected"),
        (_checks(B, P), _checks(P, P), True, set(), set(), False, "not reproduced"),
    ],
)
def test_fix_is_verified_only_with_full_before_after_evidence(
    before: dict, after: dict, policy: bool, fail_before: set, fail_after: set, verified: bool, expected: str
) -> None:
    ok, outcome = _verdict(before, after, policy, fail_before, fail_after)
    assert ok is verified and expected in outcome


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    (tmp_path / "requirements.txt").write_text("jinja2==3.1.2\n")
    review.git(tmp_path, "init", "-q", "-b", review.BASE_BRANCH)
    review.git(tmp_path, "add", "-A")
    review.git(tmp_path, "commit", "-q", "-m", "baseline")
    review.git(tmp_path, "checkout", "-q", "-b", "qai/repair-test")
    return tmp_path


def _commit(repo: Path) -> None:
    review.git(repo, "add", "-A")
    review.git(repo, "commit", "-q", "-m", "patch")


def test_policy_review_approves_a_minimal_declared_change(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "requirements.txt").write_text("jinja2==3.1.6\n")
    _commit(repo)
    proposal = RepairProposal(strategy="dependency_bump", affected_files=["requirements.txt"])
    assert all(check["passed"] for check in review.policy_review(repo, proposal))


def test_policy_review_rejects_test_tampering_deletions_and_undeclared_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "requirements.txt").write_text("jinja2==3.1.6\n")
    (repo / "tests" / "test_x.py").unlink()
    (repo / "extra.py").write_text("x = 1\n")
    _commit(repo)
    proposal = RepairProposal(strategy="dependency_bump", affected_files=["requirements.txt"])
    failed = {check["name"] for check in review.policy_review(repo, proposal) if not check["passed"]}
    assert {"only_declared_files", "no_tests_modified", "no_files_deleted"} <= failed


def _seed_proposal(db, client: TestClient, *, sensitive: bool) -> RepairProposal:
    project = client.post("/api/projects/import", data={"name": "Demo", "kind": "local_folder",
                                                        "local_path": str(DEMO_PROJECT)}).json()  # fmt: skip
    finding = Finding(project_id=uuid.UUID(project["id"]), version_id=uuid.UUID(project["versions"][0]["id"]),
                      fingerprint="f1", category="security", severity="high", title="vulnerable jinja2",
                      description="d", source_tool="trivy", facts=_jinja_facts(package="jinja2"))  # fmt: skip
    db.add(finding)
    db.flush()
    proposal = RepairProposal(finding_id=finding.id, strategy="dependency_bump", generated_by="deterministic",
                              rationale="r", diff="d", affected_files=["requirements.txt"], sensitive=sensitive)  # fmt: skip
    db.add(proposal)
    db.commit()
    return proposal


def test_nothing_is_executed_without_owner_approval(client: TestClient, db) -> None:
    proposal = _seed_proposal(db, client, sensitive=False)
    assert db.scalar(select(RepairExecution)) is None
    rejected = client.post(f"/api/repairs/{proposal.id}/decision", json={"approve": False, "note": "not now"})
    assert rejected.json()["status"] == "rejected" and db.scalar(select(RepairExecution)) is None
    again = client.post(f"/api/repairs/{proposal.id}/decision", json={"approve": True})
    assert again.status_code == 409  # a decided proposal cannot be re-decided


def test_sensitive_patch_needs_explicit_confirmation(client: TestClient, db) -> None:
    proposal = _seed_proposal(db, client, sensitive=True)
    blocked = client.post(f"/api/repairs/{proposal.id}/decision", json={"approve": True})
    assert blocked.status_code == 409 and "explicit confirmation" in blocked.json()["detail"]
    approved = client.post(f"/api/repairs/{proposal.id}/decision", json={"approve": True, "confirm_sensitive": True})
    assert approved.json()["status"] == "approved"
    assert db.scalar(select(Job).where(Job.kind == "repair_execution")) is not None
    patch = client.get(f"/api/repairs/{proposal.id}/patch")
    assert patch.status_code == 200 and "attachment" in patch.headers["content-disposition"]


def test_proposal_generation_records_why_it_failed(client: TestClient, db) -> None:
    project = client.post("/api/projects/import", data={"name": "Demo", "kind": "local_folder",
                                                        "local_path": str(DEMO_PROJECT)}).json()  # fmt: skip
    finding = Finding(project_id=uuid.UUID(project["id"]), version_id=uuid.UUID(project["versions"][0]["id"]),
                      fingerprint="f2", category="accessibility", severity="low", title="axe", description="d",
                      source_tool="axe-core", facts={"kind": "axe"})  # fmt: skip
    db.add(finding)
    db.commit()
    assert client.post(f"/api/findings/{finding.id}/repair-proposals").status_code == 202
    repair.generate_proposal(finding.id)
    detail = client.get(f"/api/findings/{finding.id}").json()
    assert detail["proposals"][0]["status"] == "failed" and "no automated repair strategy" in detail["proposals"][0]["rationale"]
    assert detail["status"] == "open"
