from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UsageSummary:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    cost_usd: float | None = None
    session_id: str | None = None
    step_count: int = 0
    event_count: int = 0
    error_count: int = 0
    malformed_line_count: int = 0


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


def _as_nonnegative_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return max(0.0, float(value))


def parse_synergy_jsonl(output: str) -> UsageSummary:
    steps: dict[str, dict[str, Any]] = {}
    event_count = 0
    error_count = 0
    malformed_line_count = 0
    session_id: str | None = None

    for index, raw_line in enumerate(output.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed_line_count += 1
            continue
        if not isinstance(event, dict):
            malformed_line_count += 1
            continue

        event_count += 1
        candidate_session_id = event.get("sessionID")
        if isinstance(candidate_session_id, str) and candidate_session_id:
            session_id = candidate_session_id

        event_type = event.get("type")
        if event_type == "error":
            error_count += 1
            continue
        if event_type != "step_finish":
            continue

        part = event.get("part")
        if not isinstance(part, dict) or part.get("type") != "step-finish":
            continue
        part_id = part.get("id")
        key = part_id if isinstance(part_id, str) and part_id else f"line:{index}"
        steps[key] = part

    if not steps:
        return UsageSummary(
            session_id=session_id,
            event_count=event_count,
            error_count=error_count,
            malformed_line_count=malformed_line_count,
        )

    input_tokens = 0
    output_tokens = 0
    cache_tokens = 0
    cost_usd = 0.0
    for part in steps.values():
        tokens = part.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}
        cache = tokens.get("cache")
        if not isinstance(cache, dict):
            cache = {}

        input_tokens += _as_nonnegative_int(tokens.get("input"))
        output_tokens += _as_nonnegative_int(tokens.get("output"))
        output_tokens += _as_nonnegative_int(tokens.get("reasoning"))
        cache_tokens += _as_nonnegative_int(cache.get("read"))
        cache_tokens += _as_nonnegative_int(cache.get("write"))
        cost_usd += _as_nonnegative_float(part.get("cost"))

    return UsageSummary(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_tokens=cache_tokens,
        cost_usd=cost_usd,
        session_id=session_id,
        step_count=len(steps),
        event_count=event_count,
        error_count=error_count,
        malformed_line_count=malformed_line_count,
    )
