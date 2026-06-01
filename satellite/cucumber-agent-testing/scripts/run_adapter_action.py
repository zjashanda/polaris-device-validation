#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render or execute one action from the local adapter registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from polaris_env import load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.adapter_executor import execute_adapter_action  # noqa: E402
from runtime.device_adapter import build_adapter_registry  # noqa: E402


def parse_params(values: list[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise SystemExit(f"--param must be key=value, got: {raw}")
        key, value = raw.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def quote_cmd(args: list[str]) -> str:
    rendered = []
    for arg in args:
        text = str(arg)
        if not text:
            rendered.append('""')
        elif any(ch.isspace() for ch in text) or any(ch in text for ch in ['"', "'", "&"]):
            rendered.append('"' + text.replace('"', '\\"') + '"')
        else:
            rendered.append(text)
    return " ".join(rendered)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Polaris adapter action")
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--adapter-id", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--param", action="append", default=[], help="key=value placeholder parameter")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--execute", action="store_true", help="execute the rendered command; default is dry-run")
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    env_path = resolve_env_path(args.env_file, WORKSPACE_ROOT)
    registry = build_adapter_registry(load_env_payload(env_path))
    params = {"env_file": str(env_path), **parse_params(args.param)}
    result = execute_adapter_action(
        registry,
        adapter_id=args.adapter_id,
        action_name=args.action,
        params=params,
        allow_side_effects=args.allow_side_effects,
        dry_run=not args.execute,
        cwd=WORKSPACE_ROOT,
        timeout_s=args.timeout_s,
    )
    out = Path(args.out) if args.out else BDD_ROOT / "debug" / "adapter_actions" / f"{args.adapter_id}_{args.action}.json"
    if not out.is_absolute():
        out = (WORKSPACE_ROOT / out).resolve()
    write_json(out, result.to_dict())
    print(out)
    if result.cmd:
        print("$ " + quote_cmd(result.cmd))
    print(f"result={result.result} reason={result.reason}")
    return 0 if result.result in {"PASS", "PLAN_OK"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
