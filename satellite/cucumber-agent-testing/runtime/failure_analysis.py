#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Failure fingerprint and device health helpers for execution records."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List


@dataclass
class FailureFingerprint:
    fingerprint: str
    category: str
    reason: str
    task_id: str
    project_id: str = ""
    stability: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def classify_execution_record(record: Dict[str, Any]) -> FailureFingerprint:
    result = str(record.get("result", "") or "").upper()
    stability = str(record.get("stability", "") or "")
    task_id = str(record.get("task_id", "") or record.get("task", "") or "")
    preflight = record.get("preflight", {}) if isinstance(record.get("preflight"), dict) else {}
    project_id = _project_from_record(record)
    category = "PASS" if result == "PASS" else "UNKNOWN_FAIL"
    reason = result or "UNKNOWN"
    if result == "BLOCKED" or stability in {"ENV_RELATED", "PRECHECK_BLOCKED"}:
        category = "ENV_RELATED"
        reason = _first_bad_constraint(preflight) or stability or result
    elif result == "FAIL":
        attempts = record.get("attempts", []) if isinstance(record.get("attempts"), list) else []
        text = " ".join(str(item) for item in attempts)
        lowered = text.lower()
        if any(token in lowered for token in ["reboot", "crash", "watchdog", "panic", "hardfault"]):
            category = "FAIL_REBOOT_OR_CRASH"
        elif "wake" in lowered and ("no" in lowered or "fail" in lowered):
            category = "FAIL_NO_WAKE"
        elif "media" in lowered or "http" in lowered:
            category = "WARN_MEDIA_ERROR"
        else:
            category = "STABLE_FAIL" if stability == "STABLE_FAIL" else "FLAKY_FAIL"
        reason = stability or result
    material = f"{project_id}|{task_id}|{category}|{reason}".encode("utf-8", errors="replace")
    return FailureFingerprint(
        fingerprint="fp_" + hashlib.sha1(material).hexdigest()[:12],
        category=category,
        reason=reason,
        task_id=task_id,
        project_id=project_id,
        stability=stability,
    )


def aggregate_health(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(records)
    total = len(rows)
    result_counts = Counter(str(item.get("result", "UNKNOWN") or "UNKNOWN") for item in rows)
    stability_counts = Counter(str(item.get("stability", "UNKNOWN") or "UNKNOWN") for item in rows)
    by_project: Dict[str, Counter[str]] = defaultdict(Counter)
    fingerprints: List[FailureFingerprint] = []
    for record in rows:
        project = _project_from_record(record) or "unknown"
        by_project[project][str(record.get("result", "UNKNOWN") or "UNKNOWN")] += 1
        fp = classify_execution_record(record)
        if fp.category != "PASS":
            fingerprints.append(fp)
    fp_counts = Counter(item.fingerprint for item in fingerprints)
    fp_payload = []
    for fp in fingerprints:
        if any(item["fingerprint"] == fp.fingerprint for item in fp_payload):
            continue
        item = fp.to_dict()
        item["count"] = fp_counts[fp.fingerprint]
        fp_payload.append(item)
    return {
        "schema": "polaris.device_health.v1",
        "total": total,
        "result_counts": dict(result_counts),
        "stability_counts": dict(stability_counts),
        "by_project": {project: dict(counter) for project, counter in by_project.items()},
        "failure_fingerprints": sorted(fp_payload, key=lambda item: (-int(item["count"]), item["fingerprint"])),
        "pass_rate": (result_counts.get("PASS", 0) / total) if total else 0,
        "blocked_rate": (result_counts.get("BLOCKED", 0) / total) if total else 0,
    }


def render_health_report(health: Dict[str, Any]) -> str:
    lines = [
        "# Polaris Device Health Report",
        "",
        f"- total: `{health.get('total', 0)}`",
        f"- pass_rate: `{health.get('pass_rate', 0):.2%}`",
        f"- blocked_rate: `{health.get('blocked_rate', 0):.2%}`",
        "",
        "## Result Counts",
        "",
    ]
    for key, value in sorted((health.get("result_counts") or {}).items()):
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Stability Counts", ""])
    for key, value in sorted((health.get("stability_counts") or {}).items()):
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Failure Fingerprints", ""])
    for item in health.get("failure_fingerprints", [])[:50]:
        lines.append(f"- `{item['fingerprint']}` {item['category']} count=`{item['count']}` task=`{item.get('task_id','')}` project=`{item.get('project_id','')}` reason={item.get('reason','')}")
    lines.append("")
    return "\n".join(lines)


def _first_bad_constraint(preflight: Dict[str, Any]) -> str:
    for item in preflight.get("constraints", []) if isinstance(preflight.get("constraints"), list) else []:
        if isinstance(item, dict) and str(item.get("result", "")).upper() in {"FAIL", "BLOCKED", "WARN"}:
            return f"{item.get('name')}: {item.get('reason')}"
    return ""


def _project_from_record(record: Dict[str, Any]) -> str:
    preflight = record.get("preflight", {}) if isinstance(record.get("preflight"), dict) else {}
    for item in preflight.get("constraints", []) if isinstance(preflight.get("constraints"), list) else []:
        if isinstance(item, dict) and item.get("name") == "project_selected":
            actual = item.get("actual", {}) if isinstance(item.get("actual"), dict) else {}
            return str(actual.get("project_id", "") or "")
    return ""
