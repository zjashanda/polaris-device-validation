#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate Assertion DSL against a replay timeline."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.assertion_dsl import evaluate_dsl  # noqa: E402
from runtime.events import ValidationEvent  # noqa: E402
from runtime.timeline import Timeline  # noqa: E402


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Polaris assertion DSL.")
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--dsl", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    timeline_payload = json.loads(resolve_path(args.timeline).read_text(encoding="utf-8-sig"))
    allowed = {item.name for item in fields(ValidationEvent)}
    events = [ValidationEvent(**{key: value for key, value in item.items() if key in allowed}) for item in timeline_payload.get("events", [])]
    timeline = Timeline.from_events(events)
    lines = resolve_path(args.dsl).read_text(encoding="utf-8").splitlines()
    result = evaluate_dsl(lines, timeline)
    out = resolve_path(args.out) if args.out else resolve_path(args.timeline).parent / "assertion_dsl_result.json"
    write_json(out, result)
    print(out)
    print(f"result={result['result']} assertions={len(result['assertions'])}")
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
