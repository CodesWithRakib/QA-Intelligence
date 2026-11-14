"""Write per-project and consolidated QA reports from data stored by the platform.

Reads everything through the API of the RUNNING backend; nothing is computed outside stored results.
When a project has several runs, the newest result of each check is used and the run it came from
is named. Secret values never appear: scanners redact or mask them before storage.

Usage:  backend/.venv/Scripts/python scripts/generate_project_reports.py "Dhruto" "OgroniEdu" ...
Output: reports/projects/<slug>/{QA_REPORT,FINDINGS,TEST_RESULTS}.md and reports/PHASE_2_ALL_PROJECTS_REPORT.md
        (the reports folder is git-ignored: it describes weaknesses of private projects)
"""

import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV = dotenv_values(ROOT / ".env")
API = f"http://127.0.0.1:{ENV.get('QAI_API_PORT', '8000')}/api"
OUT = ROOT / "reports"
STATES = ["passed", "failed", "blocked", "skipped", "not_tested", "not_applicable"]
LABEL = {"passed": "Passed", "failed": "Failed", "blocked": "Blocked", "skipped": "Skipped", "not_tested": "Not tested",
         "not_applicable": "Not applicable"}  # fmt: skip
SEVERITIES = ["critical", "high", "medium", "low", "info"]
CLASS_LABEL = {"confirmed_defect": "Confirmed", "potential_defect": "Potential (tool-reported)", "ai_hypothesis": "AI hypothesis",
               "environment_failure": "Environment", "false_positive": "False positive"}  # fmt: skip
# Test categories that every project report must account for, with the engines/keys that cover them.
CATEGORY_MATRIX = [
    ("Build verification", lambda c: c["spec"].get("step") == "build"),
    ("Static analysis (type check, lint, SAST)", lambda c: c["spec"].get("step") in ("typecheck", "lint") or c["spec"].get("tool") == "semgrep"),
    ("Dependency audit", lambda c: c["engine"] == "dependency_scan"),
    ("Secret scan", lambda c: str(c["spec"].get("tool", "")).startswith(("gitleaks", "trivy-secret", "builtin"))),
    ("Unit / integration tests", lambda c: c["spec"].get("step") == "test" or (c["engine"] == "project_tests" and c["spec"].get("runtime") == "python")),
    ("Local environment start (migrations, seed, apps)", lambda c: c["engine"] == "environment"),
    ("Functional (page health, routes, forms, links)", lambda c: c["spec"].get("check") in ("page_health", "crawl", "route_sweep", "forms") and c["spec"].get("browser", "chromium") == "chromium"),
    ("End-to-end user journeys (authenticated)", lambda c: c["key"].startswith(("MAN-FUNC", "MAN-AUTH"))),
    ("Responsive", lambda c: c["spec"].get("check") == "responsive"),
    ("Cross-browser (Firefox, WebKit)", lambda c: c["spec"].get("browser") in ("firefox", "webkit")),
    ("Visual", lambda c: c["spec"].get("check") == "visual"),
    ("Accessibility", lambda c: c["spec"].get("check") == "accessibility" or c["key"].startswith("MAN-A11Y")),
    ("API", lambda c: c["engine"] == "api" and c["category"] == "api"),
    ("Database", lambda c: c["category"] == "database"),
    ("Performance", lambda c: c["category"] == "performance"),
    ("Security (headers, cookies, CORS, config, passive scan)", lambda c: c["spec"].get("check") in ("security_headers", "cookie_flags", "cors") or c["engine"] == "dast" or c["spec"].get("tool") == "trivy-config"),
    ("PWA", lambda c: c["spec"].get("check") == "pwa"),
    ("Android", lambda c: c["engine"].startswith("android")),
]  # fmt: skip


def cell(text: object, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(text)).replace("|", "\\|")[:limit]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def collect(client: httpx.Client, project: dict[str, Any]) -> dict[str, Any]:
    runs = client.get("/runs", params={"project_id": project["id"], "limit": 100}).raise_for_status().json()["items"]
    finished = sorted((r for r in runs if r["status"] in ("completed", "cancelled")), key=lambda r: r["created_at"])
    latest: dict[str, dict[str, Any]] = {}
    for run in finished:
        detail = client.get(f"/runs/{run['id']}").raise_for_status().json()
        for result in detail["results"]:
            identity = f"{result['case']['category']}|{result['case']['title']}"
            latest[identity] = {**result, "run_id": run["id"], "run_at": run["created_at"], "managed": run["target_url"] is None}
    findings = []
    offset = 0
    while True:
        page = client.get("/findings", params={"project_id": project["id"], "limit": 200, "offset": offset}).raise_for_status().json()
        findings += [client.get(f"/findings/{f['id']}").raise_for_status().json() for f in page["items"]]
        offset += 200
        if offset >= page["total"]:
            break
    architecture = client.get(f"/projects/{project['id']}/architecture").raise_for_status().json()
    plans = client.get("/plans", params={"project_id": project["id"], "limit": 50}).raise_for_status().json()["items"]
    notes = sorted({note for plan in plans for note in client.get(f"/plans/{plan['id']}").json()["notes"]})
    return {"project": project, "runs": finished, "results": sorted(latest.values(), key=lambda r: r["case"]["key"]),
            "findings": findings, "architecture": architecture, "notes": notes}  # fmt: skip


def category_table(results: list[dict[str, Any]]) -> list[str]:
    lines = ["| Test category | Outcome | Detail |", "|---|---|---|"]
    for label, matches in CATEGORY_MATRIX:
        rows = [r for r in results if matches(r["case"])]
        if not rows:
            lines.append(f"| {label} | Not planned | No applicable check was planned for this project (see coverage notes) |")
            continue
        counts = Counter(r["status"] for r in rows)
        outcome = ", ".join(f"{counts[s]} {LABEL[s].lower()}" for s in STATES if counts[s])
        reasons = "; ".join(sorted({cell(r["message"], 150) for r in rows if r["status"] in ("blocked", "not_tested", "failed")}))[:420]
        lines.append(f"| {label} | {outcome} | {reasons or 'see TEST_RESULTS.md'} |")
    return lines


def environment_lines(data: dict[str, Any]) -> list[str]:
    project, architecture = data["project"], data["architecture"]
    source = project["sources"][0]
    config = source["config"]
    inventory = architecture["inventory"]
    lines = [
        f"- Source: {source['kind'].replace('_', ' ')} `{source['location']}`",
        f"- Git: branch `{config.get('branch', 'n/a')}`, commit `{str(config.get('commit', 'n/a'))[:12]}`, "
        f"{config.get('uncommitted_files', 0)} uncommitted file(s) at import ({config.get('note', 'n/a')})",
        f"- Imported files: {project['versions'][-1]['file_count']} (content hash `{project['versions'][-1]['content_hash'][:12]}`); "
        f"{len(config.get('excluded_secret_files', []))} secret-like file(s) were excluded from the copy and never read",
        f"- Stack: {', '.join(inventory.get('frameworks', [])) or 'unknown'}; package manager {', '.join(inventory.get('package_managers', [])) or 'n/a'}",
        "- Host: Windows 11 Pro, Ryzen 5 3400G, 16 GB RAM; Docker 29.8.2 (4 GB VM); sandbox image node:22-slim (2.5 GB limit)",
        "- Static tools: Trivy 0.76.2, Semgrep 1.184.0 (p/default), gitleaks 8.30.1, built-in credential heuristic",
        "- Dynamic tools (when a local environment ran): Playwright Chromium / Firefox / WebKit, axe-core 4.10.3, Lighthouse 12.8.2, k6 2.4.0",
        f"- Runs included: {', '.join(r['id'][:8] + ' (' + r['created_at'][:16] + ')' for r in data['runs']) or 'none'}",
    ]
    return lines


def write_project(data: dict[str, Any]) -> dict[str, Any]:
    project = data["project"]
    folder = OUT / "projects" / slug(project["name"])
    folder.mkdir(parents=True, exist_ok=True)
    results, findings = data["results"], data["findings"]
    counts = Counter(r["status"] for r in results)
    real = [f for f in findings if f["classification"] in ("confirmed_defect", "potential_defect") and f["status"] != "false_positive"]
    real.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["classification"], f["title"]))
    confirmed = [f for f in real if f["classification"] == "confirmed_defect"]
    potential = [f for f in real if f["classification"] == "potential_defect"]
    environment = [f for f in findings if f["classification"] == "environment_failure"]
    generated = datetime.now(UTC).isoformat(timespec="minutes")

    # ---------------- TEST_RESULTS.md
    lines = [f"# Test results: {project['name']}", "", f"Generated {generated} from stored results (newest result per check).", "",
             "| State | Count |", "|---|---|", *[f"| {LABEL[s]} | {counts.get(s, 0)} |" for s in STATES], f"| **Total** | **{len(results)}** |", "",
             "Blocked, skipped and not-tested checks have **no verdict** and are not counted as passing.", "",
             "| Key | Category | Check | State | Duration | Result / exact reason | Evidence | Run |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in results:
        evidence = ", ".join(f"{e['kind']}:{e['id'][:8]}" for e in r["evidence"][:4]) or "-"
        lines.append(f"| {r['case']['key']} | {r['case']['category']} | {cell(r['case']['title'], 110)} | {LABEL[r['status']]} | "
                     f"{r['duration_ms'] / 1000:.0f}s | {cell(r['message'], 260)} | {evidence} | {r['run_id'][:8]} |")  # fmt: skip
    lines += ["", "Evidence ids refer to rows of the `evidence` table; open them in the dashboard under the run "
              "(`/runs/<run id>`) or fetch `GET /api/evidence/<id>/file` for screenshots. Files are stored under `data/evidence/<run id>/`."]  # fmt: skip
    (folder / "TEST_RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------------- FINDINGS.md
    lines = [f"# Findings: {project['name']}", "", f"Generated {generated}.", "",
             f"- Confirmed defects (the platform observed the behaviour itself): **{len(confirmed)}**",
             f"- Potential defects (reported by a scanner or heuristic, not independently reproduced): **{len(potential)}**",
             f"- Environment-related (not evidence of a defect): {len(environment)}", "",
             "Scanner findings are leads to review, not proven vulnerabilities. Secret values are redacted by the tools.", ""]  # fmt: skip
    for title, group in (("Confirmed defects", confirmed), ("Potential defects", potential), ("Environment-related observations", environment)):
        lines += [f"## {title} ({len(group)})", ""]
        if not group:
            lines += ["None.", ""]
            continue
        lines += ["| # | Severity | Category | Finding | Where | Tool | Confidence |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {i} | {f['severity']} | {f['category']} | {cell(f['title'], 150)} | `{cell(f['affected_component'], 90)}` | "
                  f"{f['source_tool']} | {f['confidence']:.0%} |" for i, f in enumerate(group, 1)]  # fmt: skip
        lines.append("")
        for i, f in enumerate(group, 1):
            if SEVERITIES.index(f["severity"]) > 1 and title != "Confirmed defects":
                continue  # details for confirmed defects and for high/critical potential ones
            lines += [f"### {title[:9]} {i}. {cell(f['title'], 200)}", "",
                      f"- Severity: {f['severity']} · {CLASS_LABEL[f['classification']]} · confidence {f['confidence']:.0%} · tool {f['source_tool']}",
                      f"- Where: `{f['affected_component']}`"]  # fmt: skip
            if f.get("expected_behavior") or f.get("actual_behavior"):
                lines += [f"- Expected: {cell(f['expected_behavior'], 300)}", f"- Actual: {cell(f['actual_behavior'], 300)}"]
            lines += ["- Reproduction:", *[f"  {n}. {cell(step, 300)}" for n, step in enumerate(f["reproduction_steps"], 1)],
                      f"- Recommended fix: {cell(f['remediation'], 500)}",
                      f"- Evidence: {', '.join(e['kind'] + ':' + e['id'][:8] for e in f['evidence'][:5]) or 'see the test result of the run'} (finding id `{f['id']}`)",
                      "", "```text", f["description"][:1800], "```", ""]  # fmt: skip
    (folder / "FINDINGS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------------- QA_REPORT.md
    by_severity = Counter(f["severity"] for f in real)
    high = [f for f in real if f["severity"] in ("critical", "high")]
    lines = [f"# QA report: {project['name']}", "", f"Generated {generated}. Details: [TEST_RESULTS.md](TEST_RESULTS.md), [FINDINGS.md](FINDINGS.md).", "",
             "## Test environment", "", *environment_lines(data), "",
             "## What was tested", "", *category_table(results), "",
             "## Result summary", "", "| State | Count |", "|---|---|", *[f"| {LABEL[s]} | {counts.get(s, 0)} |" for s in STATES], "",
             f"{counts.get('passed', 0) + counts.get('failed', 0)} of {len(results)} planned checks reached a pass/fail verdict.", "",
             "## Findings", "", f"- Confirmed defects: {len(confirmed)}", f"- Potential defects: {len(potential)}",
             "- By severity: " + (", ".join(f"{by_severity[s]} {s}" for s in SEVERITIES if by_severity[s]) or "none"), "",
             f"## High-risk issues ({len(high)})", ""]  # fmt: skip
    lines += [f"- **{f['severity']}** · {CLASS_LABEL[f['classification']]} · {cell(f['title'], 170)} (`{cell(f['affected_component'], 80)}`) - "
              f"{cell(f['remediation'], 200)}" for f in high[:40]] or ["None recorded."]  # fmt: skip
    if len(high) > 40:
        lines.append(f"- … and {len(high) - 40} more in FINDINGS.md")
    blocked = [r for r in results if r["status"] in ("blocked", "not_tested", "skipped")]
    lines += ["", f"## What could not be tested ({len(blocked)})", ""]
    lines += [f"- {r['case']['key']} {cell(r['case']['title'], 110)}: **{LABEL[r['status']]}** - {cell(r['message'], 260)}" for r in blocked] or ["Nothing."]
    lines += ["", "## Coverage notes and remaining limitations", "", *[f"- {cell(n, 400)}" for n in data["notes"]],
              "- No deployed (staging or production) environment was accessed. Dynamic results, where present, describe an isolated local "
              "copy built from the committed example configuration, not the live system.",
              "- No test accounts were available: authenticated areas, role-based access and data-changing journeys were not exercised."]  # fmt: skip
    (folder / "QA_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"name": project["name"], "folder": folder.relative_to(ROOT).as_posix(), "counts": counts, "confirmed": confirmed,
            "potential": potential, "high": high, "results": results, "blocked": blocked, "data": data}  # fmt: skip


def write_consolidated(projects: list[dict[str, Any]]) -> None:
    generated = datetime.now(UTC).isoformat(timespec="minutes")
    lines = ["# Phase 2: all projects report", "", f"Generated {generated} from stored results.", "",
             "## Summary", "", "| Project | Checks | Passed | Failed | Blocked | Not tested | Confirmed defects | Potential defects | High / critical |",
             "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for p in projects:
        c = p["counts"]
        lines.append(f"| [{p['name']}](projects/{slug(p['name'])}/QA_REPORT.md) | {len(p['results'])} | {c.get('passed', 0)} | {c.get('failed', 0)} | "
                     f"{c.get('blocked', 0)} | {c.get('not_tested', 0) + c.get('skipped', 0)} | {len(p['confirmed'])} | {len(p['potential'])} | {len(p['high'])} |")  # fmt: skip
    lines += ["", "## Coverage by test category", "", "| Test category | " + " | ".join(p["name"] for p in projects) + " |",
              "|---|" + "---|" * len(projects)]  # fmt: skip
    for label, matches in CATEGORY_MATRIX:
        cells = []
        for p in projects:
            rows = [r for r in p["results"] if matches(r["case"])]
            counts = Counter(r["status"] for r in rows)
            cells.append(", ".join(f"{counts[s]} {LABEL[s].lower()}" for s in STATES if counts[s]) or "not planned")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "## High-risk issues", ""]
    for p in projects:
        lines += [f"### {p['name']} ({len(p['high'])})", ""]
        lines += [f"- **{f['severity']}** · {CLASS_LABEL[f['classification']]} · {cell(f['title'], 170)} (`{cell(f['affected_component'], 80)}`)"
                  for f in p["high"][:25]] or ["None recorded."]  # fmt: skip
        if len(p["high"]) > 25:
            lines.append(f"- … and {len(p['high']) - 25} more: see projects/{slug(p['name'])}/FINDINGS.md")
        lines.append("")
    lines += ["## What was blocked or not tested, and why", ""]
    for p in projects:
        reasons = Counter(cell(r["message"], 170) for r in p["blocked"])
        lines += [f"### {p['name']}", "", *[f"- {count}× {reason}" for reason, count in reasons.most_common(12)], ""]
    lines += ["## How to read this report", "",
              "- **Confirmed** = the platform reproduced the behaviour itself (a build that fails, a route that errors, a failing test).",
              "- **Potential** = reported by a scanner or heuristic. It is a lead to review, not a proven vulnerability.",
              "- A check without a verdict (blocked / not tested) says nothing about quality in that area.",
              "- No deployed environment, production data or real credential was accessed. Source repositories were only read."]  # fmt: skip
    (OUT / "PHASE_2_ALL_PROJECTS_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    names = sys.argv[1:]
    if not names:
        print(__doc__)
        return 2
    client = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {ENV['QAI_API_TOKEN']}"}, timeout=120, trust_env=False)
    projects = client.get("/projects", params={"limit": 200}).raise_for_status().json()["items"]
    written = []
    for name in names:
        matching = sorted((p for p in projects if p["name"] == name), key=lambda p: p["created_at"])
        if not matching:
            print(f"no project named {name!r}")
            continue
        summary = write_project(collect(client, matching[-1]))
        written.append(summary)
        print(f"{name}: {len(summary['results'])} checks, {len(summary['confirmed'])} confirmed, {len(summary['potential'])} potential -> {summary['folder']}")
    if written:
        write_consolidated(written)
        print("consolidated -> reports/PHASE_2_ALL_PROJECTS_REPORT.md")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
