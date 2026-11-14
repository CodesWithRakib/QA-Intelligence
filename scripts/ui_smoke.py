"""Dashboard smoke test against the RUNNING stack (scripts/dev.ps1).

Opens every dashboard screen in headless Chromium, including detail screens for existing data, and
fails on: HTTP errors from the API proxy, uncaught page errors, console errors, a missing page
heading, or a visible error banner. Screenshots are written to data/evidence/ui-smoke/.

Usage: backend/.venv/Scripts/python scripts/ui_smoke.py
"""

import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ENV = dotenv_values(ROOT / ".env")
DASHBOARD = "http://127.0.0.1:3000"
API = f"http://127.0.0.1:{ENV.get('QAI_API_PORT', '8000')}/api"
OUT = ROOT / "data" / "evidence" / "ui-smoke"
STATIC = ["/", "/projects", "/projects/import", "/plans", "/runs", "/findings", "/repairs", "/security", "/performance",
          "/accessibility", "/reports", "/settings/ai", "/settings/execution", "/integrations", "/agents", "/audit", "/system"]


def first_id(client: httpx.Client, path: str) -> str | None:
    items = client.get(path, params={"limit": 1}).raise_for_status().json()["items"]
    return items[0]["id"] if items else None


def main() -> int:
    client = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {ENV['QAI_API_TOKEN']}"}, trust_env=False, timeout=30)
    routes = list(STATIC)
    skipped = []
    for label, list_path, template in [
        ("project", "/projects", ["/projects/{id}", "/projects/{id}/architecture"]),
        ("plan", "/plans", ["/plans/{id}"]), ("run", "/runs", ["/runs/{id}"]), ("finding", "/findings", ["/findings/{id}"]),
        ("repair", "/repairs", ["/repairs/{id}"]), ("report", "/reports", ["/reports/{id}"]),
    ]:
        entity = first_id(client, list_path)
        if entity:
            routes += [t.format(id=entity) for t in template]
        else:
            skipped.append(label)
    OUT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for viewport in ({"width": 1366, "height": 768}, {"width": 390, "height": 844}):
            page = browser.new_context(viewport=viewport).new_page()
            problems: list[str] = []
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            page.on("console", lambda m: problems.append(f"console: {m.text[:200]}") if m.type == "error" else None)
            page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
            for route in routes:
                problems.clear()
                page.goto(DASHBOARD + route, wait_until="networkidle")
                page.wait_for_selector("h1", timeout=15000)
                heading = page.locator("h1").first.inner_text()
                alerts = page.locator('[role="alert"]').all_inner_texts()
                overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
                name = f"{viewport['width']}{route.replace('/', '_') or '_home'}"[:120]
                page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
                issues = problems + [f"error banner: {a[:160]}" for a in alerts if a.strip()] + (["page overflows horizontally"] if overflow else [])
                status = "FAIL" if issues else "PASS"
                print(f"[{status}] {viewport['width']}px {route} - {heading}" + (f" :: {issues}" if issues else ""), flush=True)
                failures += [f"{viewport['width']}px {route}: {i}" for i in issues]
        browser.close()
    print(f"\n{len(routes)} routes x 2 viewports; detail screens skipped for missing data: {skipped or 'none'}; failures: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
