"""Controlled agent system.

An "agent" here is a bounded unit of work with an explicit contract, not a free-running LLM loop.
Agents never call each other: only the orchestrator (the job worker following a fixed workflow)
invokes them, and every invocation is recorded in ``agent_executions``.
"""

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import AgentExecution


@dataclass(frozen=True)
class AgentSpec:
    name: str
    responsibility: str
    inputs: str
    outputs: str
    tools: tuple[str, ...]
    timeout_s: int
    retry_limit: int
    evidence: str
    uses_ai: bool = False


_SPECS = [
    AgentSpec(
        "orchestrator",
        "Runs the fixed QA / repair workflows and invokes the other agents.",
        "job (kind, payload)",
        "job status",
        ("job queue",),
        3600,
        0,
        "job and agent execution records",
    ),
    AgentSpec(
        "project_discovery",
        "Builds the architecture inventory from imported files.",
        "workspace path",
        "inventory, components, routes, endpoints",
        ("filesystem (read-only)",),
        120,
        0,
        "inventory facts trace to files",
    ),
    AgentSpec(
        "architecture_analyst",
        "Writes a narrative summary of the inventory (clearly labelled as AI).",
        "inventory JSON",
        "summary text",
        ("local LLM",),
        300,
        1,
        "summary stored separately from facts",
        True,
    ),
    AgentSpec(
        "qa_planner",
        "Generates the test plan deterministically from the inventory.",
        "snapshot, target URL",
        "test plan and cases",
        ("database",),
        30,
        0,
        "plan notes list what is not covered",
    ),
    AgentSpec(
        "functional_qa",
        "Runs the project's own test suite in the sandbox.",
        "workspace, test command",
        "result, failing tests",
        ("docker sandbox",),
        1800,
        0,
        "captured test output",
    ),
    AgentSpec(
        "browser_qa",
        "Page health, crawl and responsive checks in headless Chromium.",
        "target URL",
        "results, findings",
        ("playwright", "outbound policy"),
        600,
        0,
        "screenshots, console/network log",
    ),
    AgentSpec(
        "api_qa",
        "Contract and negative tests against the OpenAPI specification.",
        "target URL, OpenAPI file",
        "results, findings",
        ("http client", "outbound policy"),
        600,
        0,
        "HTTP exchanges",
    ),
    AgentSpec(
        "database_qa",
        "Static checks on data models and migrations.",
        "inventory",
        "results, findings",
        (),
        30,
        0,
        "inventory facts",
    ),
    AgentSpec(
        "security_qa",
        "Dependency, secret, configuration, SAST and header checks.",
        "workspace / target URL",
        "results, findings",
        ("trivy", "semgrep", "http client"),
        1800,
        0,
        "tool output",
    ),
    AgentSpec(
        "performance_qa",
        "Bounded latency sampling and page load timing (not load testing).",
        "target URL",
        "results, findings",
        ("http client", "playwright"),
        600,
        0,
        "timing samples",
    ),
    AgentSpec(
        "accessibility_qa",
        "Automated axe-core rules on rendered pages.",
        "target URL",
        "results, findings",
        ("playwright", "axe-core"),
        600,
        0,
        "axe node output",
    ),
    AgentSpec(
        "bug_investigator",
        "Explains a finding's likely root cause (AI, advisory).",
        "finding and evidence",
        "explanation text",
        ("local LLM",),
        300,
        1,
        "stored as ai_explanation",
        True,
    ),
    AgentSpec(
        "code_repair",
        "Proposes a patch: deterministic strategies first, AI patch otherwise.",
        "finding, workspace files",
        "repair proposal (diff)",
        ("filesystem (read-only)", "LLM"),
        600,
        1,
        "unified diff",
        True,
    ),
    AgentSpec(
        "independent_reviewer",
        "Policy review of an applied patch (deterministic) plus advisory AI review.",
        "diff, declared files",
        "approve / reject with reasons",
        ("git", "LLM"),
        300,
        0,
        "review record",
        True,
    ),
    AgentSpec(
        "regression_verifier",
        "Re-runs targeted and regression checks before and after a patch.",
        "isolated workspace",
        "before/after results",
        ("docker sandbox", "trivy"),
        3600,
        0,
        "regression results",
    ),
    AgentSpec(
        "reporting",
        "Assembles reports strictly from stored results.",
        "run / project id",
        "markdown report",
        ("database",),
        60,
        0,
        "every number is a stored count",
    ),
]
AGENTS: dict[str, AgentSpec] = {spec.name: spec for spec in _SPECS}
NOT_IMPLEMENTED = ["product_intelligence"]


@contextmanager
def agent_run(db: Session, name: str, *, project_id: uuid.UUID | None, job_id: uuid.UUID | None = None,
              attempt: int = 1, **input_summary: Any) -> Iterator[dict[str, Any]]:  # fmt: skip
    """Record one agent invocation. The yielded dict becomes the stored output summary."""
    spec = AGENTS[name]  # unknown agents are a programming error
    output: dict[str, Any] = {}
    started = time.monotonic()
    status, error = "succeeded", None
    try:
        yield output
    except Exception as exc:
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        duration = int((time.monotonic() - started) * 1000)
        if status == "succeeded" and duration > spec.timeout_s * 1000:
            status, error = "timed_out", f"exceeded the {spec.timeout_s}s budget"
        db.add(AgentExecution(agent=name, job_id=job_id, project_id=project_id, status=status, attempt=attempt,
                              duration_ms=duration, input_summary=input_summary, output_summary=output, error=error))  # fmt: skip
