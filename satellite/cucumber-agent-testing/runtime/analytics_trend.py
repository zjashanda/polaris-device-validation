#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local trend aggregation over execution_record.json files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _date_key(record: Dict[str, Any], path: Path) -> str:
    text = str(record.get("created_at", "") or "")
    if len(text) >= 10:
        return text[:10]
    return path.parent.name[:8]


def _project(record: Dict[str, Any]) -> str:
    state = record.get("state", {}) if isinstance(record.get("state"), dict) else {}
    attempts = record.get("attempts", []) if isinstance(record.get("attempts"), list) else []
    for attempt in attempts:
        bdd = attempt.get("bdd", {}) if isinstance(attempt, dict) else {}
        for item in bdd.get("scenario_results", []) if isinstance(bdd.get("scenario_results"), list) else []:
            if isinstance(item, dict):
                runtime = item.get("runtime_replay", {}) if isinstance(item.get("runtime_replay"), dict) else {}
                if runtime.get("project"):
                    return str(runtime.get("project"))
    before = state.get("before", "") if isinstance(state, dict) else ""
    return str(record.get("project_id") or before or "unknown")


@dataclass
class TrendBucket:
    key: str
    total: int = 0
    results: Dict[str, int] = field(default_factory=dict)
    stability: Dict[str, int] = field(default_factory=dict)

    def add(self, record: Dict[str, Any]) -> None:
        self.total += 1
        result = str(record.get("result", "UNKNOWN") or "UNKNOWN").upper()
        stable = str(record.get("stability", "UNKNOWN") or "UNKNOWN").upper()
        self.results[result] = self.results.get(result, 0) + 1
        self.stability[stable] = self.stability.get(stable, 0) + 1

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "total": self.total, "results": self.results, "stability": self.stability}


def build_trend(records: Iterable[Path]) -> Dict[str, Any]:
    by_day: Dict[str, TrendBucket] = {}
    by_project: Dict[str, TrendBucket] = {}
    by_task: Dict[str, TrendBucket] = {}
    scanned: List[str] = []
    for path in records:
        record = _load_json(path)
        if not record:
            continue
        scanned.append(str(path))
        day = _date_key(record, path)
        project = _project(record)
        task = str(record.get("task_id") or record.get("task") or "unknown")
        by_day.setdefault(day, TrendBucket(day)).add(record)
        by_project.setdefault(project, TrendBucket(project)).add(record)
        by_task.setdefault(task, TrendBucket(task)).add(record)
    return {
        "schema": "polaris.analytics_trend.v1",
        "record_count": len(scanned),
        "by_day": [bucket.to_dict() for _, bucket in sorted(by_day.items())],
        "by_project": [bucket.to_dict() for _, bucket in sorted(by_project.items())],
        "by_task": [bucket.to_dict() for _, bucket in sorted(by_task.items())],
        "records": scanned,
    }


def render_trend_markdown(trend: Dict[str, Any]) -> str:
    lines = ["# Polaris Execution Trend", "", f"- record_count: `{trend.get('record_count', 0)}`", ""]
    for section, title in (("by_day", "By Day"), ("by_project", "By Project"), ("by_task", "By Task")):
        lines.extend([f"## {title}", "", "| Key | Total | Results | Stability |", "|---|---:|---|---|"])
        for bucket in trend.get(section, []):
            lines.append(
                f"| `{bucket.get('key')}` | {bucket.get('total', 0)} | `{bucket.get('results', {})}` | `{bucket.get('stability', {})}` |"
            )
        lines.append("")
    return "\n".join(lines)
