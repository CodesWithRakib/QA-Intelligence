"""Manifest parsers: turn dependency manifests into component facts."""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from app.discovery.types import ComponentInfo

JS_FRAMEWORKS = {
    "next": "Next.js", "react": "React", "vue": "Vue", "nuxt": "Nuxt", "@angular/core": "Angular",
    "svelte": "Svelte", "@sveltejs/kit": "SvelteKit", "express": "Express", "@nestjs/core": "NestJS",
    "fastify": "Fastify", "koa": "Koa", "hono": "Hono", "vite": "Vite", "tailwindcss": "Tailwind CSS",
    "prisma": "Prisma", "@prisma/client": "Prisma", "drizzle-orm": "Drizzle", "typeorm": "TypeORM",
    "mongoose": "Mongoose", "sequelize": "Sequelize", "electron": "Electron",
    "react-native": "React Native", "expo": "Expo",
}  # fmt: skip
JS_TEST_FRAMEWORKS = {
    "jest": "Jest", "vitest": "Vitest", "mocha": "Mocha", "@playwright/test": "Playwright",
    "cypress": "Cypress", "@testing-library/react": "Testing Library",
}  # fmt: skip
PY_FRAMEWORKS = {
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask", "starlette": "Starlette",
    "sqlalchemy": "SQLAlchemy", "alembic": "Alembic", "celery": "Celery", "pydantic": "Pydantic",
    "streamlit": "Streamlit", "aiohttp": "aiohttp", "tornado": "Tornado", "djangorestframework": "DRF",
}  # fmt: skip
PY_TEST_FRAMEWORKS = {"pytest": "pytest", "hypothesis": "Hypothesis", "nose2": "nose2", "tox": "tox"}
FRONTEND = {"Next.js", "React", "Vue", "Nuxt", "Angular", "Svelte", "SvelteKit", "React Native", "Expo"}
BACKEND = {
    "Express", "NestJS", "Fastify", "Koa", "Hono", "FastAPI", "Django", "Flask", "Starlette", "aiohttp",
    "Tornado", "DRF", "Spring Boot", "Laravel", "Rails", "ASP.NET Core", "Gin", "Echo", "Fiber",
    "Actix", "Axum", "Rocket",
}  # fmt: skip
JS_LOCKFILES = {
    "pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "package-lock.json": "npm", "bun.lockb": "bun",
    "bun.lock": "bun",
}  # fmt: skip
PY_LOCKFILES = {"poetry.lock": "poetry", "uv.lock": "uv", "Pipfile.lock": "pipenv", "pdm.lock": "pdm"}
# (manifest file, ecosystem, language, package manager, {text marker: framework})
TEXT_MANIFESTS: list[tuple[str, str, str, str, dict[str, str]]] = [
    ("go.mod", "go", "Go", "go modules",
     {"gin-gonic/gin": "Gin", "labstack/echo": "Echo", "gofiber/fiber": "Fiber"}),
    ("Cargo.toml", "cargo", "Rust", "cargo", {"actix-web": "Actix", "axum": "Axum", "rocket": "Rocket"}),
    ("pom.xml", "maven", "Java", "maven", {"spring-boot": "Spring Boot"}),
    ("build.gradle", "gradle", "Java/Kotlin", "gradle", {"spring-boot": "Spring Boot"}),
    ("build.gradle.kts", "gradle", "Java/Kotlin", "gradle", {"spring-boot": "Spring Boot"}),
    ("composer.json", "composer", "PHP", "composer", {"laravel/framework": "Laravel"}),
    ("Gemfile", "bundler", "Ruby", "bundler", {"rails": "Rails"}),
]  # fmt: skip
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([=<>!~][^;#]*)?")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def _read(path: Path) -> str:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _kind(frameworks: list[str]) -> str:
    front, back = bool(FRONTEND & set(frameworks)), bool(BACKEND & set(frameworks))
    if front and back:
        return "fullstack"
    return "frontend" if front else "backend" if back else "library"


def _dep(name: str, spec: str, ecosystem: str, dev: bool = False) -> dict[str, Any]:
    return {"name": name, "spec": spec.strip(), "ecosystem": ecosystem, "dev": dev}


def parse_package_json(path: Path, rel_dir: str) -> ComponentInfo | None:
    try:
        data = json.loads(_read(path) or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    runtime = _as_dict(data.get("dependencies"))
    dev = _as_dict(data.get("devDependencies"))
    names = {**runtime, **dev}
    frameworks = sorted({label for dep, label in JS_FRAMEWORKS.items() if dep in names})
    tests = sorted({label for dep, label in JS_TEST_FRAMEWORKS.items() if dep in names})
    manager = next((pm for lock, pm in JS_LOCKFILES.items() if (path.parent / lock).exists()), None)
    scripts = _as_dict(data.get("scripts"))
    workspaces = data.get("workspaces")
    return ComponentInfo(
        name=str(data.get("name") or Path(rel_dir).name or "root"),
        kind=_kind(frameworks),
        path=rel_dir,
        languages=["TypeScript" if "typescript" in names else "JavaScript"],
        frameworks=frameworks,
        details={
            "manifest": "package.json",
            "package_manager": manager or str(data.get("packageManager", "")).split("@")[0] or "npm (no lockfile)",
            "package_manager_spec": str(data.get("packageManager", ""))[:60],
            "has_lockfile": manager is not None,
            "scripts": {k: str(v)[:300] for k, v in scripts.items()},
            "test_frameworks": tests,
            "workspaces": workspaces if isinstance(workspaces, list | dict) else None,
            "dependencies": [_dep(n, str(v), "npm") for n, v in runtime.items()]
            + [_dep(n, str(v), "npm", dev=True) for n, v in dev.items()],
        },
    )


def _python_requirements(text: str) -> list[dict[str, Any]]:
    deps = []
    for line in text.splitlines():
        if line.strip().startswith(("#", "-")) or not line.strip():
            continue
        match = _REQ_LINE.match(line)
        if match:
            deps.append(_dep(match.group(1), match.group(2) or "", "pypi"))
    return deps


def parse_python(directory: Path, rel_dir: str) -> ComponentInfo | None:
    deps: list[dict[str, Any]] = []
    manifests: list[str] = []
    name = Path(rel_dir).name or "root"
    scripts: dict[str, str] = {}
    for req in sorted(directory.glob("requirements*.txt")):
        manifests.append(req.name)
        deps += _python_requirements(_read(req))
    pyproject = directory / "pyproject.toml"
    if pyproject.exists():
        manifests.append("pyproject.toml")
        try:
            data = tomllib.loads(_read(pyproject))
        except tomllib.TOMLDecodeError:
            data = {}
        project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
        name = str(project.get("name") or name)
        deps += _python_requirements("\n".join(map(str, project.get("dependencies", []))))
        for group in (project.get("optional-dependencies") or {}).values():
            deps += [{**d, "dev": True} for d in _python_requirements("\n".join(map(str, group)))]
        poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data.get("tool"), dict) else {}
        deps += [_dep(n, str(v), "pypi") for n, v in (poetry.get("dependencies") or {}).items() if n != "python"]
        scripts = {k: str(v) for k, v in (project.get("scripts") or {}).items()}
    if (directory / "Pipfile").exists():
        manifests.append("Pipfile")
    if not manifests:
        return None
    lowered = {d["name"].lower().replace("_", "-") for d in deps}
    frameworks = sorted({label for dep, label in PY_FRAMEWORKS.items() if dep in lowered})
    tests = sorted({label for dep, label in PY_TEST_FRAMEWORKS.items() if dep in lowered})
    manager = next((pm for lock, pm in PY_LOCKFILES.items() if (directory / lock).exists()), None)
    return ComponentInfo(
        name=name,
        kind=_kind(frameworks),
        path=rel_dir,
        languages=["Python"],
        frameworks=frameworks,
        details={
            "manifest": ", ".join(manifests),
            "package_manager": manager or "pip",
            "has_lockfile": manager is not None,
            "scripts": scripts,
            "test_frameworks": tests,
            "dependencies": deps,
        },
    )


def parse_text_manifest(path: Path, rel_dir: str) -> ComponentInfo | None:
    for manifest, ecosystem, language, manager, markers in TEXT_MANIFESTS:
        if path.name == manifest:
            text = _read(path)
            frameworks = sorted({label for marker, label in markers.items() if marker in text})
            return ComponentInfo(
                name=Path(rel_dir).name or "root",
                kind=_kind(frameworks),
                path=rel_dir,
                languages=[language],
                frameworks=frameworks,
                details={"manifest": manifest, "package_manager": manager, "ecosystem": ecosystem,
                         "analysis": "manifest detected; dependency-level parsing not implemented"},
            )  # fmt: skip
    if path.suffix == ".csproj":
        text = _read(path)
        frameworks = ["ASP.NET Core"] if "Microsoft.AspNetCore" in text or "Microsoft.NET.Sdk.Web" in text else []
        return ComponentInfo(
            name=path.stem, kind=_kind(frameworks), path=rel_dir, languages=["C#"], frameworks=frameworks,
            details={"manifest": path.name, "package_manager": "nuget",
                     "analysis": "manifest detected; dependency-level parsing not implemented"},
        )  # fmt: skip
    return None
