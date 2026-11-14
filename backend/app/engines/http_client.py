"""Policy-enforcing HTTP client for active tests against authorised targets."""

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

from app.security.ssrf import validate_target

MAX_REDIRECTS = 5
MAX_BODY_BYTES = 2 * 1024 * 1024
USER_AGENT = "QA-Intelligence/0.1 (authorised local QA)"


class RequestBudgetExceeded(Exception):
    """The per-run request cap was reached (protects targets from unbounded traffic)."""


@dataclass
class Exchange:
    method: str
    url: str
    status: int
    elapsed_ms: float
    headers: dict[str, str]
    body: bytes
    request_body: Any = None
    set_cookies: list[str] = field(default_factory=list)

    def json(self) -> Any:
        return json.loads(self.body)

    def transcript(self) -> str:
        shown = self.body[:4000].decode("utf-8", "replace")
        request = f"\n\n{self.request_body!r}" if self.request_body is not None else ""
        headers = "\n".join(f"{k}: {v}" for k, v in self.headers.items())
        return f"> {self.method} {self.url}{request}\n\n< HTTP {self.status} ({self.elapsed_ms:.0f} ms)\n{headers}\n\n{shown}"


class PolicyClient:
    """Every hop (including redirects) is validated against the allowlist before it is sent."""

    def __init__(self, allowed: list[str], timeout_s: float, max_requests: int) -> None:
        self._allowed = allowed
        self._remaining = max_requests
        self._client = httpx.Client(
            timeout=timeout_s, follow_redirects=False, trust_env=False, headers={"User-Agent": USER_AGENT}
        )

    def __enter__(self) -> "PolicyClient":
        return self

    def __exit__(self, *_: object) -> None:
        self._client.close()

    def request(self, method: str, url: str, *, json_body: Any = None, follow: bool = True,
                headers: dict[str, str] | None = None) -> Exchange:  # fmt: skip
        started = time.perf_counter()
        for _ in range(MAX_REDIRECTS + 1):
            if self._remaining <= 0:
                raise RequestBudgetExceeded("per-run request limit reached")
            self._remaining -= 1
            validate_target(url, self._allowed)
            with self._client.stream(method, url, json=json_body, headers=headers) as response:
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BODY_BYTES:
                        break
                location = response.headers.get("location")
                if follow and response.is_redirect and location:
                    url, method, json_body = urljoin(url, location), "GET", None
                    continue
                return Exchange(
                    method, url, response.status_code, (time.perf_counter() - started) * 1000,
                    dict(response.headers), body[:MAX_BODY_BYTES], json_body,
                    response.headers.get_list("set-cookie"),
                )  # fmt: skip
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects")
