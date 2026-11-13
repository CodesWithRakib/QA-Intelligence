"""AI routing, paid-usage gates, usage accounting and benchmark graders (no real model needed)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai import benchmark, service
from app.ai.providers import PROVIDERS, ChatResult, ProviderError
from app.models import AIConfiguration, AIUsage

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}


class FakeProvider:
    def __init__(self, name: str, reply: str, models: list[str] | None = None) -> None:
        self.name, self.reply, self.models, self.calls = name, reply, models or ["fake-model"], 0

    def available(self) -> tuple[bool, str]:
        return True, "fake"

    def list_models(self) -> list[str]:
        return self.models

    def chat(self, *, model: str, **_: Any) -> ChatResult:
        self.calls += 1
        return ChatResult(self.reply, model, prompt_tokens=1000, completion_tokens=500, latency_ms=12)


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch, db) -> dict[str, FakeProvider]:
    fakes = {
        "ollama": FakeProvider("ollama", '{"answer": "local"}'),
        "anthropic": FakeProvider("anthropic", '{"answer": "paid"}'),
    }
    for name, fake in fakes.items():
        monkeypatch.setitem(PROVIDERS, name, fake)
    service.ensure_defaults(db)
    return fakes


def _approve(db, **values: Any) -> AIConfiguration:
    config = db.scalar(select(AIConfiguration).where(AIConfiguration.provider == "anthropic"))
    for key, value in values.items():
        setattr(config, key, value)
    db.commit()
    return config


def test_paid_providers_start_disabled_and_premium_tasks_stay_local(db, providers) -> None:
    configs = {c.provider: c for c in db.scalars(select(AIConfiguration))}
    assert configs["ollama"].enabled and not configs["anthropic"].enabled and not configs["openai"].enabled
    route = service.route(db, "repair")
    assert route.provider == "ollama" and "paid usage has not been approved" in route.reason
    result = service.run_json_task(db, task_type="repair", system="s", prompt="p", schema=SCHEMA)
    assert result.data == {"answer": "local"} and result.cost_usd == 0 and providers["anthropic"].calls == 0


def test_api_refuses_incomplete_paid_approval_and_unimplemented_provider(client: TestClient, providers) -> None:
    incomplete = client.patch("/api/ai/config/anthropic", json={"enabled": True, "approve_paid_usage": True})
    assert incomplete.status_code == 422 and "monthly budget above 0" in incomplete.json()["detail"]
    assert client.patch("/api/ai/config/openai", json={"enabled": True}).status_code == 422
    assert client.patch("/api/ai/config/ollama", json={"monthly_budget_usd": 5}).status_code == 422
    view = client.get("/api/ai/config").json()
    assert all(not p["paid_usage_approved"] for p in view["providers"])


def test_approved_paid_provider_is_used_only_for_premium_tasks_within_budget(client: TestClient, db, providers) -> None:
    approved = client.patch("/api/ai/config/anthropic", json={
        "enabled": True, "default_model": "claude-opus-5-5", "monthly_budget_usd": 0.05, "per_project_budget_usd": 0.05,
        "input_price_per_mtok": 4, "output_price_per_mtok": 20, "approve_paid_usage": True})  # fmt: skip
    assert approved.status_code == 200 and approved.json()["paid_usage_approved"]
    assert service.route(db, "classify").provider == "ollama"  # routine task: never paid
    result = service.run_json_task(db, task_type="repair", system="s", prompt="p", schema=SCHEMA, max_output_tokens=500)
    assert result.provider == "anthropic" and result.cost_usd == pytest.approx((1000 * 4 + 500 * 20) / 1_000_000)
    assert service.spent(db, "anthropic") == pytest.approx(0.014)
    # The pessimistic estimate for a large request no longer fits the remaining budget: hard stop, fall back to local.
    route = service.route(db, "repair", prompt="x" * 3000, max_output_tokens=2048)
    assert route.provider == "ollama" and "monthly budget would be exceeded" in route.reason
    with pytest.raises(service.AIUnavailable, match="budget"):
        service.run_json_task(db, task_type="repair", system="s", prompt="x" * 3000, schema=SCHEMA,
                              forced=service.Route("anthropic", "claude-opus-5-5", "forced"))  # fmt: skip
    assert providers["anthropic"].calls == 1


def test_paid_call_blocked_when_prices_unknown(db, providers) -> None:
    config = _approve(db, enabled=True, default_model="m", monthly_budget_usd=5, paid_usage_approved_at=None)
    assert "not been approved" in (service.paid_block_reason(db, config, 0, None) or "")
    from datetime import UTC, datetime

    config = _approve(db, paid_usage_approved_at=datetime.now(UTC))
    assert "cost cannot be estimated" in (service.paid_block_reason(db, config, 0, None) or "")


def test_invalid_model_output_is_an_error_and_still_accounted(db, providers) -> None:
    providers["ollama"].reply = '{"wrong": 1}'
    with pytest.raises(ProviderError, match="does not match the requested schema"):
        service.run_json_task(db, task_type="classify", system="s", prompt="p", schema=SCHEMA)
    providers["ollama"].reply = "not json"
    with pytest.raises(ProviderError):
        service.run_json_task(db, task_type="classify", system="s", prompt="p", schema=SCHEMA)
    usage = db.scalars(select(AIUsage)).all()
    assert len(usage) == 2 and all(not u.success and u.error for u in usage)


def test_no_provider_available_is_reported(db, providers, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers["ollama"], "models", [])
    with pytest.raises(service.AIUnavailable, match="no local model installed"):
        service.route(db, "classify")


def test_benchmark_graders_and_summary(db, providers) -> None:
    good = {"cases": [{"value": -5, "low": 0, "high": 10, "expected": 0}, {"value": 15, "low": 0, "high": 10, "expected": 10},
                      {"value": 5, "low": 0, "high": 10, "expected": 5}, {"value": 0, "low": 0, "high": 10, "expected": 0}]}  # fmt: skip
    assert benchmark._grade_clamp(good)[0]
    assert not benchmark._grade_clamp({"cases": good["cases"][:3]})[0]  # too few cases
    wrong = {"cases": [dict(good["cases"][0], expected=-5), *good["cases"][1:]]}
    assert not benchmark._grade_clamp(wrong)[0]

    providers["ollama"].reply = '{"verdict": "vulnerable", "reason": "always says vulnerable"}'
    benchmark.run_benchmark(db, "ollama", "fake-model")
    summary = benchmark.summarise(db)[0]
    assert summary["tasks"] == len(benchmark.TASKS)
    assert summary["false_positive_rate"] == 1.0  # flagged both safe snippets
    by_task = {d["task"]: d for d in summary["details"]}
    assert by_task["classify-sqli"]["passed"] and not by_task["classify-parameterised"]["passed"]
    assert not by_task["root-cause-discount"]["structured_output_ok"]  # reply did not match that task's schema
    assert summary["cost_usd"] == 0 and "tool-call reliability" in summary["not_measured"]
