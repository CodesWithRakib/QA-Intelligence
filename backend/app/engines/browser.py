"""Browser engine (Playwright / headless Chromium): page health, crawl, responsive, axe, web vitals.

Every network request made by the browser is checked against the outbound policy: navigations must
target an authorised host, subresources may only go to public or authorised addresses.
"""

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from playwright.sync_api import BrowserContext, Page, Request, Route, ViewportSize, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from app.domain import Category, ResultStatus, Severity
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft
from app.security.ssrf import TargetNotAllowed, validate_subresource, validate_target

AXE_SOURCE = Path(__file__).parent / "vendor" / "axe.min.js"
VIEWPORTS: dict[str, ViewportSize] = {"mobile": {"width": 375, "height": 667}, "tablet": {"width": 768, "height": 1024}}
DESKTOP: ViewportSize = {"width": 1366, "height": 768}
_IMPACT = {
    "critical": Severity.HIGH,
    "serious": Severity.MEDIUM,
    "moderate": Severity.LOW,
    "minor": Severity.INFO,
}
LCP_THRESHOLD_MS = 2500
TTFB_THRESHOLD_MS = 800
_VITALS_JS = """() => new Promise(resolve => {
  const nav = performance.getEntriesByType('navigation')[0];
  const resources = performance.getEntriesByType('resource');
  const out = {
    ttfb_ms: nav ? nav.responseStart : null,
    dom_content_loaded_ms: nav ? nav.domContentLoadedEventEnd : null,
    load_ms: nav ? nav.loadEventEnd : null,
    document_bytes: nav ? nav.transferSize : null,
    resource_count: resources.length,
    resource_bytes: resources.reduce((sum, r) => sum + (r.transferSize || 0), 0),
    lcp_ms: null, cls: 0,
  };
  try {
    new PerformanceObserver(list => { for (const e of list.getEntries()) out.lcp_ms = e.startTime; })
      .observe({type: 'largest-contentful-paint', buffered: true});
    new PerformanceObserver(list => { for (const e of list.getEntries()) if (!e.hadRecentInput) out.cls += e.value; })
      .observe({type: 'layout-shift', buffered: true});
  } catch (e) {}
  setTimeout(() => resolve(out), 800);
})"""
_OVERFLOW_JS = """() => {
  const width = window.innerWidth;
  const offenders = [];
  for (const el of document.querySelectorAll('body *')) {
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.right > width + 1) {
      offenders.push({tag: el.tagName.toLowerCase(), id: el.id, cls: String(el.className).slice(0, 60),
                      right: Math.round(rect.right)});
      if (offenders.length >= 5) break;
    }
  }
  return {scrollWidth: document.documentElement.scrollWidth, innerWidth: width, offenders};
}"""


@dataclass
class PageLog:
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    bad_responses: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    def problems(self) -> list[str]:
        return (
            [f"console error: {m}" for m in self.console_errors]
            + [f"uncaught exception: {m}" for m in self.page_errors]
            + [f"request failed: {m}" for m in self.failed_requests]
            + [f"HTTP error: {m}" for m in self.bad_responses]
        )


def _guard(allowed: list[str], log: PageLog) -> Any:
    def handle(route: Route, request: Request) -> None:
        if urlsplit(request.url).scheme not in {"http", "https"}:
            route.continue_()
            return
        try:
            if request.is_navigation_request() and request.frame.parent_frame is None:
                validate_target(request.url, allowed)
            else:
                validate_subresource(request.url, allowed)
        except TargetNotAllowed as exc:
            log.blocked.append(f"{request.url} ({exc})")
            route.abort("blockedbyclient")
            return
        route.continue_()

    return handle


@contextmanager
def _session(ctx: EngineContext, viewport: ViewportSize) -> Iterator[tuple[BrowserContext, Page, PageLog]]:
    log = PageLog()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                viewport=viewport, bypass_csp=True, service_workers="block", accept_downloads=False,
                user_agent="QA-Intelligence/0.1 (authorised local QA; Playwright Chromium)",
            )  # fmt: skip
            context.set_default_timeout(ctx.settings.browser_page_timeout_s * 1000)
            context.route("**/*", _guard(ctx.settings.allowed_targets, log))
            page = context.new_page()
            page.on("console", lambda m: log.console_errors.append(m.text[:500]) if m.type == "error" else None)
            page.on("pageerror", lambda e: log.page_errors.append(str(e)[:500]))
            page.on("requestfailed", lambda r: log.failed_requests.append(f"{r.method} {r.url} ({r.failure})"))
            page.on(
                "response",
                lambda r: log.bad_responses.append(f"{r.status} {r.url}") if r.status >= 400 else None,
            )
            yield context, page, log
        finally:
            browser.close()


def _goto(page: Page, url: str) -> int | None:
    response = page.goto(url, wait_until="load")
    # A page that keeps polling is not an error; "load" has already fired.
    with suppress(PlaywrightError):
        page.wait_for_load_state("networkidle", timeout=3000)
    return response.status if response else None


def _shot(ctx: EngineContext, page: Page, label: str) -> EvidenceDraft:
    ctx.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.evidence_dir / f"{uuid.uuid4().hex}.png"
    page.screenshot(path=str(path), full_page=True)
    return EvidenceDraft("screenshot", label, file=path)


def _url(ctx: EngineContext, path: str) -> str:
    assert ctx.target_url
    return urljoin(ctx.target_url.rstrip("/") + "/", path.lstrip("/"))


def _same_origin_links(page: Page, origin: str) -> list[str]:
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)")
    links = []
    for href in hrefs:
        clean = urldefrag(str(href)).url
        parts = urlsplit(clean)
        if f"{parts.scheme}://{parts.netloc}" == origin and clean not in links:
            links.append(clean)
    return links


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _page_health(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    url = _url(ctx, spec.get("path", "/"))
    with _session(ctx, DESKTOP) as (_, page, log):
        status = _goto(page, url)
        evidence = [_shot(ctx, page, f"page {url}")]
        title = page.title()
    problems = log.problems()
    details = {
        "url": url,
        "status": status,
        "title": title,
        "problems": problems,
        "blocked_requests": log.blocked,
    }
    evidence.append(EvidenceDraft("log", "browser console and network log", json.dumps(details, indent=2)))
    if status is None or status >= 400:
        problems.insert(0, f"document returned HTTP {status}")
    if not problems:
        return CaseOutcome(ResultStatus.PASSED, f"HTTP {status}, no console or network errors", details, evidence)
    findings = [
        FindingDraft(
            key=f"page:{spec.get('path', '/')}:{problem[:200]}",
            category=Category.BROWSER,
            severity=Severity.HIGH if problem.startswith(("document returned", "uncaught")) else Severity.MEDIUM,
            title=f"{problem[:160]}",
            description=f"Observed while loading {url} in headless Chromium.\n\n{problem}",
            source_tool="playwright",
            affected_component=url,
            confidence=0.9,
            reproduction_steps=[
                f"Open {url} in Chromium with DevTools open.",
                "Check the Console and Network tabs.",
            ],
            remediation="Fix the script error / missing resource so the page loads cleanly.",
            facts={"kind": "page_problem", "url": url, "problem": problem},
            evidence=evidence,
        )  # fmt: skip
        for problem in problems[:20]
    ]
    return CaseOutcome(ResultStatus.FAILED, f"{len(problems)} problem(s) on {url}", details, evidence, findings)


def _crawl(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    start = _url(ctx, spec.get("path", "/"))
    origin = _origin(start)
    queue: list[tuple[str, str]] = [(start, "(start)")]
    visited: dict[str, int | None] = {}
    broken: list[dict[str, Any]] = []
    with _session(ctx, DESKTOP) as (_, page, _log):
        while queue and len(visited) < ctx.settings.browser_max_pages:
            url, referrer = queue.pop(0)
            if url in visited:
                continue
            try:
                status = _goto(page, url)
            except PlaywrightError as exc:
                visited[url] = None
                broken.append({"url": url, "referrer": referrer, "status": None, "error": str(exc)[:200]})
                continue
            visited[url] = status
            if status is None or status >= 400:
                broken.append({"url": url, "referrer": referrer, "status": status})
                continue
            queue += [(link, url) for link in _same_origin_links(page, origin) if link not in visited]
    details = {"pages_visited": len(visited), "page_limit": ctx.settings.browser_max_pages,
               "not_visited_due_to_limit": len({u for u, _ in queue} - set(visited)), "statuses": visited, "broken": broken}  # fmt: skip
    evidence = [EvidenceDraft("log", "crawl log", json.dumps(details, indent=2))]
    findings = [
        FindingDraft(
            key=f"broken-link:{urlsplit(item['url']).path}",
            category=Category.BROWSER,
            severity=Severity.MEDIUM,
            title=f"Broken link: {urlsplit(item['url']).path} returns {item['status'] or 'no response'}",
            description=f"The link is present on {item['referrer']} but its target does not load.",
            source_tool="playwright",
            affected_component=item["referrer"],
            confidence=0.95,
            reproduction_steps=[f"Open {item['referrer']}.", f"Follow the link to {item['url']}."],
            remediation="Create the missing page, fix the href or remove the link.",
            facts={"kind": "broken_link", **item},
            evidence=evidence,
        )  # fmt: skip
        for item in broken
    ]
    verdict = ResultStatus.FAILED if broken else ResultStatus.PASSED
    return CaseOutcome(verdict, f"{len(visited)} page(s) visited, {len(broken)} broken link(s)", details, evidence, findings)


def _responsive(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    url = _url(ctx, spec.get("path", "/"))
    evidence: list[EvidenceDraft] = []
    findings: list[FindingDraft] = []
    details: dict[str, Any] = {"url": url, "viewports": {}}
    for name, viewport in VIEWPORTS.items():
        with _session(ctx, viewport) as (_, page, _log):
            status = _goto(page, url)
            if status is None or status >= 400:
                return CaseOutcome.blocked(f"page returned HTTP {status}; layout cannot be assessed")
            measured = page.evaluate(_OVERFLOW_JS)
            shot = _shot(ctx, page, f"{name} {viewport['width']}x{viewport['height']} {url}")
        evidence.append(shot)
        overflow = measured["scrollWidth"] > measured["innerWidth"] + 1
        details["viewports"][name] = {**viewport, **measured, "horizontal_overflow": overflow}
        if overflow:
            offenders = ", ".join(f"<{o['tag']}{'#' + o['id'] if o['id'] else ''}>" for o in measured["offenders"])
            findings.append(FindingDraft(
                key=f"overflow:{spec.get('path', '/')}:{name}", category=Category.BROWSER, severity=Severity.MEDIUM,
                title=f"Horizontal overflow on {name} viewport ({viewport['width']}px)",
                description=f"Content is {measured['scrollWidth']}px wide in a {measured['innerWidth']}px viewport. "
                            f"Elements extending past the viewport: {offenders or 'not identified'}.",
                source_tool="playwright", affected_component=url, confidence=0.9,
                reproduction_steps=[f"Open {url} at {viewport['width']}x{viewport['height']}.", "Scroll horizontally."],
                remediation="Use fluid widths (max-width: 100%), wrap wide tables in a scroll container.",
                facts={"kind": "responsive_overflow", "viewport": name, **measured}, evidence=[shot],
            ))  # fmt: skip
    verdict = ResultStatus.FAILED if findings else ResultStatus.PASSED
    return CaseOutcome(verdict, f"{len(findings)} viewport(s) with horizontal overflow", details, evidence, findings)


def _accessibility(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    start = _url(ctx, spec.get("path", "/"))
    axe = AXE_SOURCE.read_text(encoding="utf-8")
    findings: list[FindingDraft] = []
    pages: dict[str, Any] = {}
    with _session(ctx, DESKTOP) as (_, page, _log):
        status = _goto(page, start)
        if status is None or status >= 400:
            return CaseOutcome.blocked(f"page returned HTTP {status}; accessibility cannot be assessed")
        targets = [start, *[u for u in _same_origin_links(page, _origin(start)) if u != start]]
        for url in targets[: ctx.settings.browser_max_pages]:
            if url != start:
                status = _goto(page, url)
                if status is None or status >= 400:
                    pages[url] = {"skipped": f"HTTP {status}"}
                    continue
            page.evaluate(axe)
            result = page.evaluate("() => axe.run(document, {resultTypes: ['violations', 'incomplete']})")
            pages[url] = {"violations": len(result["violations"]), "needs_manual_review": len(result["incomplete"]),
                          "rules_passed": len(result["passes"])}  # fmt: skip
            for violation in result["violations"]:
                nodes = violation["nodes"][:5]
                snippet = "\n".join(f"- {' '.join(map(str, n['target']))}: {n['html'][:200]}" for n in nodes)
                findings.append(FindingDraft(
                    key=f"axe:{violation['id']}:{urlsplit(url).path}", category=Category.ACCESSIBILITY,
                    severity=_IMPACT.get(violation.get("impact") or "", Severity.LOW),
                    title=f"{violation['help']} ({violation['id']})"[:400],
                    description=f"{violation['description']}\n\n{len(violation['nodes'])} element(s) affected on {url}:\n{snippet}",
                    source_tool="axe-core", affected_component=url, confidence=0.9,
                    reproduction_steps=[f"Open {url}.", f"Run axe-core and look for rule `{violation['id']}`."],
                    remediation=f"See {violation['helpUrl']}",
                    facts={"kind": "axe", "rule": violation["id"], "impact": violation.get("impact"), "url": url,
                           "wcag": [t for t in violation.get("tags", []) if t.startswith("wcag")]},
                    evidence=[EvidenceDraft("tool_output", f"axe {violation['id']}", json.dumps(nodes, indent=2))],
                ))  # fmt: skip
    details = {
        "pages": pages,
        "note": "Automated checks cover only part of WCAG; manual review is still required.",
    }
    verdict = ResultStatus.FAILED if findings else ResultStatus.PASSED
    return CaseOutcome(
        verdict, f"{len(findings)} axe violation(s) on {len(pages)} page(s)", details,
                       [EvidenceDraft("log", "axe summary", json.dumps(details, indent=2))], findings)  # fmt: skip


def _web_performance(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    url = _url(ctx, spec.get("path", "/"))
    with _session(ctx, DESKTOP) as (_, page, _log):
        status = _goto(page, url)
        if status is None or status >= 400:
            return CaseOutcome.blocked(f"page returned HTTP {status}; performance cannot be measured")
        vitals = page.evaluate(_VITALS_JS)
    details = {"url": url, **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in vitals.items()},
               "thresholds": {"lcp_ms": LCP_THRESHOLD_MS, "ttfb_ms": TTFB_THRESHOLD_MS},
               "note": "Single lab measurement from the QA host (headless Chromium), not field data."}  # fmt: skip
    evidence = [EvidenceDraft("log", "navigation timing", json.dumps(details, indent=2))]
    slow = []
    if vitals["lcp_ms"] is None:
        details["lcp_note"] = "LCP was not reported by the browser for this page"
    elif vitals["lcp_ms"] > LCP_THRESHOLD_MS:
        slow.append(f"LCP {vitals['lcp_ms']:.0f} ms > {LCP_THRESHOLD_MS} ms")
    if vitals["ttfb_ms"] is not None and vitals["ttfb_ms"] > TTFB_THRESHOLD_MS:
        slow.append(f"TTFB {vitals['ttfb_ms']:.0f} ms > {TTFB_THRESHOLD_MS} ms")
    if not slow:
        return CaseOutcome(
            ResultStatus.PASSED,
            f"TTFB {vitals['ttfb_ms']:.0f} ms, load {vitals['load_ms']:.0f} ms",
            details,
            evidence,
        )
    return CaseOutcome(
        ResultStatus.FAILED, "; ".join(slow), details, evidence,
        [FindingDraft(
            key=f"web-perf:{spec.get('path', '/')}", category=Category.PERFORMANCE, severity=Severity.MEDIUM,
            title=f"Slow page load: {'; '.join(slow)}", description=json.dumps(details, indent=2),
            source_tool="playwright", affected_component=url, confidence=0.6,
            reproduction_steps=[f"Open {url} in Chromium and record a Performance trace."],
            remediation="Reduce server response time and render-blocking resources; optimise the largest element.",
            facts={"kind": "web_performance", **details}, evidence=evidence,
        )],
    )  # fmt: skip


_CHECKS = {
    "page_health": _page_health,
    "crawl": _crawl,
    "responsive": _responsive,
    "accessibility": _accessibility,
    "web_performance": _web_performance,
}


def run_case(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    if not ctx.target_url:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "no target URL configured for this plan")
    check = _CHECKS.get(spec.get("check", ""))
    if not check:
        return CaseOutcome.blocked(f"unknown browser check '{spec.get('check')}'")
    try:
        validate_target(ctx.target_url, ctx.settings.allowed_targets)
        return check(ctx, spec)
    except TargetNotAllowed as exc:
        return CaseOutcome.blocked(f"target not authorised: {exc}")
    except PlaywrightError as exc:
        return CaseOutcome.blocked(f"browser could not complete the check: {str(exc)[:300]}")
