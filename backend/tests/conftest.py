"""Test configuration: disposable PostgreSQL database and a temporary data directory.

The environment is prepared before any application module is imported so that the engine,
settings and data paths all point at test resources.
"""

import json
import os
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]
_env = dotenv_values(REPO_ROOT / ".env")
_test_url = os.environ.get("QAI_TEST_DATABASE_URL") or _env.get("QAI_TEST_DATABASE_URL")
if not _test_url:
    raise RuntimeError("QAI_TEST_DATABASE_URL is not configured (run scripts/setup-env.ps1)")
if not _test_url.rstrip("/").endswith("_test"):
    raise RuntimeError("refusing to run tests: the test database name must end with '_test'")
os.environ["QAI_DATABASE_URL"] = _test_url
os.environ["QAI_ALEMBIC_URL"] = _test_url
os.environ["QAI_DATA_DIR"] = tempfile.mkdtemp(prefix="qai-test-data-")
os.environ["QAI_API_TOKEN"] = "test-token-0123456789abcdef0123456789"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

DEMO_PROJECT = REPO_ROOT / "examples" / "demo-shop"
AUTH = {"Authorization": f"Bearer {os.environ['QAI_API_TOKEN']}"}


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    config = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "backend" / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with SessionLocal() as db:
        db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        db.commit()


@pytest.fixture
def db() -> Iterator[object]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, headers=AUTH) as test_client:
        yield test_client


class _TargetHandler(BaseHTTPRequestHandler):
    """A tiny local 'application under test' with one correct and several defective routes."""

    def log_message(self, *_: object) -> None:  # silence request logging
        pass

    def _send(self, status: int, body: object, content_type: str = "application/json",
              headers: dict[str, str] | None = None) -> None:  # fmt: skip
        payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/items":
            self._send(200, [{"id": 1, "name": "ok"}])
        elif self.path == "/bad-items":
            self._send(200, [{"id": "not-a-number", "name": "bad"}])
        elif self.path == "/items/1":
            self._send(200, {"id": 1, "name": "ok"})
        elif self.path.startswith("/items/"):
            self._send(500, {"detail": "boom"})
        elif self.path == "/redirect-out":
            self._send(302, "", headers={"Location": "http://127.0.0.1:1/elsewhere"})
        elif self.path == "/":
            self._send(200, '<!doctype html><html lang="en"><head><title>Target</title></head><body><main>'
                            '<h1>Target</h1><a href="/missing">broken</a></main></body></html>', "text/html")  # fmt: skip
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._send(422 if self.path == "/items" else 404, {"detail": "invalid"})


@pytest.fixture(scope="session")
def target_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
