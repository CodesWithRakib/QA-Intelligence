"""Chat providers behind one small interface.

* ``ollama``    - local, free, the default.
* ``anthropic`` - optional and paid; uses the official SDK (install with ``pip install .[anthropic]``).
                  Never called unless the owner approved paid usage and set a budget (see budget.py).
* ``openai``    - declared for configuration, adapter not implemented in the MVP.
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import get_settings

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"


class ProviderError(Exception):
    """The provider could not produce a usable answer."""


@dataclass
class ChatResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class Provider(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...

    def list_models(self) -> list[str]: ...

    def chat(self, *, model: str, system: str, prompt: str, schema: dict[str, Any] | None,
             timeout_s: float, max_output_tokens: int) -> ChatResult: ...  # fmt: skip


class OllamaProvider:
    name = "ollama"

    def __init__(self) -> None:
        self._base = get_settings().ollama_base_url.rstrip("/")

    def _client(self, timeout_s: float) -> httpx.Client:
        return httpx.Client(base_url=self._base, timeout=timeout_s, trust_env=False)

    def available(self) -> tuple[bool, str]:
        try:
            with self._client(5) as client:
                version = client.get("/api/version").json().get("version", "?")
            return True, f"Ollama {version}"
        except (httpx.HTTPError, ValueError) as exc:
            return False, f"Ollama not reachable at {self._base}: {type(exc).__name__}"

    def list_models(self) -> list[str]:
        try:
            with self._client(5) as client:
                return sorted(m["name"] for m in client.get("/api/tags").json().get("models", []))
        except (httpx.HTTPError, ValueError, KeyError):
            return []

    def chat(self, *, model: str, system: str, prompt: str, schema: dict[str, Any] | None,
             timeout_s: float, max_output_tokens: int) -> ChatResult:  # fmt: skip
        body: dict[str, Any] = {
            "model": model, "stream": False, "think": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "options": {"temperature": 0, "num_predict": max_output_tokens},
        }  # fmt: skip
        if schema:
            body["format"] = schema
        started = time.monotonic()
        try:
            with self._client(timeout_s) as client:
                response = client.post("/api/chat", json=body)
                if response.status_code == 400 and "think" in response.text.lower():
                    body.pop("think")  # model without a thinking mode
                    response = client.post("/api/chat", json=body)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError(f"local model timed out after {timeout_s:.0f}s") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc
        return ChatResult(
            text=str(data.get("message", {}).get("content", "")), model=model,
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            latency_ms=int((time.monotonic() - started) * 1000),
        )  # fmt: skip


class AnthropicProvider:
    """Optional paid provider. Implemented against the official SDK; not exercised in the MVP
    verification because no API key or spending approval exists."""

    name = "anthropic"

    def available(self) -> tuple[bool, str]:
        if not get_settings().provider_key("anthropic"):
            return False, "ANTHROPIC_API_KEY is not set in .env"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "anthropic SDK not installed (pip install '.[anthropic]')"
        return True, "API key configured"

    def list_models(self) -> list[str]:
        return [ANTHROPIC_DEFAULT_MODEL, "claude-sonnet-5-5", "claude-haiku-5-5"]

    def chat(self, *, model: str, system: str, prompt: str, schema: dict[str, Any] | None,
             timeout_s: float, max_output_tokens: int) -> ChatResult:  # fmt: skip
        import anthropic

        client = anthropic.Anthropic(api_key=get_settings().provider_key("anthropic"), timeout=timeout_s, max_retries=1)
        output_config: dict[str, Any] = {"effort": "medium"}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        started = time.monotonic()
        try:
            response = client.messages.create(
                model=model, max_tokens=max_output_tokens, system=system, output_config=output_config,
                messages=[{"role": "user", "content": prompt}],
            )  # fmt: skip
        except anthropic.RateLimitError as exc:
            raise ProviderError("Anthropic rate limit reached") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("could not reach the Anthropic API") from exc
        if response.stop_reason in {"refusal", "max_tokens"}:
            raise ProviderError(f"Anthropic response incomplete (stop_reason={response.stop_reason})")
        text = "".join(block.text for block in response.content if block.type == "text")
        return ChatResult(text=text, model=response.model, prompt_tokens=response.usage.input_tokens,
                          completion_tokens=response.usage.output_tokens,
                          latency_ms=int((time.monotonic() - started) * 1000))  # fmt: skip


PROVIDERS: dict[str, Provider] = {"ollama": OllamaProvider(), "anthropic": AnthropicProvider()}
PAID = {"anthropic", "openai"}
DECLARED_ONLY = {"openai": "adapter not implemented in the MVP"}


def parse_json(text: str) -> Any:
    """Parse a model reply that should be JSON (tolerates a Markdown code fence)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(cleaned)
