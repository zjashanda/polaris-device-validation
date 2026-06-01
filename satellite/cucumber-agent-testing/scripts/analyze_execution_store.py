#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analyze optimized execution records into fingerprints and health metrics."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.failure_analysis import aggregate_health, classify_execution_record, render_health_report  # noqa: E402


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Polaris optimized execution records.")
    parser.add_argument("--runs", default="satellite/cucumber-agent-testing/debug/optimized_runs")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    runs = resolve_path(args.runs)
    out = resolve_path(args.out) if args.out else BDD_ROOT / "debug" / "analysis" / f"{stamp()}_execution_store"
    records: List[Dict[str, Any]] = []
    for path in runs.rglob("execution_record.json") if runs.exists() else []:
        record = load_json(path)
        if record:
            record["_path"] = str(path)
            records.append(record)
    health = aggregate_health(records)
    fingerprints = [classify_execution_record(record).to_dict() | {"record": record.get("_path", "")} for record in records if str(record.get("result", "")).upper() != "PASS"]
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "device_health.json", health)
    write_json(out / "failure_fingerprints.json", fingerprints)
    (out / "device_health_report.md").write_text(render_health_report(health), encoding="utf-8")
    print(out)
    print(f"records={len(records)} fingerprints={len(fingerprints)} pass_rate={health.get('pass_rate', 0):.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
