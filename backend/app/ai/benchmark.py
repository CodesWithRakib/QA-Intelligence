"""Model benchmark: identical tasks, deterministic graders, stored results.

Measured: accuracy, false-positive rate, root-cause correctness, test-generation quality, repair
success (the candidate fix is executed against hidden assertions inside the sandbox), structured
output reliability, latency, token usage and cost per successful task.
Not measured in the MVP: regression rate across multi-file repairs and tool-call reliability.
"""

import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers import ProviderError
from app.ai.service import AIUnavailable, Route, run_json_task
from app.config import get_settings
from app.engines import sandbox
from app.engines.sandbox import ContainerSpec, Mount
from app.models import ModelEvaluation

SYSTEM = "You are a precise software quality engineer. Answer only with JSON matching the requested schema."


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_VERDICT = _obj({"verdict": {"type": "string", "enum": ["vulnerable", "safe"]}, "reason": {"type": "string"}})
_REPAIR_CHECK = """from candidate import median
assert median([3, 1, 2]) == 2
assert median([4, 1, 3, 2]) == 2.5
assert median([7]) == 7
try:
    median([])
except ValueError:
    pass
else:
    raise AssertionError("empty input must raise ValueError")
"""


def _expect(field: str, value: str) -> Callable[[Any], tuple[bool, str]]:
    return lambda data: (data.get(field) == value, f"{field}={data.get(field)!r}, expected {value!r}")


def _grade_clamp(data: Any) -> tuple[bool, str]:
    cases = data.get("cases", [])
    wrong = [c for c in cases if max(c["low"], min(c["value"], c["high"])) != c["expected"]]
    kinds = {"below" if c["value"] < c["low"] else "above" if c["value"] > c["high"] else "inside" for c in cases}
    ok = len(cases) >= 4 and not wrong and kinds == {"below", "above", "inside"}
    return (
        ok,
        f"{len(cases)} cases, {len(wrong)} with a wrong expected value, branches covered: {sorted(kinds)}",
    )


def _grade_repair(data: Any) -> tuple[bool, str]:
    """Execute the model's fix against hidden assertions in a network-less sandbox."""
    image = sandbox.RUNTIME_IMAGES["python"]
    if not sandbox.docker_available() or not sandbox.image_present(image):
        return False, "not gradable: sandbox unavailable"
    work = get_settings().cache_dir / "benchmark" / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        (work / "candidate.py").write_text(str(data.get("code", "")), encoding="utf-8")
        (work / "check.py").write_text(_REPAIR_CHECK, encoding="utf-8")
        result = sandbox.run_container(ContainerSpec(
            image, ["python", "check.py"], [Mount(str(work), "/job")], workdir="/job", timeout_s=30,
            env={"PYTHONDONTWRITEBYTECODE": "1"},
        ))  # fmt: skip
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result.exit_code == 0, "hidden assertions passed" if result.exit_code == 0 else result.output[-300:]


@dataclass(frozen=True)
class BenchTask:
    key: str
    task_type: str
    measures: str
    prompt: str
    schema: dict[str, Any]
    grade: Callable[[Any], tuple[bool, str]]


TASKS = [
    BenchTask("classify-sqli", "classify", "accuracy",
              "Is this code vulnerable to SQL injection?\n\nquery = f\"SELECT * FROM users WHERE name = '{name}'\"\ncursor.execute(query)",
              _VERDICT, _expect("verdict", "vulnerable")),
    BenchTask("classify-parameterised", "classify", "false_positive",
              "Is this code vulnerable to SQL injection?\n\ncursor.execute(\"SELECT * FROM users WHERE name = %s\", (name,))",
              _VERDICT, _expect("verdict", "safe")),
    BenchTask("classify-subprocess-list", "classify", "false_positive",
              "Is this code vulnerable to command injection?\n\nsubprocess.run([\"ls\", \"-l\", user_path], check=True)",
              _VERDICT, _expect("verdict", "safe")),
    BenchTask("root-cause-discount", "root_cause", "root_cause",
              "A test fails: apply_discount(200, 10) returned 190, expected 180.\n\n"
              "def apply_discount(price, percent):\n    if not 0 <= percent <= 100:\n        raise ValueError\n"
              "    return round(price - percent, 2)\n\nWhich option is the root cause?\n"
              "A) The range check rejects valid percentages\nB) The percentage is subtracted as an absolute amount\n"
              "C) round() uses the wrong number of digits\nD) The test expectation is wrong",
              _obj({"answer": {"type": "string", "enum": ["A", "B", "C", "D"]}, "reason": {"type": "string"}}),
              _expect("answer", "B")),
    BenchTask("repair-median", "repair", "repair",
              "Fix this function. It must return the middle value for odd lengths, the mean of the two middle "
              "values for even lengths, and raise ValueError for an empty list. Return the complete corrected "
              "Python module in `code`.\n\ndef median(values):\n    ordered = sorted(values)\n    return ordered[len(ordered) // 2]",
              _obj({"code": {"type": "string"}}), _grade_repair),
    BenchTask("testgen-clamp", "test_suggestions", "test_generation",
              "clamp(value, low, high) returns value limited to the inclusive range [low, high]. Produce at least "
              "four test cases covering a value below the range, above the range and inside it.",
              _obj({"cases": {"type": "array", "items": _obj({
                  "value": {"type": "number"}, "low": {"type": "number"}, "high": {"type": "number"},
                  "expected": {"type": "number"}})}}),
              _grade_clamp),
]  # fmt: skip


def run_benchmark(db: Session, provider: str, model: str) -> uuid.UUID:
    """Run every task against one model and store one evaluation row per task."""
    batch = uuid.uuid4()
    for task in TASKS:
        row = ModelEvaluation(batch_id=batch, provider=provider, model=model, task_key=task.key,
                              task_type=task.measures, passed=False, structured_output_ok=False, latency_ms=0)  # fmt: skip
        try:
            result = run_json_task(db, task_type=task.task_type, system=SYSTEM, prompt=task.prompt, schema=task.schema,
                                   timeout_s=300, max_output_tokens=1024, forced=Route(provider, model, "benchmark"))  # fmt: skip
            row.structured_output_ok = True
            row.latency_ms, row.prompt_tokens = result.latency_ms, result.prompt_tokens
            row.completion_tokens, row.cost_usd = result.completion_tokens, result.cost_usd
            row.passed, row.detail = task.grade(result.data)
        except (ProviderError, AIUnavailable) as exc:
            row.detail = f"no usable answer: {exc}"[:1000]
        except (KeyError, TypeError, AttributeError) as exc:
            row.structured_output_ok, row.detail = False, f"answer missing expected fields: {exc!r}"
        db.add(row)
        db.commit()
    return batch


def summarise(db: Session) -> list[dict[str, Any]]:
    """Aggregate the latest batch per (provider, model)."""
    rows = list(db.scalars(select(ModelEvaluation).order_by(ModelEvaluation.created_at.desc())))
    latest: dict[tuple[str, str], uuid.UUID] = {}
    for row in rows:
        latest.setdefault((row.provider, row.model), row.batch_id)
    summaries = []
    for (provider, model), batch in latest.items():
        items = [r for r in rows if r.batch_id == batch]
        passed = [r for r in items if r.passed]
        fp_items = [r for r in items if r.task_type == "false_positive" and r.structured_output_ok]
        cost = sum(float(r.cost_usd) for r in items)

        def rate(kind: str, items: list[ModelEvaluation] = items) -> float | None:
            scoped = [r for r in items if r.task_type == kind]
            return round(sum(r.passed for r in scoped) / len(scoped), 2) if scoped else None

        summaries.append({
            "provider": provider, "model": model, "batch_id": str(batch), "run_at": max(r.created_at for r in items),
            "tasks": len(items), "passed": len(passed), "accuracy": round(len(passed) / len(items), 2),
            "false_positive_rate": round(sum(not r.passed for r in fp_items) / len(fp_items), 2) if fp_items else None,
            "root_cause_correct": rate("root_cause"), "repair_success": rate("repair"),
            "test_generation": rate("test_generation"),
            "structured_output_reliability": round(sum(r.structured_output_ok for r in items) / len(items), 2),
            "avg_latency_ms": round(sum(r.latency_ms for r in items) / len(items)),
            "total_tokens": sum(r.prompt_tokens + r.completion_tokens for r in items),
            "cost_usd": round(cost, 6),
            "cost_per_successful_task_usd": round(cost / len(passed), 6) if passed else None,
            "not_measured": ["regression rate", "tool-call reliability"],
            "details": [{"task": r.task_key, "passed": r.passed, "structured_output_ok": r.structured_output_ok,
                         "latency_ms": r.latency_ms, "detail": r.detail} for r in sorted(items, key=lambda r: r.task_key)],
        })  # fmt: skip
    return summaries
