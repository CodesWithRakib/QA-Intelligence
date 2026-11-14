"""Walks an imported workspace and builds the architecture inventory from actual file contents."""

import os
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.discovery import android, manifests, routes
from app.discovery.types import ComponentInfo, EndpointInfo, RouteInfo, ScanResult
from app.security.archive import EXCLUDED_DIRS

LANGUAGES = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java",
    ".kt": "Kotlin", ".cs": "C#", ".php": "PHP", ".rb": "Ruby", ".swift": "Swift", ".dart": "Dart",
    ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".scala": "Scala", ".ex": "Elixir",
    ".vue": "Vue SFC", ".svelte": "Svelte", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
}  # fmt: skip
# Languages with source-level route/endpoint/model extraction. Everything else is inventory-only.
DEEP_ANALYSIS = {"Python", "TypeScript", "JavaScript"}
MARKUP = {"HTML", "CSS", "SCSS", "SQL", "Shell", "PowerShell", "Vue SFC", "Svelte"}
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".prisma"}
TEST_FILE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.(py|go)|[^/]+\.(test|spec)\.[jt]sx?|[^/]+Tests?\.(java|cs))$")
MIGRATION_DIR = re.compile(r"(^|/)(alembic/versions|migrations|prisma/migrations|db/migrate)(/|$)")
SENSITIVE = re.compile(
    r"(^|/)(\.env[^/]*|[^/]*\.(pem|key|p12|pfx|jks)|id_(rsa|ed25519)|credentials[^/]*\.json|secrets?\.[^/]+"
    r"|[^/]*(auth|password|crypto|payment|billing|token|session|permission)[^/]*\.(py|ts|tsx|js|go|java|cs|php|rb))$",
    re.IGNORECASE,
)
OPENAPI_NAME = re.compile(r"(openapi|swagger|api[-_]?spec)", re.IGNORECASE)
MAX_SOURCE_BYTES = 512 * 1024
MAX_SOURCE_FILES = 6000
MAX_LIST = 300


def _walk(root: Path) -> list[tuple[str, Path, int]]:
    files = []
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in sorted(names):
            path = Path(current) / name
            if path.is_file() and not path.is_symlink():
                files.append((path.relative_to(root).as_posix(), path, path.stat().st_size))
    return files


def _components(files: list[tuple[str, Path, int]]) -> list[ComponentInfo]:
    components: list[ComponentInfo] = []
    python_dirs: set[Path] = set()
    for rel, path, _ in files:
        rel_dir = PurePosixPath(rel).parent.as_posix().removeprefix(".")
        component: ComponentInfo | None = None
        if path.name == "package.json":
            component = manifests.parse_package_json(path, rel_dir)
        elif path.name == "pyproject.toml" or re.fullmatch(r"requirements.*\.txt|Pipfile", path.name):
            if path.parent not in python_dirs:
                python_dirs.add(path.parent)
                component = manifests.parse_python(path.parent, rel_dir)
        else:
            component = manifests.parse_text_manifest(path, rel_dir)
        if component:
            components.append(component)
    return components


def _compose_services(path: Path) -> list[dict[str, Any]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (yaml.YAMLError, OSError):
        return []
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return []
    return [
        {"name": str(name), "image": str(spec.get("image", "")), "build": bool(spec.get("build")),
         "ports": [str(p) for p in spec.get("ports", [])][:10],
         "depends_on": sorted(spec["depends_on"]) if isinstance(spec.get("depends_on"), list | dict) else []}
        for name, spec in services.items() if isinstance(spec, dict)
    ]  # fmt: skip


def _test_commands(components: list[ComponentInfo], test_files: list[str]) -> list[dict[str, str]]:
    """Commands the sandbox may run. Only derived from manifests - never guessed."""
    commands = []
    for component in components:
        details = component.details
        prefix = f"{component.path}/" if component.path else ""
        has_tests = any(f.startswith(prefix) for f in test_files)
        if "Python" in component.languages and has_tests:
            commands.append({"component": component.path, "runtime": "python", "command": "python -m pytest -q"})
        elif details.get("manifest") == "package.json" and "no test specified" not in details.get("scripts", {}).get(
            "test", "no test specified"
        ):
            commands.append({"component": component.path, "runtime": "node", "command": "npm test"})
    return commands


# Files whose presence is characteristic of a framework (second signal next to the dependency manifest).
CONFIG_SIGNALS = {
    "next.config": "Next.js", "nuxt.config": "Nuxt", "angular.json": "Angular", "svelte.config": "Svelte",
    "astro.config": "Astro", "vite.config": "Vite", "nest-cli.json": "NestJS", "manage.py": "Django",
    "artisan": "Laravel", "wp-config.php": "WordPress", "wp-config-sample.php": "WordPress", "tailwind.config": "Tailwind CSS",
}  # fmt: skip


def framework_evidence(components: list[ComponentInfo], rels: list[str]) -> dict[str, set[str]]:
    evidence: dict[str, set[str]] = {}
    for component in components:
        manifest = f"{component.path + '/' if component.path else ''}{component.details.get('manifest', 'manifest')}"
        for framework in component.frameworks:
            evidence.setdefault(framework, set()).add(f"dependency in {manifest}")
    for rel in rels:
        name = PurePosixPath(rel).name
        for prefix, framework in CONFIG_SIGNALS.items():
            if name == prefix or name.startswith(prefix + "."):
                evidence.setdefault(framework, set()).add(f"config file {rel}")
        if rel.startswith("wp-content/") or "/wp-content/themes/" in rel:
            evidence.setdefault("WordPress", set()).add("wp-content directory")
    return evidence


def scan_workspace(root: Path) -> ScanResult:
    files = _walk(root)
    language_files: Counter[str] = Counter()
    language_bytes: Counter[str] = Counter()
    for rel, _, size in files:
        language = LANGUAGES.get(PurePosixPath(rel).suffix.lower())
        if language:
            language_files[language] += 1
            language_bytes[language] += size

    components = _components(files)
    next_roots = [c.path for c in components if "Next.js" in c.frameworks]
    found_routes: list[RouteInfo] = []
    endpoints: list[EndpointInfo] = []
    models: list[dict[str, str]] = []
    env: set[str] = set()
    openapi_specs: list[dict[str, Any]] = []
    compose_services: list[dict[str, Any]] = []
    scanned = 0

    for rel, path, size in files:
        suffix, name = path.suffix.lower(), path.name
        if suffix in {".json", ".yaml", ".yml"} and OPENAPI_NAME.search(name) and size <= 4 * MAX_SOURCE_BYTES:
            spec = routes.load_openapi(path)
            if spec:
                info = spec["info"] if isinstance(spec.get("info"), dict) else {}
                openapi_specs.append({
                    "file": rel, "title": str(info.get("title", "")), "version": str(info.get("version", "")),
                    "spec_version": str(spec.get("openapi") or spec.get("swagger")),
                    "servers": [str(s.get("url")) for s in spec.get("servers", []) if isinstance(s, dict)][:5],
                })  # fmt: skip
                endpoints += routes.openapi_endpoints(spec, rel)
        if re.fullmatch(r"(docker-)?compose(\.[\w-]+)?\.ya?ml", name):
            compose_services += _compose_services(path)
        is_env_template = name.startswith(".env")
        if (suffix not in SOURCE_SUFFIXES and not is_env_template) or size > MAX_SOURCE_BYTES:
            continue
        if scanned >= MAX_SOURCE_FILES:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        env |= routes.env_names(text, rel)
        if is_env_template:
            continue
        models += routes.data_models(text, rel)
        if suffix == ".py":
            endpoints += routes.python_endpoints(text, rel)
        elif suffix != ".prisma":
            endpoints += routes.js_endpoints(text, rel)
            page_routes, api_routes = routes.next_routes(rel, text, next_roots)
            found_routes += page_routes
            endpoints += api_routes

    rels = [rel for rel, _, _ in files]
    evidence = framework_evidence(components, rels)
    test_files = [rel for rel in rels if TEST_FILE.search(rel)]
    code_languages = [lang for lang in language_files if lang not in MARKUP]
    unsupported = [
        f"{lang}: language detected, but route/endpoint/model extraction is not implemented"
        for lang in sorted(code_languages) if lang not in DEEP_ANALYSIS
    ]  # fmt: skip
    if not components and code_languages:
        unsupported.append("No recognised dependency manifest: frameworks and dependencies could not be determined")
    if scanned >= MAX_SOURCE_FILES:
        unsupported.append(f"Source scan stopped after {MAX_SOURCE_FILES} files; later files were not analysed")

    monorepo_markers = [m for m in ("pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json") if m in rels]
    has_workspaces = any(c.details.get("workspaces") for c in components)
    inventory: dict[str, Any] = {
        "file_count": len(files),
        "languages": [
            {"name": lang, "files": count, "bytes": language_bytes[lang]} for lang, count in language_files.most_common()
        ],
        "frameworks": sorted(evidence),
        # Why each framework was reported: several independent signals are stronger than one dependency name.
        "framework_evidence": {name: sorted(signals) for name, signals in sorted(evidence.items())},
        "package_managers": sorted({c.details.get("package_manager", "") for c in components} - {""}),
        "test_frameworks": sorted({t for c in components for t in c.details.get("test_frameworks", [])}),
        "test_files": {"count": len(test_files), "sample": test_files[:25]},
        "test_commands": _test_commands(components, test_files),
        "monorepo": {
            "detected": bool(monorepo_markers or has_workspaces or len(components) > 1),
            "markers": monorepo_markers,
            "package_count": len(components),
        },  # fmt: skip
        "data_models": models[:MAX_LIST],
        "migrations": sorted({m.group(0).strip("/") for rel in rels if (m := MIGRATION_DIR.search(rel))}),
        "env_var_names": sorted(env)[:MAX_LIST],
        "android": android.detect(files),
        "root_node": next(
            (
                {"package_manager_spec": c.details.get("package_manager_spec", ""), "has_lockfile": c.details.get("has_lockfile")}
                for c in components
                if c.path == "" and c.details.get("manifest") == "package.json"
            ),
            None,
        ),  # fmt: skip
        "openapi_specs": openapi_specs,
        "containers": {
            "dockerfiles": [r for r in rels if PurePosixPath(r).name.startswith("Dockerfile")][:20],
            "compose_services": compose_services[:50],
        },  # fmt: skip
        "ci": [r for r in rels if r.startswith((".github/workflows/", ".gitlab-ci", ".circleci/"))][:20],
        "security_sensitive_files": [r for r in rels if SENSITIVE.search(r)][:MAX_LIST],
        "analysis_notes": [
            "Routes and endpoints found in code come from pattern matching; prefixes applied in other files "
            "(e.g. include_router, app.use) are not resolved.",
            "Test coverage percentage is not measured; only the presence of test files is reported.",
        ],
    }
    unique_endpoints = list({(e.method, e.path, e.origin, e.source_file): e for e in endpoints}.values())
    return ScanResult(inventory, unsupported, components, found_routes[:2000], unique_endpoints[:2000])
