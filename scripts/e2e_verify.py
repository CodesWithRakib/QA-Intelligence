"""End-to-end acceptance check against a RUNNING stack (API + worker + demo target).

Drives the public HTTP API only, exactly like the dashboard does:
import -> plan -> run -> findings -> repair proposal -> approval -> verified repair -> report.

Usage (from the repository root, after scripts/dev.ps1 and scripts/start-demo-target.ps1):
    backend\\.venv\\Scripts\\python scripts\\e2e_verify.py [--skip-ai-repair]

Exit code 0 only when every required check passed. Nothing is assumed: each line printed is an
assertion on data returned by the API.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV = dotenv_values(ROOT / ".env")
API = f"http://{ENV.get('QAI_API_HOST', '127.0.0.1')}:{ENV.get('QAI_API_PORT', '8000')}/api"
TARGET = "http://127.0.0.1:8100"
checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    checks.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)
    return ok


def wait_for(client: httpx.Client, path: str, done: Any, timeout_s: int, label: str) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        data = client.get(path).raise_for_status().json()
        if done(data):
            return data
        if time.monotonic() > deadline:
            raise TimeoutError(f"{label} did not finish within {timeout_s}s")
        time.sleep(3)


def repair(client: httpx.Client, finding: dict[str, Any], label: str, timeout_s: int) -> dict[str, Any] | None:
    client.post(f"/findings/{finding['id']}/repair-proposals").raise_for_status()
    detail = wait_for(client, f"/findings/{finding['id']}", lambda d: d["proposals"], 600, f"{label} proposal")
    proposal = detail["proposals"][0]
    if not check(f"{label}: repair proposed", proposal["status"] == "proposed",
                 f"{proposal['strategy']} by {proposal['generated_by']}: {proposal['rationale'][:140]}"):  # fmt: skip
        return None
    check(f"{label}: nothing executed before approval", proposal["executions"] == [])
    client.post(f"/repairs/{proposal['id']}/decision", json={"approve": True, "note": "e2e approval"}).raise_for_status()
    final = wait_for(client, f"/repairs/{proposal['id']}",
                     lambda d: d["executions"] and d["executions"][-1]["status"] not in ("queued", "running"),
                     timeout_s, f"{label} execution")  # fmt: skip
    execution = final["executions"][-1]
    phases = {(r["phase"], r["check"]): r["status"] for r in execution["regression_results"]}
    check(f"{label}: applied on an isolated branch", bool(execution["branch"]) and "repairs/" in (execution["workspace_path"] or ""),
          f"{execution['branch']} in data/{execution['workspace_path']}")  # fmt: skip
    check(f"{label}: independent policy review approved", execution["review"].get("policy_approved") is True,
          f"AI review: {execution['review'].get('ai_review', {}).get('status')}")  # fmt: skip
    check(f"{label}: defect reproduced before, resolved after",
          phases.get(("before", "targeted")) == "failed" and phases.get(("after", "targeted")) == "passed", str(phases))
    check(f"{label}: regression tests ran before and after",
          phases.get(("before", "project_tests")) in ("passed", "failed") and phases.get(("after", "project_tests")) in ("passed", "failed"))
    check(f"{label}: repair verified", execution["status"] == "verified", execution["outcome"])
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-ai-repair", action="store_true", help="skip the local-LLM patch for the failing tests")
    args = parser.parse_args()
    token = ENV.get("QAI_API_TOKEN")
    if not token:
        print("QAI_API_TOKEN missing: run scripts/setup-env.ps1")
        return 2
    client = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=120, trust_env=False)

    health = client.get("/system/health").raise_for_status().json()
    for name in ("database", "worker", "docker", "sandbox_python", "trivy", "semgrep", "ollama", "playwright"):
        check(f"health: {name}", health["checks"][name]["ok"], health["checks"][name]["detail"])
    check("unauthenticated request is rejected", httpx.get(f"{API}/projects", trust_env=False).status_code == 401)

    settings = client.get("/settings/execution").json()
    settings["allowed_targets"] = sorted({*settings["allowed_targets"], "127.0.0.1:8100"})
    client.put("/settings/execution", json=settings).raise_for_status()

    project = client.post("/projects/import", data={
        "name": "Demo Shop (e2e)", "kind": "local_folder", "local_path": str(ROOT / "examples" / "demo-shop"),
        "target_url": TARGET}).raise_for_status().json()  # fmt: skip
    check("local project imported", project["versions"][0]["file_count"] >= 10, f"{project['versions'][0]['file_count']} files")
    architecture = client.get(f"/projects/{project['id']}/architecture").json()
    inventory = architecture["inventory"]
    check("language and framework detected", inventory["languages"][0]["name"] == "Python" and "FastAPI" in inventory["frameworks"],
          f"{[lang['name'] for lang in inventory['languages']]} / {inventory['frameworks']}")  # fmt: skip
    check("architecture inventory generated", len(architecture["api_endpoints"]) >= 8 and len(architecture["components"]) == 1,
          f"{len(architecture['api_endpoints'])} endpoints, {len(inventory['openapi_specs'])} OpenAPI spec")  # fmt: skip

    plan = client.post("/plans", json={"project_id": project["id"], "target_url": TARGET}).raise_for_status().json()
    check("QA test plan generated", len(plan["cases"]) >= 20, f"{len(plan['cases'])} cases")
    run = client.post(f"/plans/{plan['id']}/runs").raise_for_status().json()
    print("... test run in progress (scanners, sandbox tests, browser, API) ...", flush=True)
    run = wait_for(client, f"/runs/{run['id']}", lambda d: d["status"] not in ("queued", "running"), 2400, "test run")
    by_key = {r["case"]["key"]: r for r in run["results"]}
    check("run completed and every planned case has a stored result",
          run["status"] == "completed" and len(run["results"]) == run["planned_cases"], str(run["summary"]))

    def status_of(prefix: str, check_name: str | None = None) -> list[str]:
        return [r["status"] for k, r in by_key.items() if k.startswith(prefix)
                and (check_name is None or r["case"]["spec"].get("check") == check_name)]  # fmt: skip

    executed = ("passed", "failed")
    browser = [r for r in run["results"] if r["case"]["engine"] == "browser"]
    check("real Playwright browser tests executed", all(r["status"] in executed for r in browser) and len(browser) == 5,
          ", ".join(f"{r['case']['key']}={r['status']}" for r in browser))  # fmt: skip
    check("browser evidence stored (screenshots)", any(e["kind"] == "screenshot" and e["has_file"] for r in browser for e in r["evidence"]))
    contract = status_of("API-CONTRACT")
    check("real API tests executed against the local target", all(s in (*executed, "skipped") for s in contract) and "failed" in contract,
          f"contract: {contract}, negative: {status_of('API-NEG')}")  # fmt: skip
    check("dependency scan executed (Trivy)", by_key["SEC-DEP-001"]["status"] == "failed", by_key["SEC-DEP-001"]["message"])
    check("static analysis executed (Semgrep)", by_key["SEC-SAST-001"]["status"] in executed, by_key["SEC-SAST-001"]["message"])
    check("project tests executed in the sandbox", by_key["FUNC-TEST-001"]["status"] == "failed", by_key["FUNC-TEST-001"]["message"])
    check("sandbox isolation recorded", "network disabled" in by_key["FUNC-TEST-001"]["details"].get("isolation", ""))
    check("manual areas are reported as not tested", set(status_of("MAN-")) == {"not_tested"})
    screenshot = next(e for r in browser for e in r["evidence"] if e["kind"] == "screenshot")
    image = client.get(f"/evidence/{screenshot['id']}/file")
    check("evidence file retrievable", image.status_code == 200 and image.content[:4] == b"\x89PNG", f"{len(image.content)} bytes")

    findings = client.get("/findings", params={"project_id": project["id"], "limit": 200}).json()["items"]
    categories = {f["category"] for f in findings}
    check("actionable findings created across categories",
          {"security", "functional", "api", "accessibility", "browser"} <= categories, f"{len(findings)} findings in {sorted(categories)}")
    seeded = {
        "wrong status for unknown product": any("unknown resource" in f["title"] for f in findings),
        "schema mismatch (price)": any("does not match its OpenAPI schema" in f["title"] for f in findings),
        "vulnerable jinja2": any(f["title"].lower().startswith("jinja2 3.1.2") for f in findings),
        "hard-coded credential": any("Hard-coded credential" in f["title"] for f in findings),
        "missing alt text": any("image-alt" in f["title"] for f in findings),
        "broken link /promo": any("Broken link: /promo" in f["title"] for f in findings),
        "mobile overflow": any("Horizontal overflow on mobile" in f["title"] for f in findings),
        "missing security headers": any("Missing security header" in f["title"] for f in findings),
    }
    for name, found in seeded.items():
        check(f"seeded defect detected: {name}", found)
    sqli = any(f["source_tool"] == "semgrep" and "catalog.py" in f["affected_component"] for f in findings)
    check("seeded defect detected: SQL injection (Semgrep)", sqli)

    jinja = next(f for f in findings if f["title"].lower().startswith("jinja2 3.1.2"))
    repair(client, jinja, "dependency repair", 1500)
    after = client.get(f"/findings/{jinja['id']}").json()
    check("finding marked fixed only after verification", after["status"] == "fixed" and after["verification_status"] == "verified_fixed")

    if not args.skip_ai_repair:
        tests = next(f for f in findings if f["category"] == "functional")
        outcome = repair(client, tests, "AI repair (local model)", 1800)
        if outcome is None:
            print("      note: the local model did not produce a usable patch; this is reported, not hidden.")

    report = client.post(f"/runs/{run['id']}/report").raise_for_status().json()
    text = report["content_markdown"]
    check("honest report produced", "must not be read as passing" in text and f"| Not tested | {run['summary']['not_tested']} |" in text,
          f"counts {report['data']['counts']}")  # fmt: skip
    audit = client.get("/audit", params={"limit": 200}).json()["items"]
    actions = {a["action"] for a in audit}
    check("audit trail recorded", {"project.imported", "plan.generated", "run.queued", "repair.approved", "report.generated"} <= actions)
    agents = client.get("/system/agents").json()
    check("agent executions recorded", sum(a["executions"] for a in agents["agents"]) >= 8)

    failed = [name for name, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
