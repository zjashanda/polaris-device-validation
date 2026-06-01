#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate runtime_state coverage against lightweight profile policies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


def _as_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _merge_policy(policy: Dict[str, Any], profile: str, project_id: str = "") -> Dict[str, Any]:
    coverage = policy.get("coverage", {}) if isinstance(policy.get("coverage"), dict) else {}
    effective = deepcopy(coverage.get("common", {}) if isinstance(coverage.get("common"), dict) else {})
    profiles = coverage.get("profiles", {}) if isinstance(coverage.get("profiles"), dict) else {}
    specific = profiles.get(profile, {}) if isinstance(profiles.get(profile), dict) else {}
    effective = _deep_merge(effective, specific)
    projects = coverage.get("projects", {}) if isinstance(coverage.get("projects"), dict) else {}
    project_policy = projects.get(project_id, {}) if project_id and isinstance(projects.get(project_id), dict) else {}
    project_common = project_policy.get("common", {}) if isinstance(project_policy.get("common"), dict) else {}
    effective = _deep_merge(effective, project_common)
    project_profiles = project_policy.get("profiles", {}) if isinstance(project_policy.get("profiles"), dict) else {}
    project_specific = project_profiles.get(profile, {}) if isinstance(project_profiles.get(profile), dict) else {}
    effective = _deep_merge(effective, project_specific)
    return effective


def _check(result: str, name: str, expected: Any, actual: Any, reason: str) -> Dict[str, Any]:
    return {
        "name": name,
        "result": result,
        "expected": expected,
        "actual": actual,
        "reason": reason,
    }


def _coverage_from_runtime_state(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    coverage = runtime_state.get("coverage", {}) if isinstance(runtime_state.get("coverage"), dict) else {}
    if coverage:
        return coverage
    history = runtime_state.get("history", []) if isinstance(runtime_state.get("history"), list) else []
    event_counts: Dict[str, int] = {}
    visited_states = set()
    for item in history:
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type", "") or "").strip()
        if event_type:
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        state = str(item.get("state", "") or "").strip()
        if state:
            visited_states.add(state)
    parallel_states = runtime_state.get("parallel_states", {}) if isinstance(runtime_state.get("parallel_states"), dict) else {}
    visited_parallel = {key: [str(value)] for key, value in parallel_states.items() if str(value).strip()}
    return {
        "transition_count": len(history),
        "visited_states": sorted(visited_states),
        "visited_parallel_states": visited_parallel,
        "event_type_counts": event_counts,
        "violation_count": len(runtime_state.get("state_violations", []) or []),
        "violation_severity_counts": {},
        "derived_from_history": True,
    }


def evaluate_state_coverage_policy(runtime_state: Dict[str, Any], profile: str, policy: Dict[str, Any], project_id: str = "") -> Dict[str, Any]:
    """Return PASS/WARN/FAIL for coverage thresholds without replacing business assertions."""

    coverage = _coverage_from_runtime_state(runtime_state)
    event_counts = coverage.get("event_type_counts", {}) if isinstance(coverage.get("event_type_counts"), dict) else {}
    visited_states = set(_as_list(coverage.get("visited_states")))
    visited_parallel = coverage.get("visited_parallel_states", {}) if isinstance(coverage.get("visited_parallel_states"), dict) else {}
    severity_counts = coverage.get("violation_severity_counts", {}) if isinstance(coverage.get("violation_severity_counts"), dict) else {}
    effective = _merge_policy(policy, profile, project_id=project_id)
    checks: List[Dict[str, Any]] = []

    min_transition_count = effective.get("min_transition_count")
    if min_transition_count is not None:
        actual = int(coverage.get("transition_count", 0) or 0)
        expected = int(min_transition_count or 0)
        checks.append(
            _check(
                "PASS" if actual >= expected else "WARN",
                "min_transition_count",
                expected,
                actual,
                "transition coverage is sufficient" if actual >= expected else "transition coverage is lower than profile expectation",
            )
        )

    max_error = effective.get("max_error_violations")
    if max_error is not None:
        actual = int(severity_counts.get("error", 0) or 0)
        expected = int(max_error or 0)
        checks.append(
            _check(
                "PASS" if actual <= expected else "FAIL",
                "max_error_violations",
                expected,
                actual,
                "no unexpected error-level state guard violations" if actual <= expected else "error-level state guard violations exceeded policy",
            )
        )

    max_warn = effective.get("max_warn_violations")
    if max_warn is not None:
        actual = int(severity_counts.get("warn", 0) or 0)
        expected = int(max_warn or 0)
        checks.append(
            _check(
                "PASS" if actual <= expected else "WARN",
                "max_warn_violations",
                expected,
                actual,
                "warn-level state guard violations within policy" if actual <= expected else "warn-level state guard violations exceeded policy",
            )
        )

    for event_type in _as_list(effective.get("required_event_types")):
        actual = int(event_counts.get(event_type, 0) or 0)
        checks.append(
            _check(
                "PASS" if actual > 0 else "FAIL",
                f"required_event_type:{event_type}",
                ">0",
                actual,
                f"{event_type} observed" if actual > 0 else f"{event_type} missing from runtime_state coverage",
            )
        )

    for group in effective.get("required_any_event_types", []) if isinstance(effective.get("required_any_event_types"), list) else []:
        candidates = _as_list(group)
        actual = {item: int(event_counts.get(item, 0) or 0) for item in candidates}
        passed = any(count > 0 for count in actual.values())
        checks.append(
            _check(
                "PASS" if passed else "FAIL",
                "required_any_event_types:" + "|".join(candidates),
                "any >0",
                actual,
                "at least one expected event type observed" if passed else "none of the expected event types was observed",
            )
        )

    for event_type in _as_list(effective.get("forbidden_event_types")):
        actual = int(event_counts.get(event_type, 0) or 0)
        checks.append(
            _check(
                "PASS" if actual == 0 else "FAIL",
                f"forbidden_event_type:{event_type}",
                0,
                actual,
                f"{event_type} not observed" if actual == 0 else f"{event_type} was observed but forbidden by profile policy",
            )
        )

    for state in _as_list(effective.get("required_states")):
        checks.append(
            _check(
                "PASS" if state in visited_states else "WARN",
                f"required_state:{state}",
                "visited",
                sorted(visited_states),
                f"{state} visited" if state in visited_states else f"{state} was not visited",
            )
        )

    required_parallel = effective.get("required_parallel_states", {}) if isinstance(effective.get("required_parallel_states"), dict) else {}
    for region, expected_states in required_parallel.items():
        visited = set(_as_list(visited_parallel.get(region)))
        missing = [item for item in _as_list(expected_states) if item not in visited]
        checks.append(
            _check(
                "PASS" if not missing else "WARN",
                f"required_parallel_states:{region}",
                _as_list(expected_states),
                sorted(visited),
                "all expected parallel states visited" if not missing else "some expected parallel states were not visited",
            )
        )

    required_any_parallel = effective.get("required_any_parallel_states", {}) if isinstance(effective.get("required_any_parallel_states"), dict) else {}
    for region, expected_states in required_any_parallel.items():
        visited = set(_as_list(visited_parallel.get(region)))
        candidates = _as_list(expected_states)
        passed = any(item in visited for item in candidates)
        checks.append(
            _check(
                "PASS" if passed else "WARN",
                f"required_any_parallel_states:{region}",
                candidates,
                sorted(visited),
                "at least one expected parallel state visited" if passed else "none of the expected parallel states was visited",
            )
        )

    if not checks:
        aggregate = "SKIPPED"
    elif any(item["result"] == "FAIL" for item in checks):
        aggregate = "FAIL"
    elif any(item["result"] == "WARN" for item in checks):
        aggregate = "WARN"
    else:
        aggregate = "PASS"
    return {
        "schema": "polaris.state_coverage_policy_result.v1",
        "profile": profile,
        "project_id": project_id,
        "result": aggregate,
        "state_health": runtime_state.get("state_health", "UNKNOWN"),
        "policy": effective,
        "checks": checks,
    }
