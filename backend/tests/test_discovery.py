"""Project discovery works from file contents, not names."""

import json
from pathlib import Path

from app.discovery.scanner import scan_workspace
from tests.conftest import DEMO_PROJECT


def test_demo_project_inventory() -> None:
    scan = scan_workspace(DEMO_PROJECT)
    inventory = scan.inventory
    assert inventory["languages"][0]["name"] == "Python"
    assert "FastAPI" in inventory["frameworks"] and "pytest" in inventory["test_frameworks"]
    assert inventory["package_managers"] == ["pip"]
    assert inventory["test_commands"] == [{"component": "", "runtime": "python", "command": "python -m pytest -q"}]
    assert inventory["test_files"]["count"] == 2
    assert "SHOP_NAME" in inventory["env_var_names"]
    assert inventory["openapi_specs"][0]["file"] == "openapi.json"
    assert inventory["containers"]["dockerfiles"] == ["Dockerfile"]
    endpoints = {(e.method, e.path, e.origin) for e in scan.endpoints}
    assert ("GET", "/api/products/{product_id}", "code") in endpoints
    assert ("POST", "/api/orders", "openapi") in endpoints
    jinja = next(d for d in scan.components[0].details["dependencies"] if d["name"] == "jinja2")
    assert jinja["spec"] == "==3.1.2"


def test_framework_is_not_guessed_from_folder_names(tmp_path: Path) -> None:
    (tmp_path / "nextjs-django-app").mkdir()
    (tmp_path / "nextjs-django-app" / "notes.py").write_text("x = 1")
    scan = scan_workspace(tmp_path)
    assert scan.inventory["frameworks"] == []
    assert any("No recognised dependency manifest" in item for item in scan.unsupported)


def test_nextjs_routes_and_monorepo_detection(tmp_path: Path) -> None:
    web = tmp_path / "apps" / "web"
    (web / "app" / "(shop)" / "products" / "[id]").mkdir(parents=True)
    (web / "app" / "api" / "orders").mkdir(parents=True)
    (web / "package.json").write_text(json.dumps({
        "name": "web", "dependencies": {"next": "15.0.0", "react": "19.0.0"},
        "devDependencies": {"typescript": "5", "vitest": "2"}, "scripts": {"test": "vitest run"},
    }))  # fmt: skip
    (web / "app" / "page.tsx").write_text("export default function Page() { return null }")
    (web / "app" / "(shop)" / "products" / "[id]" / "page.tsx").write_text(
        "export default function P() { return process.env.API_URL }"
    )
    (web / "app" / "api" / "orders" / "route.ts").write_text("export async function POST() {}\nexport const GET = async () => {}")
    (tmp_path / "pnpm-workspace.yaml").write_text("packages: ['apps/*']")
    (tmp_path / "legacy").mkdir()
    (tmp_path / "legacy" / "Main.java").write_text("class Main {}")
    scan = scan_workspace(tmp_path)
    assert {r.path for r in scan.routes} == {"/", "/products/[id]"}
    assert {(e.method, e.path) for e in scan.endpoints} == {("GET", "/api/orders"), ("POST", "/api/orders")}
    assert scan.inventory["monorepo"]["detected"] and "pnpm-workspace.yaml" in scan.inventory["monorepo"]["markers"]
    assert scan.components[0].kind == "frontend" and "Vitest" in scan.inventory["test_frameworks"]
    assert "API_URL" in scan.inventory["env_var_names"]
    assert any(item.startswith("Java:") for item in scan.unsupported)
    assert scan.inventory["test_commands"] == [{"component": "apps/web", "runtime": "node", "command": "npm test"}]
