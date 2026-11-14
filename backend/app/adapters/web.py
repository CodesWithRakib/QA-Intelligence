"""Web, API, backend and database adapters."""

from typing import Any

from app.adapters.base import Adapter, PlanContext, PlannedCase, PlanResult, ReadinessReport
from app.domain import Category, Engine, Readiness
from app.engines import (
    api,
    browser,
    browser_extra,
    database_static,
    environment,
    project_tests,
    sandbox,
    security_scan,
    web_tools,
)
from app.engines.base import CaseOutcome, EngineContext
from app.services.settings import ExecutionSettings

assert browser_extra  # imported for its side effect: registers the extra browser checks

MAX_API_OPERATIONS = 60
WEB_FRAMEWORKS = {"Next.js", "React", "Vue", "Nuxt", "Angular", "Svelte", "SvelteKit", "Astro", "Vite", "Express", "NestJS",
                  "Fastify", "FastAPI", "Django", "Flask", "Laravel", "WordPress", "Rails", "Spring Boot", "ASP.NET Core"}  # fmt: skip
PIPELINE_STEPS = (
    ("typecheck", "Type check"),
    ("lint", "Lint"),
    ("test", "Unit / integration tests"),
    ("build", "Production build"),
)


def _case(adapter: Adapter, prefix: str, title: str, category: Category, engine: Engine, spec: dict[str, Any] | None = None,
          priority: int = 2) -> PlannedCase:  # fmt: skip
    return PlannedCase(prefix, title, category, engine, {"adapter": adapter.name, **(spec or {})}, priority)


class WebDetectionAdapter(Adapter):
    """Reports what kind of web application this is. It plans nothing: detection happens at import."""

    name, title, agent = "web_detection", "Web detection", "project_discovery"

    def capabilities(self, plan: PlanContext) -> list[str]:
        detected = sorted(plan.frameworks & WEB_FRAMEWORKS)
        if plan.has_target and not detected:
            detected.append("running website (framework unknown: no source)")
        return detected

    def plan(self, plan: PlanContext) -> PlanResult:
        evidence = [f"{c.name}: {', '.join(c.frameworks) or 'no framework'} (from {c.details.get('manifest', 'manifest')})"
                    for c in plan.snapshot.components]  # fmt: skip
        return PlanResult(notes=[f"Detected stack - {item}" for item in evidence[:12]])


class ProjectPipelineAdapter(Adapter):
    """Build verification and the project's own tests, inside the sandbox."""

    name, title, agent = "project_pipeline", "Build and project tests", "functional_qa"
    needs_workspace = True

    def _root_node(self, plan: PlanContext) -> dict[str, Any] | None:
        for component in plan.snapshot.components:
            if component.path == "" and component.details.get("manifest") == "package.json":
                return dict(component.details)
        return None

    def capabilities(self, plan: PlanContext) -> list[str]:
        if plan.workspace is None or plan.is_apk:
            return []
        found = [f"python: {c['command']}" for c in plan.inventory.get("test_commands", []) if c["runtime"] == "python"]
        node = self._root_node(plan)
        if node:
            found += [f"node script: {step}" for step, _ in PIPELINE_STEPS if step in node.get("scripts", {})]
        return found

    def plan(self, plan: PlanContext) -> PlanResult:
        result = PlanResult()
        for command in plan.inventory.get("test_commands", []):
            if command["runtime"] == "python":
                result.cases.append(_case(self, "FUNC-TEST", f"Project test suite in sandbox: `{command['command']}` in "
                                          f"{command['component'] or '.'}", Category.FUNCTIONAL, Engine.PROJECT_TESTS,
                                          {"component": command["component"], "runtime": "python"}, 1))  # fmt: skip
        node = self._root_node(plan)
        if node:
            scripts = node.get("scripts", {})
            node_spec = {"package_manager_spec": node.get("package_manager_spec", ""), "has_lockfile": node.get("has_lockfile")}
            for step, label in PIPELINE_STEPS:
                if step not in scripts or "no test specified" in scripts[step]:
                    result.notes.append(f"No `{step}` script in the root package.json: {label.lower()} is not executed.")
                    continue
                category = Category.FUNCTIONAL if step == "test" else Category.BUILD
                result.cases.append(_case(self, "BUILD" if step != "test" else "FUNC-TEST",
                                          f"{label} in sandbox: `{scripts[step][:80]}`", category, Engine.PROJECT_TESTS,
                                          {"component": "", "runtime": "node", "step": step, "script": step, "node": node_spec}, 1))  # fmt: skip
        return result

    def environment(self, settings: ExecutionSettings) -> ReadinessReport:
        report = ReadinessReport(requirements=["Docker sandbox", "network access for dependency installation"])
        if not sandbox.docker_available():
            report.block(Readiness.BLOCKED, "Docker is not running")
        return report

    def readiness(self, ctx: EngineContext, spec: dict[str, Any]) -> ReadinessReport:
        report = super().readiness(ctx, spec)
        image = sandbox.RUNTIME_IMAGES.get(str(spec.get("runtime")), "")
        if report.runnable and image and not sandbox.image_present(image):
            report.block(Readiness.BLOCKED, f"sandbox image {image} is not pulled")
        if spec.get("runtime") == "node":
            report.requirements.append("about 2.5 GB of container memory")
        return report

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        return project_tests.run_case(ctx, spec)

    def cleanup(self, ctx: EngineContext) -> dict[str, Any]:
        project_tests.cleanup(ctx)
        return {"sandbox_volumes": "removed"}


class _BrowserAdapter(Adapter):
    needs_target = True

    def capabilities(self, plan: PlanContext) -> list[str]:
        return [f"running target {plan.target_label}"] if plan.has_target else []

    def environment(self, settings: ExecutionSettings) -> ReadinessReport:
        return ReadinessReport(requirements=["one headless browser process"])

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        return browser.run_case(ctx, spec)


class WebFunctionalAdapter(_BrowserAdapter):
    name, title, agent = "web_functional", "Web functional and cross-browser", "browser_qa"

    def plan(self, plan: PlanContext) -> PlanResult:
        result = PlanResult()
        add = result.cases.append
        add(_case(self, "BRW", "Home page loads without console or network errors (Chromium)", Category.BROWSER, Engine.BROWSER,
                  {"check": "page_health", "path": "/", "browser": "chromium"}, 1))  # fmt: skip
        for name in plan.settings.browsers:
            if name != "chromium":
                add(_case(self, "XBROWSER", f"Home page loads without errors in {name.capitalize()}", Category.COMPATIBILITY,
                          Engine.BROWSER, {"check": "page_health", "path": "/", "browser": name}))  # fmt: skip
        add(_case(self, "BRW", "Same-origin links resolve (bounded crawl)", Category.BROWSER, Engine.BROWSER,
                  {"check": "crawl", "path": "/"}))  # fmt: skip
        routes = sorted({r.path for r in plan.snapshot.routes})
        if routes:
            static = [r for r in routes if "[" not in r]
            add(_case(self, "FUNC-ROUTE", f"Every static route from the source code renders ({len(static)} of {len(routes)} routes)",
                      Category.FUNCTIONAL, Engine.BROWSER, {"check": "route_sweep", "routes": routes}, 1))  # fmt: skip
            if len(static) < len(routes):
                result.notes.append(
                    f"{len(routes) - len(static)} dynamic route(s) need real ids and are not opened by the route sweep."
                )
        else:
            result.notes.append("No frontend routes were discovered in source code: the route sweep is not planned.")
        add(_case(self, "FUNC-FORM", "Forms and interactive elements: labels, validation attributes, accessible names",
                  Category.FUNCTIONAL, Engine.BROWSER, {"check": "forms", "path": "/"}))  # fmt: skip
        add(_case(self, "RESP", "No horizontal overflow or tiny touch targets across 7 viewport classes", Category.BROWSER,
                  Engine.BROWSER, {"check": "responsive", "path": "/"}))  # fmt: skip
        add(_case(self, "PWA", "Progressive Web App: manifest, icons and service worker", Category.COMPATIBILITY, Engine.BROWSER,
                  {"check": "pwa", "path": "/"}, 3))  # fmt: skip
        result.notes.append("Authenticated journeys (login, role dashboards, CRUD, checkout, uploads) are not automated: "
                            "they need dedicated test accounts, which are not configured.")  # fmt: skip
        return result

    def readiness(self, ctx: EngineContext, spec: dict[str, Any]) -> ReadinessReport:
        report = super().readiness(ctx, spec)
        if spec.get("browser") in ("firefox", "webkit"):
            report.requirements.append(f"Playwright {spec['browser']} build")
        return report


class VisualAdapter(_BrowserAdapter):
    name, title, agent = "visual", "Visual regression", "browser_qa"

    def plan(self, plan: PlanContext) -> PlanResult:
        return PlanResult([_case(self, "VISUAL", "Screenshots match the approved baseline (desktop and mobile)", Category.VISUAL,
                                 Engine.BROWSER, {"check": "visual", "path": "/"})])  # fmt: skip


class AccessibilityAdapter(_BrowserAdapter):
    name, title, agent = "accessibility", "Accessibility", "accessibility_qa"

    def plan(self, plan: PlanContext) -> PlanResult:
        return PlanResult(
            [_case(self, "A11Y", "Automated accessibility rules (axe-core) on home and linked pages", Category.ACCESSIBILITY,
                   Engine.BROWSER, {"check": "accessibility", "path": "/"}, 1)],
            ["Keyboard traps, focus order and screen-reader behaviour are not automated (manual case)."],
        )  # fmt: skip


class WebPerformanceAdapter(Adapter):
    name, title, agent = "web_performance", "Web performance", "performance_qa"
    needs_target = True

    def capabilities(self, plan: PlanContext) -> list[str]:
        return [f"running target {plan.target_label}"] if plan.has_target else []

    def plan(self, plan: PlanContext) -> PlanResult:
        result = PlanResult()
        result.cases.append(_case(self, "PERF-WEB", "Page load timing, LCP and CLS (browser APIs)", Category.PERFORMANCE,
                                  Engine.BROWSER, {"check": "web_performance", "path": "/"}, 3))  # fmt: skip
        result.cases.append(_case(self, "PERF-LH", "Lighthouse audit: performance, accessibility, best practices, SEO",
                                  Category.PERFORMANCE, Engine.LIGHTHOUSE, {"path": "/"}, 3))  # fmt: skip
        if plan.managed_environment or web_tools.is_local_target(str(plan.target_url)):
            result.cases.append(_case(self, "PERF-LOAD", f"Bounded load test with k6 ({plan.settings.load_test_vus} users, "
                                      f"{plan.settings.load_test_duration_s}s)", Category.PERFORMANCE, Engine.LOAD_TEST,
                                      {"path": "/"}, 3))  # fmt: skip
        else:
            result.notes.append("Load testing is not planned: the target is not local. Testing an external system needs "
                                "explicit authorisation and scope.")  # fmt: skip
        return result

    def readiness(self, ctx: EngineContext, spec: dict[str, Any]) -> ReadinessReport:
        report = super().readiness(ctx, spec)
        engine = spec.get("_engine")
        if engine == Engine.LOAD_TEST.value and report.runnable:
            if not sandbox.docker_available():
                report.block(Readiness.BLOCKED, "Docker is not running (k6 runs in a container)")
            elif not sandbox.image_present(web_tools.K6_IMAGE):
                report.block(Readiness.BLOCKED, f"container image {web_tools.K6_IMAGE} is not pulled")
        return report

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        engine = spec.get("_engine")
        if engine == Engine.LIGHTHOUSE.value:
            return web_tools.lighthouse(ctx, spec)
        if engine == Engine.LOAD_TEST.value:
            return web_tools.load_test(ctx, spec)
        return browser.run_case(ctx, spec)


class WebSecurityAdapter(Adapter):
    name, title, agent = "web_security", "Security", "security_qa"

    def capabilities(self, plan: PlanContext) -> list[str]:
        found = []
        if plan.workspace is not None and not plan.is_apk:
            found += ["dependency audit", "secret scan", "static analysis"]
        if plan.has_target:
            found += ["security headers", "cookie flags", "CORS", "passive scan"]
        return found

    def plan(self, plan: PlanContext) -> PlanResult:
        result = PlanResult()
        add = result.cases.append
        if plan.workspace is not None and not plan.is_apk:
            add(_case(self, "SEC-DEP", "Known vulnerabilities in declared dependencies (Trivy)", Category.SECURITY,
                      Engine.DEPENDENCY_SCAN, priority=1))  # fmt: skip
            add(_case(self, "SEC-SAST", "Static application security testing (Semgrep, p/default rules)", Category.SECURITY,
                      Engine.STATIC_ANALYSIS, {"tool": "semgrep"}, 1))  # fmt: skip
            add(_case(self, "SEC-SECRET", "Secrets in the working tree (gitleaks)", Category.SECURITY, Engine.STATIC_ANALYSIS,
                      {"tool": "gitleaks"}, 1))  # fmt: skip
            if plan.has_git_history:
                add(_case(self, "SEC-SECRET", "Secrets in git history (gitleaks, read-only on the original repository)",
                          Category.SECURITY, Engine.STATIC_ANALYSIS, {"tool": "gitleaks-history"}, 1))  # fmt: skip
            else:
                result.notes.append(
                    "Git history secret scan not planned: the project was not imported from a local git repository."
                )
            add(_case(self, "SEC-SECRET", "Committed secrets in known token formats (Trivy)", Category.SECURITY,
                      Engine.STATIC_ANALYSIS, {"tool": "trivy-secret"}))  # fmt: skip
            add(_case(self, "SEC-SECRET", "Hard-coded credential assignments (built-in heuristic)", Category.SECURITY,
                      Engine.STATIC_ANALYSIS, {"tool": "builtin-secrets"}))  # fmt: skip
            containers = plan.inventory.get("containers") or {}
            if containers.get("dockerfiles") or containers.get("compose_services"):
                add(_case(self, "SEC-CONF", "Container and infrastructure configuration (Trivy misconfig)", Category.SECURITY,
                          Engine.STATIC_ANALYSIS, {"tool": "trivy-config"}))  # fmt: skip
        if plan.has_target:
            add(_case(self, "SEC-HDR", "Recommended HTTP security headers are present", Category.SECURITY, Engine.API,
                      {"check": "security_headers"}))  # fmt: skip
            add(_case(self, "SEC-COOKIE", "Cookies set by the start page carry HttpOnly / SameSite / Secure", Category.SECURITY,
                      Engine.API, {"check": "cookie_flags"}))  # fmt: skip
            add(_case(self, "SEC-CORS", "CORS does not grant access to an untrusted origin", Category.SECURITY, Engine.API,
                      {"check": "cors", "path": "/"}))  # fmt: skip
            if plan.managed_environment or web_tools.is_local_target(str(plan.target_url)):
                add(_case(self, "SEC-DAST", "OWASP ZAP baseline (spider + passive rules, no active attacks)", Category.SECURITY,
                          Engine.DAST, priority=3))  # fmt: skip
            else:
                result.notes.append("OWASP ZAP is not planned for a non-local target without explicit authorisation.")
            result.notes.append("Active (intrusive) security testing is never run automatically.")
        return result

    def readiness(self, ctx: EngineContext, spec: dict[str, Any]) -> ReadinessReport:
        engine, report = spec.get("_engine"), ReadinessReport()
        images = {Engine.DEPENDENCY_SCAN.value: security_scan.TRIVY_IMAGE, Engine.DAST.value: web_tools.ZAP_IMAGE}
        tool_images = {"semgrep": security_scan.SEMGREP_IMAGE, "gitleaks": security_scan.GITLEAKS_IMAGE,
                       "gitleaks-history": security_scan.GITLEAKS_IMAGE, "trivy-secret": security_scan.TRIVY_IMAGE,
                       "trivy-config": security_scan.TRIVY_IMAGE}  # fmt: skip
        image = images.get(str(engine)) or tool_images.get(str(spec.get("tool")))
        if image:
            if not sandbox.docker_available():
                report.block(Readiness.BLOCKED, "Docker is not running (the scanner runs in a container)")
            elif not sandbox.image_present(image):
                report.block(Readiness.BLOCKED, f"container image {image} is not pulled (scripts\\install-phase2-tools.ps1)")
            report.requirements.append(f"container {image}")
        if engine in (Engine.DEPENDENCY_SCAN.value, Engine.STATIC_ANALYSIS.value) and ctx.workspace is None:
            report.block(Readiness.REQUIRES_SOURCE, "this check needs the project's source files")
        if spec.get("tool") == "gitleaks-history" and (ctx.source_path is None or not (ctx.source_path / ".git").exists()):
            report.block(Readiness.REQUIRES_SOURCE, "the original local git repository is no longer available at its import path")
        if engine in (Engine.API.value, Engine.DAST.value):
            from app.adapters.base import target_readiness

            target_readiness(ctx, report)
        return report

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        engine = spec.get("_engine")
        if engine == Engine.DEPENDENCY_SCAN.value:
            return security_scan.dependency_scan(ctx, spec)
        if engine == Engine.STATIC_ANALYSIS.value:
            return security_scan.static_analysis(ctx, spec)
        if engine == Engine.DAST.value:
            return web_tools.zap_baseline(ctx, spec)
        return api.run_case(ctx, spec)


class ApiAdapter(Adapter):
    name, title, agent = "api", "API contract and negative tests", "api_qa"
    needs_target = True

    def capabilities(self, plan: PlanContext) -> list[str]:
        if not plan.has_target:
            return []
        operations = sum(1 for e in plan.snapshot.api_endpoints if e.origin == "openapi")
        return [f"{operations} OpenAPI operation(s)" if operations else "generic HTTP checks (no OpenAPI specification)"]

    def plan(self, plan: PlanContext) -> PlanResult:
        result = PlanResult()
        operations = [e for e in plan.snapshot.api_endpoints if e.origin == "openapi"]
        code_only = sum(1 for e in plan.snapshot.api_endpoints if e.origin == "code")
        if not operations:
            result.notes.append(f"No OpenAPI specification found ({code_only} endpoint(s) were discovered in code): contract "
                                "tests need a spec, so only generic API checks are planned.")  # fmt: skip
        if len(operations) > MAX_API_OPERATIONS:
            result.notes.append(f"The spec has {len(operations)} operations; the first {MAX_API_OPERATIONS} are planned.")
        secured = 0
        first_get: str | None = None
        for endpoint in operations[:MAX_API_OPERATIONS]:
            base = {"spec_file": endpoint.source_file, "method": endpoint.method, "path": endpoint.path}
            has_path_param = "{" in endpoint.path
            if endpoint.spec.get("secured"):
                secured += 1
            result.cases.append(_case(self, "API-CONTRACT", f"{endpoint.method} {endpoint.path}: documented status and response schema",
                                      Category.API, Engine.API, {"check": "operation", **base}, 1))  # fmt: skip
            if endpoint.method == "GET" and has_path_param and "404" in endpoint.spec.get("responses", []):
                result.cases.append(_case(self, "API-NEG", f"GET {endpoint.path}: unknown resource returns 404", Category.API,
                                          Engine.API, {"check": "negative_not_found", **base}))  # fmt: skip
            if endpoint.method in api.MUTATING and endpoint.spec.get("has_request_body") and not has_path_param:
                result.cases.append(_case(self, "API-NEG", f"{endpoint.method} {endpoint.path}: invalid body is rejected with 4xx",
                                          Category.API, Engine.API, {"check": "negative_invalid_body", **base}))  # fmt: skip
            if endpoint.method == "GET" and not has_path_param and first_get is None and not endpoint.spec.get("secured"):
                first_get = endpoint.path
        if secured:
            result.notes.append(f"{secured} operation(s) require authentication; without test credentials they are expected to "
                                "answer 401/403 and their contract checks will report that.")  # fmt: skip
        result.cases.append(_case(self, "API-NEG", "Unknown route returns a clean 404", Category.API, Engine.API,
                                  {"check": "unknown_route"}))  # fmt: skip
        if first_get:
            result.cases.append(_case(self, "PERF-API", f"Latency sample for GET {first_get}", Category.PERFORMANCE, Engine.API,
                                      {"check": "latency", "path": first_get}, 3))  # fmt: skip
        result.notes.append("GraphQL and WebSocket testing are not implemented.")
        return result

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        return api.run_case(ctx, spec)


class DatabaseAdapter(Adapter):
    name, title, agent = "database", "Database (static)", "database_qa"
    needs_workspace = True

    def capabilities(self, plan: PlanContext) -> list[str]:
        models = plan.inventory.get("data_models") or []
        return [f"{len(models)} data model(s)"] if models and plan.workspace is not None else []

    def plan(self, plan: PlanContext) -> PlanResult:
        return PlanResult(
            [_case(self, "DB-STATIC", "Data models have versioned schema migrations", Category.DATABASE, Engine.DATABASE_STATIC)],
            ["Live database checks (constraints, indexes, consistency, transactions) are not implemented: no authorised "
             "test database connection is configured."],
        )  # fmt: skip

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        return database_static.run_case(ctx, spec)


class EnvironmentAdapter(Adapter):
    """Starts the isolated local copy of the project that the dynamic checks of this run will use."""

    name, title, agent = "environment", "Managed test environment", "orchestrator"
    needs_workspace = True

    def capabilities(self, plan: PlanContext) -> list[str]:
        return ["isolated local environment"] if plan.managed_environment else []

    def plan(self, plan: PlanContext) -> PlanResult:
        return PlanResult(
            [_case(self, "ENV", "Project starts from a clean copy: services, migrations/seed and application processes",
                   Category.BUILD, Engine.ENVIRONMENT, priority=1)],
            ["Dynamic checks run against an isolated copy started by the platform (generated secrets, its own database, "
             "no access to the project's real .env files). Results describe the code, not any deployed environment."],
        )  # fmt: skip

    def environment(self, settings: ExecutionSettings) -> ReadinessReport:
        report = ReadinessReport(requirements=["Docker", "about 2.5 GB of container memory"])
        if not sandbox.docker_available():
            report.block(Readiness.BLOCKED, "Docker is not running")
        return report

    def readiness(self, ctx: EngineContext, spec: dict[str, Any]) -> ReadinessReport:
        report = super().readiness(ctx, spec)
        if not ctx.project_config.get("environment"):
            report.block(Readiness.REQUIRES_CONFIGURATION, "no environment profile is stored for this project")
        return report

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        return environment.start_case(ctx)

    def cleanup(self, ctx: EngineContext) -> dict[str, Any]:
        return environment.down(ctx)
