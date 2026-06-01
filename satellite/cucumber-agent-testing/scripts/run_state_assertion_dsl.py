#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run state assertion DSL against runtime_state.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.state_assertion_dsl import evaluate_state_dsl  # noqa: E402


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Polaris state assertion DSL")
    parser.add_argument("--state", required=True, help="runtime_state.json")
    parser.add_argument("--dsl", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    state_path = Path(args.state)
    dsl_path = Path(args.dsl)
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    result = evaluate_state_dsl(state, dsl_path.read_text(encoding="utf-8-sig"))
    out = Path(args.out) if args.out else state_path.parent / "state_assertion_dsl_result.json"
    write_json(out, result)
    print(out)
    print(f"result={result['result']} assertions={result['assertion_count']}")
    return 0 if result["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
