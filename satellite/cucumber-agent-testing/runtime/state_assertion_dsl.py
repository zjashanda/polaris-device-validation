#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiny assertion DSL over runtime_state.json.

Supported lines:
- EXPECT_STATE parallel_states.power = ON
- FORBID_STATE parallel_states.power = CRASHED
- EXPECT_HISTORY WakeDetected
- EXPECT_ANY_HISTORY TTSStarted|MediaStarted
- FORBID_HISTORY CrashDetected
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List


STATE_RE = re.compile(r"^(?P<op>EXPECT_STATE|FORBID_STATE)\s+(?P<path>[A-Za-z0-9_.-]+)\s*=\s*(?P<value>.+)$")
HISTORY_RE = re.compile(r"^EXPECT_HISTORY\s+(?P<event_type>[A-Za-z0-9_]+)$")
ANY_HISTORY_RE = re.compile(r"^EXPECT_ANY_HISTORY\s+(?P<event_types>[A-Za-z0-9_|, ]+)$")
FORBID_HISTORY_RE = re.compile(r"^FORBID_HISTORY\s+(?P<event_type>[A-Za-z0-9_]+)$")


@dataclass
class StateAssertionResult:
    name: str
    result: str
    reason: str
    actual: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _get_path(payload: Dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def evaluate_state_dsl(state: Dict[str, Any], dsl_text: str) -> Dict[str, Any]:
    assertions: List[StateAssertionResult] = []
    for line_no, raw in enumerate(dsl_text.splitlines(), start=1):
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        match = STATE_RE.match(line)
        if match:
            op = match.group("op")
            path = match.group("path")
            expected = match.group("value").strip()
            actual = _get_path(state, path)
            ok = str(actual) == expected
            if op == "FORBID_STATE":
                ok = not ok
            assertions.append(
                StateAssertionResult(
                    name=f"{op.lower()}:{path}",
                    result="PASS" if ok else "FAIL",
                    reason=f"line {line_no}: {path} actual={actual!r}, expected={expected!r}, op={op}",
                    actual={"path": path, "actual": actual, "expected": expected, "op": op},
                )
            )
            continue
        match = HISTORY_RE.match(line)
        if match:
            event_type = match.group("event_type")
            hits = [item for item in state.get("history", []) if isinstance(item, dict) and item.get("event_type") == event_type]
            assertions.append(
                StateAssertionResult(
                    name=f"expect_history:{event_type}",
                    result="PASS" if hits else "FAIL",
                    reason=f"line {line_no}: history contains {len(hits)} {event_type} events.",
                    actual={"event_type": event_type, "count": len(hits)},
                )
            )
            continue
        match = ANY_HISTORY_RE.match(line)
        if match:
            event_types = [
                item.strip()
                for item in re.split(r"[|,]", match.group("event_types"))
                if item.strip()
            ]
            hits = [
                item
                for item in state.get("history", [])
                if isinstance(item, dict) and item.get("event_type") in set(event_types)
            ]
            assertions.append(
                StateAssertionResult(
                    name=f"expect_any_history:{'|'.join(event_types)}",
                    result="PASS" if hits else "FAIL",
                    reason=f"line {line_no}: history contains {len(hits)} events among {event_types}.",
                    actual={"event_types": event_types, "count": len(hits)},
                )
            )
            continue
        match = FORBID_HISTORY_RE.match(line)
        if match:
            event_type = match.group("event_type")
            hits = [item for item in state.get("history", []) if isinstance(item, dict) and item.get("event_type") == event_type]
            assertions.append(
                StateAssertionResult(
                    name=f"forbid_history:{event_type}",
                    result="PASS" if not hits else "FAIL",
                    reason=f"line {line_no}: history contains {len(hits)} forbidden {event_type} events.",
                    actual={"event_type": event_type, "count": len(hits)},
                )
            )
            continue
        assertions.append(
            StateAssertionResult(
                name=f"parse_error:{line_no}",
                result="FAIL",
                reason=f"line {line_no}: unsupported state assertion syntax: {line}",
                actual={"line": line},
            )
        )
    result = "FAIL" if any(item.result == "FAIL" for item in assertions) else "PASS"
    return {
        "schema": "polaris.state_assertion_dsl_result.v1",
        "result": result,
        "assertion_count": len(assertions),
        "assertions": [item.to_dict() for item in assertions],
    }
