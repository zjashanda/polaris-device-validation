#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate runtime_state.json coverage against the local state coverage policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_POLICY = BDD_ROOT / "references" / "optimization" / "state_assertion_policy.json"
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.state_coverage_policy import evaluate_state_coverage_policy  # noqa: E402


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / value).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Polaris state coverage policy.")
    parser.add_argument("--runtime-state", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project-id", default="", help="optional project-specific coverage policy override key")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    runtime_state = load_json(resolve_path(args.runtime_state))
    policy = load_json(resolve_path(args.policy))
    result = evaluate_state_coverage_policy(runtime_state, args.profile, policy, project_id=args.project_id)
    out = resolve_path(args.out) if args.out else BDD_ROOT / "debug" / "state_coverage_policy" / f"{args.profile}.json"
    write_json(out, result)
    print(out)
    print(f"result={result.get('result')} checks={len(result.get('checks', []))}")
    return 0 if result.get("result") in {"PASS", "WARN", "SKIPPED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
