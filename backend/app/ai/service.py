"""Task routing, budget enforcement and usage accounting for every AI call."""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.providers import DECLARED_ONLY, PAID, PROVIDERS, ChatResult, ProviderError, parse_json
from app.models import AIConfiguration, AIUsage

log = logging.getLogger(__name__)
# Local models handle routine tasks; "premium" tasks MAY use a paid provider, but only when the
# owner has approved one and budget remains - otherwise they also run locally.
TASK_TIERS = {
    "summarize": "local", "classify": "local", "explain": "local", "test_suggestions": "local",
    "root_cause": "premium", "repair": "premium", "review": "premium",
}  # fmt: skip
BUDGET_ALERT_RATIO = 0.8


class AIUnavailable(Exception):
    """No provider can serve the task (not running, not configured, or over budget)."""


@dataclass
class Route:
    provider: str
    model: str
    reason: str


@dataclass
class TaskResult:
    data: Any
    provider: str
    model: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def ensure_defaults(db: Session) -> None:
    """Create one configuration row per known provider. Paid providers start disabled."""
    existing = set(db.scalars(select(AIConfiguration.provider)))
    for name in ("ollama", "anthropic", "openai"):
        if name not in existing:
            db.add(AIConfiguration(provider=name, enabled=name == "ollama", is_paid=name in PAID))
    db.commit()


def _month_start() -> datetime:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def spent(db: Session, provider: str, project_id: uuid.UUID | None = None) -> float:
    query = select(func.coalesce(func.sum(AIUsage.cost_usd), 0)).where(
        AIUsage.provider == provider, AIUsage.created_at >= _month_start()
    )
    if project_id:
        query = query.where(AIUsage.project_id == project_id)
    return float(db.scalar(query) or 0)


def _cost(config: AIConfiguration, prompt_tokens: int, completion_tokens: int) -> float:
    if not config.is_paid:
        return 0.0
    return (prompt_tokens * float(config.input_price_per_mtok or 0)
            + completion_tokens * float(config.output_price_per_mtok or 0)) / 1_000_000  # fmt: skip


def paid_block_reason(db: Session, config: AIConfiguration, estimate: float, project_id: uuid.UUID | None) -> str | None:
    """Why a paid provider may NOT be used right now (None = allowed). Every gate is a hard stop."""
    if config.provider in DECLARED_ONLY:
        return DECLARED_ONLY[config.provider]
    if not config.enabled or config.paid_usage_approved_at is None:
        return "paid usage has not been approved by the owner"
    if config.input_price_per_mtok is None or config.output_price_per_mtok is None:
        return "prices are not configured, so cost cannot be estimated"
    ok, why = PROVIDERS[config.provider].available()
    if not ok:
        return why
    if spent(db, config.provider) + estimate > float(config.monthly_budget_usd):
        return "monthly budget would be exceeded"
    if project_id and spent(db, config.provider, project_id) + estimate > float(config.per_project_budget_usd):
        return "per-project budget would be exceeded"
    return None


def _estimate(config: AIConfiguration, system: str, prompt: str, max_output_tokens: int) -> float:
    # Deliberately pessimistic: ~3 characters per token and the full output allowance.
    return _cost(config, (len(system) + len(prompt)) // 3 + 1, max_output_tokens)


def route(db: Session, task_type: str, *, system: str = "", prompt: str = "", max_output_tokens: int = 2048,
          project_id: uuid.UUID | None = None) -> Route:  # fmt: skip
    configs = {c.provider: c for c in db.scalars(select(AIConfiguration))}
    notes = []
    if TASK_TIERS.get(task_type, "local") == "premium":
        for name in sorted(PAID & configs.keys()):
            config = configs[name]
            reason = paid_block_reason(db, config, _estimate(config, system, prompt, max_output_tokens), project_id)
            if reason is None and config.default_model:
                return Route(name, config.default_model, "premium task, approved paid provider within budget")
            notes.append(f"{name}: {reason or 'no default model'}")
    local = configs.get("ollama")
    if local and local.enabled:
        provider = PROVIDERS["ollama"]
        ok, why = provider.available()
        models = provider.list_models() if ok else []
        model = local.default_model if local.default_model in models else (models[0] if models else None)
        if model:
            return Route(
                "ollama",
                model,
                "local-first routing" + (f" (paid skipped: {'; '.join(notes)})" if notes else ""),
            )
        notes.append(why if not ok else "no local model installed (ollama pull <model>)")
    else:
        notes.append("ollama is disabled")
    raise AIUnavailable("; ".join(notes))


def run_json_task(db: Session, *, task_type: str, system: str, prompt: str, schema: dict[str, Any],
                  project_id: uuid.UUID | None = None, timeout_s: float = 240, max_output_tokens: int = 2048,
                  forced: Route | None = None) -> TaskResult:  # fmt: skip
    """Run one structured-output task. Usage is always recorded, including failures."""
    chosen = forced or route(db, task_type, system=system, prompt=prompt,
                             max_output_tokens=max_output_tokens, project_id=project_id)  # fmt: skip
    config = db.scalar(select(AIConfiguration).where(AIConfiguration.provider == chosen.provider))
    if config is None:
        raise AIUnavailable(f"provider '{chosen.provider}' is not configured")
    if config.is_paid:  # re-check at call time, also for forced routes (benchmarks)
        reason = paid_block_reason(db, config, _estimate(config, system, prompt, max_output_tokens), project_id)
        if reason:
            raise AIUnavailable(f"{chosen.provider}: {reason}")
    usage = AIUsage(provider=chosen.provider, model=chosen.model, task_type=task_type, project_id=project_id)
    result: ChatResult | None = None
    try:
        result = PROVIDERS[chosen.provider].chat(model=chosen.model, system=system, prompt=prompt, schema=schema,
                                                 timeout_s=timeout_s, max_output_tokens=max_output_tokens)  # fmt: skip
        data = parse_json(result.text)
        errors = [e.message for e in Draft202012Validator(schema).iter_errors(data)]
        if errors:
            raise ProviderError(f"reply does not match the requested schema: {errors[0][:200]}")
    except (ProviderError, json.JSONDecodeError) as exc:
        usage.success, usage.error = False, str(exc)[:1000]
        raise ProviderError(str(exc)) from exc
    finally:
        if result:
            usage.prompt_tokens, usage.completion_tokens = result.prompt_tokens, result.completion_tokens
            usage.latency_ms = result.latency_ms
            usage.cost_usd = round(_cost(config, result.prompt_tokens, result.completion_tokens), 6)
        db.add(usage)
        db.commit()
    return TaskResult(data, chosen.provider, chosen.model, result.latency_ms, result.prompt_tokens,
                      result.completion_tokens, float(usage.cost_usd))  # fmt: skip


def budget_status(db: Session, config: AIConfiguration) -> dict[str, Any]:
    used, limit = spent(db, config.provider), float(config.monthly_budget_usd)
    return {
        "spent_this_month_usd": round(used, 4), "monthly_budget_usd": limit,
        "alert": bool(limit and used >= BUDGET_ALERT_RATIO * limit),
        "exhausted": bool(config.is_paid and used >= limit),
    }  # fmt: skip
