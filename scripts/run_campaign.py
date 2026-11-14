"""Run one project through the platform: import (or reuse), plan, run, print the stored results.

Drives the public HTTP API of the RUNNING stack, exactly like the dashboard.

Examples (from the repository root):
  backend/.venv/Scripts/python scripts/run_campaign.py --name "My App" --path C:\\Projects\\my-app
  backend/.venv/Scripts/python scripts/run_campaign.py --name "My App" --reuse --profile reports/profiles/my-app.json --managed
  backend/.venv/Scripts/python scripts/run_campaign.py --name "My App" --reuse --target http://127.0.0.1:3000
  backend/.venv/Scripts/python scripts/run_campaign.py --name "Sample" --apk .tools/samples/ApiDemos-debug.apk

--reuse    use the newest existing project with that name instead of importing again
--managed  run dynamic checks against an isolated local environment started by the platform (needs --profile once)
--skip     comma-separated key prefixes to disable for this run, e.g. "SEC-,BUILD,FUNC-TEST" (already covered by an earlier run)
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV = dotenv_values(ROOT / ".env")
API = f"http://127.0.0.1:{ENV.get('QAI_API_PORT', '8000')}/api"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--path", help="local project folder")
    parser.add_argument("--apk", help="APK file to upload")
    parser.add_argument("--target", help="URL of the running application (must be an authorised target)")
    parser.add_argument("--profile", help="JSON file with a managed environment profile to store for the project")
    parser.add_argument("--managed", action="store_true")
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--skip", default="")
    args = parser.parse_args()
    client = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {ENV['QAI_API_TOKEN']}"}, timeout=600, trust_env=False)

    project: dict[str, Any] | None = None
    if args.reuse:
        existing = [p for p in client.get("/projects", params={"limit": 200}).json()["items"] if p["name"] == args.name]
        project = max(existing, key=lambda p: p["created_at"]) if existing else None
    if project is None:
        if args.apk:
            with open(args.apk, "rb") as handle:
                response = client.post("/projects/import", data={"name": args.name, "kind": "apk"},
                                       files={"file": (Path(args.apk).name, handle, "application/vnd.android.package-archive")})  # fmt: skip
        elif args.path:
            data = {"name": args.name, "kind": "local_folder", "local_path": args.path}
            if args.target:
                data["target_url"] = args.target
            response = client.post("/projects/import", data=data)
        else:
            parser.error("--path or --apk is required unless --reuse finds an existing project")
        if response.status_code != 201:
            print("IMPORT FAILED", response.status_code, response.text[:600])
            return 1
        project = response.json()
        version, config = project["versions"][0], project["sources"][0]["config"]
        print(f"imported {project['id']}: {version['file_count']} files, {version['size_bytes'] / 1e6:.1f} MB, "
              f"branch {config.get('branch')} @ {str(config.get('commit'))[:10]}, {config.get('uncommitted_files')} uncommitted, "
              f"{len(config.get('excluded_secret_files', []))} secret file(s) excluded", flush=True)  # fmt: skip
    else:
        print(f"reusing project {project['id']}", flush=True)

    if args.profile:
        profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
        saved = client.put(f"/projects/{project['id']}/environment", json=profile)
        if saved.status_code != 200:
            print("PROFILE REJECTED", saved.text[:600])
            return 1
        print("environment profile stored", flush=True)

    plan = client.post("/plans", json={"project_id": project["id"], "target_url": args.target, "managed_environment": args.managed})
    if plan.status_code != 201:
        print("PLAN FAILED", plan.status_code, plan.text[:600])
        return 1
    plan = plan.json()
    skipped = 0
    for prefix in filter(None, (p.strip() for p in args.skip.split(","))):
        for case in plan["cases"]:
            if case["key"].startswith(prefix) and case["enabled"]:
                client.patch(f"/plans/{plan['id']}/cases/{case['id']}", json={"enabled": False}).raise_for_status()
                skipped += 1
    print(f"plan {plan['id']}: {len(plan['cases'])} cases, {skipped} disabled for this run", flush=True)
    run = client.post(f"/plans/{plan['id']}/runs")
    if run.status_code != 202:
        print("RUN REJECTED", run.status_code, run.text[:600])
        return 1
    run = run.json()
    print(f"run {run['id']}", flush=True)
    last = None
    while True:
        detail = client.get(f"/runs/{run['id']}").json()
        current = detail["progress"].get("case")
        if current != last and current:
            print(time.strftime("%H:%M:%S"), f"{detail['progress'].get('index')}/{detail['progress'].get('total')}", current,
                  f"[{detail['progress'].get('adapter')}]", flush=True)  # fmt: skip
            last = current
        if detail["status"] not in ("queued", "running"):
            break
        time.sleep(10)
    print("STATUS", detail["status"], detail["summary"], detail.get("error") or "")
    for result in detail["results"]:
        message = result["message"].encode("ascii", "replace").decode()[:170]
        print(f"  [{result['status']:>14}] {result['case']['key']:<16} {result['duration_ms'] / 1000:>6.0f}s {message}")
    print("cleanup:", json.dumps(detail["progress"].get("cleanup", {})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
