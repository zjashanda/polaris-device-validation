#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build local execution trend reports from optimized run records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.analytics_trend import build_trend, render_trend_markdown  # noqa: E402


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Polaris local analytics trend")
    parser.add_argument("--runs", default="satellite/cucumber-agent-testing/debug/optimized_runs")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    runs = Path(args.runs)
    if not runs.is_absolute():
        runs = (WORKSPACE_ROOT / runs).resolve()
    records = list(runs.rglob("execution_record.json")) if runs.exists() else []
    trend = build_trend(records)
    out_dir = Path(args.out_dir) if args.out_dir else BDD_ROOT / "debug" / "analytics_trend"
    if not out_dir.is_absolute():
        out_dir = (WORKSPACE_ROOT / out_dir).resolve()
    write_json(out_dir / "analytics_trend.json", trend)
    (out_dir / "analytics_trend.md").write_text(render_trend_markdown(trend), encoding="utf-8")
    print(out_dir)
    print(f"records={trend['record_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
