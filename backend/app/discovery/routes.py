"""Source-level extraction of routes, API endpoints, data models and environment variable names.

Regex based and intentionally conservative: results are reported as "discovered from code" and the
known gaps (router prefixes applied in other files, dynamic registration) are listed in the inventory.
"""

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.discovery.types import EndpointInfo, RouteInfo

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_PY_DECORATOR = re.compile(r"@\s*(\w+)\.(get|post|put|patch|delete|head|options)\(\s*[rf]?[\"']([^\"']*)[\"']", re.MULTILINE)
_PY_ROUTER_PREFIX = re.compile(r"(\w+)\s*=\s*APIRouter\([^)]*?prefix\s*=\s*[\"']([^\"']+)[\"']", re.DOTALL)
_FLASK_ROUTE = re.compile(r"@\s*\w+\.route\(\s*[\"']([^\"']+)[\"'](?:[^)]*?methods\s*=\s*[\[(]([^\])]*)[\])])?", re.DOTALL)
_EXPRESS = re.compile(r"\b(?:app|router|server|api)\.(get|post|put|patch|delete)\(\s*[\"'`]([^\"'`]+)[\"'`]")
_NEXT_ROUTE_EXPORT = re.compile(r"export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")
_TABLENAME = re.compile(r"__tablename__\s*=\s*[\"'](\w+)[\"']")
_DJANGO_MODEL = re.compile(r"class\s+(\w+)\(\s*(?:models\.)?Model\s*\)")
_PRISMA_MODEL = re.compile(r"^model\s+(\w+)\s*\{", re.MULTILINE)
_TYPEORM_ENTITY = re.compile(r"@Entity\([^)]*\)\s*export\s+class\s+(\w+)")
_ENV_JS = re.compile(r"process\.env\.([A-Z_][A-Z0-9_]*)|import\.meta\.env\.([A-Z_][A-Z0-9_]*)")
_ENV_PY = re.compile(r"os\.(?:environ(?:\.get)?\s*[\[(]|getenv\s*\()\s*[\"']([A-Z_][A-Z0-9_]*)[\"']")
_ENV_FILE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)
_NEXT_PAGE = re.compile(r"^page\.(tsx|jsx|ts|js)$")
_NEXT_ROUTE = re.compile(r"^route\.(ts|js)$")


def python_endpoints(text: str, rel: str) -> list[EndpointInfo]:
    prefixes = dict(_PY_ROUTER_PREFIX.findall(text))
    found = [
        EndpointInfo(method.upper(), prefixes.get(owner, "") + path or "/", "code", rel)
        for owner, method, path in _PY_DECORATOR.findall(text)
    ]
    for path, methods in _FLASK_ROUTE.findall(text):
        names = re.findall(r"[A-Za-z]+", methods) or ["GET"]
        found += [EndpointInfo(name.upper(), path, "code", rel) for name in names]
    return found


def js_endpoints(text: str, rel: str) -> list[EndpointInfo]:
    return [EndpointInfo(method.upper(), path, "code", rel) for method, path in _EXPRESS.findall(text)]


def _next_segments(parts: tuple[str, ...]) -> str:
    kept = [p for p in parts if not (p.startswith("(") and p.endswith(")")) and not p.startswith("@")]
    return "/" + "/".join(kept)


def next_routes(rel: str, text: str, next_roots: list[str]) -> tuple[list[RouteInfo], list[EndpointInfo]]:
    """Next.js file-system routing, applied only inside components that depend on `next`."""
    path = PurePosixPath(rel)
    for root in next_roots:
        try:
            inner = path.relative_to(root) if root else path
        except ValueError:
            continue
        parts = inner.parts[1:] if inner.parts[:1] == ("src",) else inner.parts
        if parts[:1] == ("app",):
            if _NEXT_PAGE.match(path.name):
                return [RouteInfo(_next_segments(parts[1:-1]), "Next.js (app router)", rel)], []
            if _NEXT_ROUTE.match(path.name):
                methods = sorted(set(_NEXT_ROUTE_EXPORT.findall(text))) or ["ANY"]
                url = _next_segments(parts[1:-1])
                return [], [EndpointInfo(m, url, "code", rel) for m in methods]
        if parts[:1] == ("pages",) and path.suffix in {".tsx", ".jsx", ".ts", ".js"}:
            stem = [*parts[1:-1], path.stem]
            if stem[-1] == "index":
                stem = stem[:-1]
            if any(segment.startswith("_") for segment in stem):
                return [], []
            url = "/" + "/".join(stem)
            if parts[1:2] == ("api",):
                return [], [EndpointInfo("ANY", url, "code", rel)]
            return [RouteInfo(url, "Next.js (pages router)", rel)], []
    return [], []


def data_models(text: str, rel: str) -> list[dict[str, str]]:
    models = [{"name": t, "kind": "SQLAlchemy table", "file": rel} for t in _TABLENAME.findall(text)]
    if rel.endswith(".py") and "models" in text:
        models += [{"name": n, "kind": "Django model", "file": rel} for n in _DJANGO_MODEL.findall(text)]
    if rel.endswith(".prisma"):
        models += [{"name": n, "kind": "Prisma model", "file": rel} for n in _PRISMA_MODEL.findall(text)]
    if rel.endswith(".ts"):
        models += [{"name": n, "kind": "TypeORM entity", "file": rel} for n in _TYPEORM_ENTITY.findall(text)]
    return models


def env_names(text: str, rel: str) -> set[str]:
    name = PurePosixPath(rel).name
    if name.startswith(".env"):
        return set(_ENV_FILE.findall(text))
    if rel.endswith(".py"):
        return set(_ENV_PY.findall(text))
    return {a or b for a, b in _ENV_JS.findall(text)}


def load_openapi(path: Path) -> dict[str, Any] | None:
    """Parse a JSON/YAML document and return it only when it is an OpenAPI/Swagger spec."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError, OSError, RecursionError):
        return None
    if isinstance(data, dict) and ("openapi" in data or "swagger" in data) and isinstance(data.get("paths"), dict):
        return data
    return None


def openapi_endpoints(spec: dict[str, Any], rel: str) -> list[EndpointInfo]:
    endpoints = []
    for url, item in spec.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            endpoints.append(
                EndpointInfo(
                    method.upper(),
                    str(url),
                    "openapi",
                    rel,
                    operation_id=operation.get("operationId"),
                    spec={
                        "summary": str(operation.get("summary", ""))[:300],
                        "has_request_body": "requestBody" in operation,
                        "responses": sorted(map(str, (operation.get("responses") or {}).keys())),
                        "secured": bool(operation.get("security", spec.get("security"))),
                    },
                )  # fmt: skip
            )
    return endpoints
