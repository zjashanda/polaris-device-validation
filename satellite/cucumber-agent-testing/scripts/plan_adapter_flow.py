#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render or execute high-level flows through the adapter action executor."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from polaris_env import load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_FLOW_MAP = BDD_ROOT / "references" / "adapter_flow_map.json"
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.adapter_executor import execute_adapter_action  # noqa: E402
from runtime.device_adapter import build_adapter_registry  # noqa: E402


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / value).resolve()


def nested(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def parse_params(values: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise SystemExit(f"--param must be key=value, got: {raw}")
        key, value = raw.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def default_context(env_payload: Dict[str, Any], env_path: Path) -> Dict[str, str]:
    return {
        "env_file": str(env_path),
        "wifi_ssid": str(nested(env_payload, "network", "wifi_ssid") or ""),
        "wifi_password": str(nested(env_payload, "network", "wifi_password") or env_payload.get("wifi_password", "") or ""),
        "half_duplex_timeout_s": str(nested(env_payload, "timeouts", "half_duplex_timeout_s") or "15"),
        "full_duplex_timeout_s": str(nested(env_payload, "timeouts", "full_duplex_timeout_s") or "60"),
        "volume": str(nested(env_payload, "audio", "playback_volume") or "30"),
        "enable": "1",
        "time_from": "22:00",
        "time_to": "07:00",
        "awake_threshold": "0",
        "repeat": "1",
        "audio_file": "",
        "run_dir": str(BDD_ROOT / "debug" / "adapter_flows"),
        "out": str(BDD_ROOT / "debug" / "adapter_flows" / "snapshot.json"),
        "before": "",
        "after": "",
    }


def render_placeholders(value: Any, context: Dict[str, str]) -> str:
    text = str(value)
    for key, item in context.items():
        text = text.replace("{" + key + "}", str(item))
    return text


def quote_cmd(args: List[str]) -> str:
    rendered: List[str] = []
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
    parser = argparse.ArgumentParser(description="Plan or execute a Polaris adapter flow.")
    parser.add_argument("--flow", required=True)
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--flow-map", default=str(DEFAULT_FLOW_MAP))
    parser.add_argument("--param", action="append", default=[], help="flow placeholder override, key=value")
    parser.add_argument("--execute", action="store_true", help="execute rendered adapter actions; default only dry-runs")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    env_path = resolve_env_path(args.env_file, WORKSPACE_ROOT)
    env_payload = load_env_payload(env_path)
    flow_map = load_json(resolve_path(args.flow_map))
    flows = flow_map.get("flows", {}) if isinstance(flow_map.get("flows"), dict) else {}
    flow = flows.get(args.flow)
    if not isinstance(flow, dict):
        raise SystemExit(f"adapter flow not found: {args.flow}")

    context = default_context(env_payload, env_path)
    context.update(parse_params(args.param))
    registry = build_adapter_registry(env_payload)
    dry_run = not args.execute
    step_results: List[Dict[str, Any]] = []
    for index, step in enumerate(flow.get("steps", []) if isinstance(flow.get("steps"), list) else [], start=1):
        if not isinstance(step, dict):
            continue
        params = {
            key: render_placeholders(value, context)
            for key, value in (step.get("params", {}) if isinstance(step.get("params"), dict) else {}).items()
        }
        result = execute_adapter_action(
            registry,
            adapter_id=str(step.get("adapter_id", "")),
            action_name=str(step.get("action", "")),
            params={**context, **params},
            allow_side_effects=args.allow_side_effects,
            dry_run=dry_run,
            cwd=WORKSPACE_ROOT,
            timeout_s=args.timeout_s,
        )
        item = {"index": index, **result.to_dict(), "cmdline": quote_cmd(result.cmd)}
        step_results.append(item)
        print(f"step={index} adapter={result.adapter_id} action={result.action} result={result.result}")
        if result.cmd:
            print("$ " + item["cmdline"])
        if result.result not in {"PASS", "PLAN_OK"}:
            break

    aggregate = "PASS" if step_results and all(item.get("result") == "PASS" for item in step_results) else "PLAN_OK"
    if any(item.get("result") in {"FAIL", "BLOCKED"} for item in step_results):
        aggregate = str(next(item.get("result") for item in step_results if item.get("result") in {"FAIL", "BLOCKED"}))
    payload = {
        "schema": "polaris.adapter_flow_plan.v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "flow": args.flow,
        "description": flow.get("description", ""),
        "env_file": str(env_path),
        "dry_run": dry_run,
        "result": aggregate,
        "context": context,
        "steps": step_results,
    }
    out = resolve_path(args.out) if args.out else BDD_ROOT / "debug" / "adapter_flows" / f"{args.flow}.json"
    write_json(out, payload)
    print(out)
    print(f"result={aggregate} steps={len(step_results)}")
    return 0 if aggregate in {"PASS", "PLAN_OK"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
