"""Additional browser checks: route sweep, forms and interactive elements, visual regression, PWA.

Registered on the browser engine at import time (see the bottom of this file). All checks are
read-only against the target: nothing is submitted and no data is created.
"""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from PIL import Image, ImageChops
from playwright.sync_api import Error as PlaywrightError

from app.domain import Category, Classification, ResultStatus, Severity
from app.engines import browser
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft

VISUAL_VIEWPORTS = {"desktop": browser.DESKTOP, "mobile": browser.VIEWPORTS["mobile"]}
VISUAL_THRESHOLD = 0.01  # share of pixels that must differ before a change is reported
PIXEL_TOLERANCE = 24  # per-channel difference ignored as anti-aliasing / font rendering noise
MAX_SWEEP_ROUTES = 25
_LOGIN_HINT = ("login", "signin", "sign-in", "auth", "otp")
_FORMS_JS = """() => [...document.forms].map((form, index) => {
  const controls = [...form.elements].filter(el => !['hidden', 'submit', 'button', 'reset'].includes(el.type) && el.tagName !== 'FIELDSET' && el.tagName !== 'BUTTON');
  const unlabeled = controls.filter(el => !(el.labels && el.labels.length) && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby') && !el.title);
  return {
    index, id: form.id || null, method: (form.getAttribute('method') || 'get').toLowerCase(), action: form.getAttribute('action') || '',
    controls: controls.length,
    constrained: controls.filter(el => el.required || el.pattern || el.minLength > 0 || el.maxLength > 0 || el.min || el.max || ['email', 'url', 'number', 'tel', 'date'].includes(el.type)).length,
    unlabeled: unlabeled.map(el => el.name || el.id || el.type).slice(0, 10),
    hasSubmit: !!form.querySelector('[type=submit], button:not([type=button])'),
    emptyIsValid: form.checkValidity(),
    passwordWithoutAutocomplete: [...form.querySelectorAll('input[type=password]')].filter(el => !el.autocomplete).length,
  };
})"""
_INTERACTIVE_JS = """() => {
  const name = el => (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || el.value || '').trim()
    || (el.querySelector('img[alt]:not([alt=""]), svg title') ? 'has-alt' : '') || (el.getAttribute('aria-labelledby') ? 'labelled' : '');
  const describe = el => el.outerHTML.slice(0, 140);
  return {
    buttons: document.querySelectorAll('button, [role=button]').length,
    links: document.querySelectorAll('a').length,
    namelessButtons: [...document.querySelectorAll('button, [role=button]')].filter(el => !name(el)).map(describe).slice(0, 10),
    deadLinks: [...document.querySelectorAll('a')].filter(a => !a.hasAttribute('href') || ['#', '', 'javascript:void(0)', 'javascript:;'].includes(a.getAttribute('href'))).filter(a => !a.getAttribute('role')).map(describe).slice(0, 10),
    dialogs: document.querySelectorAll('dialog, [role=dialog]').length,
    tables: document.querySelectorAll('table').length,
    headings: [...document.querySelectorAll('h1, h2, h3, h4, h5, h6')].map(h => Number(h.tagName[1])),
    h1: document.querySelectorAll('h1').length,
    title: document.title,
    lang: document.documentElement.lang || null,
  };
}"""


def _static_routes(ctx: EngineContext, spec: dict[str, Any]) -> list[str]:
    routes = [str(r) for r in spec.get("routes", [])]
    return [r for r in routes if "[" not in r and ":" not in r][:MAX_SWEEP_ROUTES]


def _route_sweep(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    """Open every static route found in the source code and record what actually happens."""
    routes = _static_routes(ctx, spec)
    if not routes:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "no static routes were discovered in the source code")
    assert ctx.target_url
    origin = browser._origin(ctx.target_url)
    results: list[dict[str, Any]] = []
    findings: list[FindingDraft] = []
    evidence: list[EvidenceDraft] = []
    with browser._session(ctx, browser.DESKTOP) as (_, page, log):
        for route in routes:
            url = urljoin(origin + "/", route.lstrip("/"))
            before = len(log.page_errors), len(log.console_errors)
            try:
                status = browser._goto(page, url)
            except PlaywrightError as exc:
                results.append({"route": route, "error": str(exc).splitlines()[0][:200]})
                continue
            final_path = urlsplit(page.url).path
            redirected = final_path.rstrip("/") != urlsplit(url).path.rstrip("/")
            entry: dict[str, Any] = {
                "route": route, "status": status, "final_path": final_path, "title": page.title()[:120],
                "redirected_to_login": redirected and any(hint in final_path.lower() for hint in _LOGIN_HINT),
                "page_errors": log.page_errors[before[0]:], "console_errors": len(log.console_errors) - before[1],
            }  # fmt: skip
            results.append(entry)
            problem = None
            if status is None or status >= 500:
                problem = (
                    Severity.HIGH,
                    f"Route {route} returns HTTP {status}",
                    "The route exists in the source code but the server fails to render it.",
                )
            elif status == 404 and not redirected:
                problem = (
                    Severity.MEDIUM,
                    f"Route {route} returns 404",
                    "The route is defined in the source code but is not served.",
                )
            elif entry["page_errors"]:
                problem = (Severity.HIGH, f"Uncaught JavaScript error on {route}", "\n".join(entry["page_errors"][:5]))
            if problem:
                shot = browser._shot(ctx, page, f"route {route}")
                evidence.append(shot)
                findings.append(FindingDraft(
                    key=f"route:{route}:{problem[1][:80]}", category=Category.FUNCTIONAL, severity=problem[0], title=problem[1],
                    description=problem[2], source_tool="playwright", affected_component=url, confidence=0.9,
                    reproduction_steps=[f"Open {url} in Chromium.", "Check the response status and the DevTools console."],
                    remediation="Fix the page or remove the route.", facts={"kind": "route", **entry}, evidence=[shot],
                    classification=Classification.CONFIRMED, expected="The route renders without server or script errors.",
                    actual=problem[1],
                ))  # fmt: skip
    protected = [r["route"] for r in results if r.get("redirected_to_login")]
    details = {"routes_checked": len(results), "routes_discovered": len(spec.get("routes", [])),
               "skipped_dynamic_routes": len(spec.get("routes", [])) - len(routes), "redirected_to_login": protected,
               "results": results,
               "note": "Routes that redirect to a login page were not tested further: no test credentials are configured."}  # fmt: skip
    evidence.insert(0, EvidenceDraft("log", "route sweep", json.dumps(details, indent=2)))
    verdict = ResultStatus.FAILED if findings else ResultStatus.PASSED
    return CaseOutcome(verdict, f"{len(results)} route(s) opened, {len(findings)} with errors, {len(protected)} behind login",
                       details, evidence, findings)  # fmt: skip


def _forms(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    """Inspect forms and interactive elements on the start page and its same-origin links (read-only)."""
    start = browser._url(ctx, spec.get("path", "/"))
    pages: dict[str, Any] = {}
    findings: list[FindingDraft] = []
    with browser._session(ctx, browser.DESKTOP) as (_, page, _log):
        status = browser._goto(page, start)
        if status is None or status >= 400:
            return CaseOutcome.blocked(f"page returned HTTP {status}; forms cannot be inspected")
        targets = [start, *[u for u in browser._same_origin_links(page, browser._origin(start)) if u != start]]
        for url in targets[: ctx.settings.browser_max_pages]:
            if url != start and ((status := browser._goto(page, url)) is None or status >= 400):
                continue
            forms, interactive = page.evaluate(_FORMS_JS), page.evaluate(_INTERACTIVE_JS)
            pages[url] = {"forms": forms, **interactive}
            path = urlsplit(url).path
            for form in forms:
                if form["unlabeled"]:
                    findings.append(_page_finding(f"form-labels:{path}:{form['index']}", Severity.MEDIUM, Category.ACCESSIBILITY,
                                                  f"Form on {path} has {len(form['unlabeled'])} control(s) without a label",
                                                  f"Controls: {', '.join(form['unlabeled'])}", url,
                                                  "Associate a <label> (or aria-label) with every form control."))  # fmt: skip
                if form["controls"] and form["constrained"] == 0 and form["method"] == "post":
                    findings.append(_page_finding(f"form-validation:{path}:{form['index']}", Severity.LOW, Category.FUNCTIONAL,
                                                  f"POST form on {path} declares no client-side validation",
                                                  "No control is required or constrained. Server-side validation was not exercised "
                                                  "(nothing is submitted by this check).", url,
                                                  "Add validation attributes for immediate feedback; always validate on the server.",
                                                  classification=Classification.POTENTIAL))  # fmt: skip
            if interactive["namelessButtons"]:
                findings.append(_page_finding(f"nameless-buttons:{path}", Severity.MEDIUM, Category.ACCESSIBILITY,
                                              f"{len(interactive['namelessButtons'])} button(s) without an accessible name on {path}",
                                              "\n".join(interactive["namelessButtons"]), url,
                                              "Give icon-only buttons an aria-label."))  # fmt: skip
            if interactive["deadLinks"]:
                findings.append(_page_finding(f"dead-links:{path}", Severity.LOW, Category.FUNCTIONAL,
                                              f"{len(interactive['deadLinks'])} link(s) that lead nowhere on {path}",
                                              "\n".join(interactive["deadLinks"]), url,
                                              "Use a <button> for actions, or give the link a real destination."))  # fmt: skip
            levels = interactive["headings"]
            skipped = [f"h{a}→h{b}" for a, b in zip(levels, levels[1:], strict=False) if b > a + 1]
            if interactive["h1"] != 1 or skipped:
                findings.append(_page_finding(f"headings:{path}", Severity.LOW, Category.ACCESSIBILITY,
                                              f"Heading structure problem on {path}",
                                              f"{interactive['h1']} <h1> element(s); skipped levels: {', '.join(skipped[:8]) or 'none'}.", url,
                                              "Use exactly one <h1> and do not skip heading levels."))  # fmt: skip
    details = {"pages": pages, "forms_found": sum(len(p["forms"]) for p in pages.values()),
               "scope": "read-only inspection: forms are not submitted, so server-side validation, uploads and "
                        "multi-step journeys are NOT covered by this check"}  # fmt: skip
    evidence = [EvidenceDraft("log", "forms and interactive elements", json.dumps(details, indent=2))]
    for finding in findings:
        finding.evidence = evidence
    verdict = ResultStatus.FAILED if findings else ResultStatus.PASSED
    return CaseOutcome(verdict, f"{details['forms_found']} form(s) on {len(pages)} page(s), {len(findings)} issue(s)", details,
                       evidence, findings)  # fmt: skip


def _page_finding(key: str, severity: Severity, category: Category, title: str, description: str, url: str, remediation: str,
                  classification: Classification = Classification.CONFIRMED) -> FindingDraft:  # fmt: skip
    return FindingDraft(key=key, category=category, severity=severity, title=title[:400], description=description,
                        source_tool="playwright", affected_component=url, confidence=0.85, remediation=remediation,
                        reproduction_steps=[f"Open {url}.", "Inspect the element(s) named in this finding with DevTools."],
                        facts={"kind": "page_inspection", "check": key.split(":")[0], "url": url}, classification=classification)  # fmt: skip


def baseline_key(browser_name: str, viewport: str, path: str) -> str:
    return f"{browser_name}|{viewport}|{path}"


def compare_images(baseline: Path, current: Path, diff_out: Path) -> tuple[float, tuple[int, int], tuple[int, int]]:
    """Return (share of differing pixels, baseline size, current size) and write a diff image."""
    with Image.open(baseline) as first, Image.open(current) as second:
        a, b = first.convert("RGB"), second.convert("RGB")
        size_a, size_b = a.size, b.size
        width, height = max(size_a[0], size_b[0]), max(size_a[1], size_b[1])
        canvas_a, canvas_b = Image.new("RGB", (width, height), "white"), Image.new("RGB", (width, height), "white")
        canvas_a.paste(a, (0, 0))
        canvas_b.paste(b, (0, 0))
        mask = ImageChops.difference(canvas_a, canvas_b).convert("L").point(lambda value: 255 if value > PIXEL_TOLERANCE else 0)
        changed = sum(mask.histogram()[255:])
        highlight = Image.composite(
            Image.new("RGB", (width, height), (255, 0, 80)), canvas_b.point(lambda v: int(v * 0.45 + 140)), mask
        )
        highlight.save(diff_out)
    return changed / float(width * height), size_a, size_b


def _visual(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    """Capture screenshots and compare them with the approved baselines of this project."""
    path = str(spec.get("path", "/"))
    url = browser._url(ctx, path)
    ctx.evidence_dir.mkdir(parents=True, exist_ok=True)
    captures: dict[str, Any] = {}
    evidence: list[EvidenceDraft] = []
    findings: list[FindingDraft] = []
    new_baselines: dict[str, str] = {}
    for name, viewport in VISUAL_VIEWPORTS.items():
        with browser._session(ctx, viewport) as (_, page, _log):
            status = browser._goto(page, url)
            if status is None or status >= 400:
                return CaseOutcome.blocked(f"page returned HTTP {status}; nothing to capture")
            page.add_style_tag(
                content="*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }"
            )
            shot = ctx.evidence_dir / f"visual-{uuid.uuid4().hex}.png"
            page.screenshot(path=str(shot), full_page=True)
        key = baseline_key("chromium", name, path)
        evidence.append(EvidenceDraft("screenshot", f"current {name} {path}", file=shot))
        baseline = ctx.baselines.get(key)
        if baseline is None or not baseline.is_file():
            new_baselines[key] = str(shot)
            captures[name] = {"baseline": "created from this run (nothing to compare yet)"}
            continue
        diff = ctx.evidence_dir / f"visual-diff-{uuid.uuid4().hex}.png"
        ratio, size_before, size_after = compare_images(baseline, shot, diff)
        captures[name] = {"changed_pixels_ratio": round(ratio, 5), "baseline_size": size_before, "current_size": size_after,
                          "sha256": hashlib.sha256(shot.read_bytes()).hexdigest()}  # fmt: skip
        if ratio > VISUAL_THRESHOLD:
            diff_evidence = EvidenceDraft("screenshot", f"difference {name} {path} (changed pixels in red)", file=diff)
            evidence.append(diff_evidence)
            findings.append(FindingDraft(
                key=f"visual:{key}", category=Category.VISUAL, severity=Severity.LOW,
                title=f"Visual change on {path} ({name}): {ratio:.1%} of pixels differ from the baseline",
                description=f"Baseline {size_before[0]}x{size_before[1]}, current {size_after[0]}x{size_after[1]}. "
                            "This may be an intended design change: review the diff image, then accept the new baseline or fix the regression.",
                source_tool="visual-diff", affected_component=url, confidence=0.6,
                reproduction_steps=[f"Open {url} at {viewport['width']}x{viewport['height']}.", "Compare with the stored baseline screenshot."],
                remediation="If intended, accept the new baseline in the dashboard. Otherwise fix the layout regression.",
                facts={"kind": "visual_change", "key": key, "ratio": ratio}, evidence=[diff_evidence],
                classification=Classification.POTENTIAL,
            ))  # fmt: skip
    details = {"url": url, "captures": captures, "new_baselines": new_baselines, "threshold": VISUAL_THRESHOLD,
               "tolerance": "per-channel differences up to 24/255 are ignored (anti-aliasing)"}  # fmt: skip
    if findings:
        return CaseOutcome(
            ResultStatus.FAILED, f"{len(findings)} viewport(s) differ from the baseline", details, evidence, findings
        )
    if len(new_baselines) == len(VISUAL_VIEWPORTS):
        return CaseOutcome(ResultStatus.NOT_TESTED, "first run: baseline screenshots captured, nothing to compare against yet",
                           details, evidence)  # fmt: skip
    return CaseOutcome(ResultStatus.PASSED, "screenshots match the approved baselines", details, evidence)


def _pwa(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    url = browser._url(ctx, spec.get("path", "/"))
    with browser._session(ctx, browser.VIEWPORTS["mobile"], allow_service_workers=True) as (context, page, _log):
        status = browser._goto(page, url)
        if status is None or status >= 400:
            return CaseOutcome.blocked(f"page returned HTTP {status}")
        manifest_href = page.evaluate("() => document.querySelector('link[rel=manifest]')?.href || null")
        page.wait_for_timeout(1500)
        workers = page.evaluate(
            "async () => 'serviceWorker' in navigator ? (await navigator.serviceWorker.getRegistrations()).map(r => r.scope) : []"
        )
        manifest: dict[str, Any] | None = None
        manifest_error = None
        if manifest_href:
            response = context.request.get(manifest_href)
            try:
                manifest = response.json() if response.ok else None
            except (PlaywrightError, ValueError):
                manifest_error = "manifest is not valid JSON"
            if not response.ok:
                manifest_error = f"manifest request returned HTTP {response.status}"
        theme = page.evaluate("() => document.querySelector('meta[name=theme-color]')?.content || null")
    if not manifest_href and not workers:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "no web app manifest and no service worker: the site is not a PWA",
                           {"url": url})  # fmt: skip
    problems = []
    if manifest_error or manifest is None:
        problems.append(manifest_error or "no web app manifest is linked")
    else:
        icons = [str(i.get("sizes", "")) for i in manifest.get("icons", []) if isinstance(i, dict)]
        for field_name in ("name", "start_url", "display", "icons"):
            if not manifest.get(field_name):
                problems.append(f"manifest has no '{field_name}'")
        if icons and not any("192" in size for size in icons):
            problems.append("no 192x192 icon")
        if icons and not any("512" in size for size in icons):
            problems.append("no 512x512 icon")
        if manifest.get("display") == "browser":
            problems.append("display is 'browser' (not installable as a standalone app)")
    if not workers:
        problems.append("no service worker is registered (no offline support)")
    details = {"url": url, "manifest_url": manifest_href, "manifest": manifest, "service_worker_scopes": workers,
               "theme_color": theme, "problems": problems,
               "not_checked": ["offline behaviour", "cache update behaviour", "install prompt"]}  # fmt: skip
    evidence = [EvidenceDraft("log", "PWA inspection", json.dumps(details, indent=2))]
    if not problems:
        return CaseOutcome(ResultStatus.PASSED, "manifest and service worker are present and complete", details, evidence)
    return CaseOutcome(
        ResultStatus.FAILED, f"{len(problems)} PWA installability problem(s)", details, evidence,
        [FindingDraft(key="pwa:installability", category=Category.COMPATIBILITY, severity=Severity.LOW,
                      title=f"PWA setup is incomplete ({len(problems)} problem(s))", description="\n".join(f"- {p}" for p in problems),
                      source_tool="playwright", affected_component=url, confidence=0.85,
                      reproduction_steps=[f"Open {url} in Chrome.", "DevTools → Application → Manifest / Service workers."],
                      remediation="Complete the web app manifest and register a service worker.",
                      facts={"kind": "pwa", "problems": problems}, evidence=evidence, classification=Classification.CONFIRMED)],
    )  # fmt: skip


for _name, _check in {"route_sweep": _route_sweep, "forms": _forms, "visual": _visual, "pwa": _pwa}.items():
    browser.register(_name, _check)
