#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a generated scene graph through run_optimized_task.py."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from polaris_env import load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
RUN_OPTIMIZED_TASK = SCRIPT_DIR / "run_optimized_task.py"
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.scene_engine import validate_scene_graph  # noqa: E402


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def quote_cmd(args: List[str]) -> str:
    return " ".join('"' + item.replace('"', '\\"') + '"' if any(ch.isspace() for ch in item) else item for item in args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Polaris scene graph.")
    parser.add_argument("--scene", required=True)
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--mode", choices=["plan-only", "dry-run", "execute"], default="")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--manage-session", action="store_true")
    parser.add_argument("--runtime-strict", action="store_true")
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--out-root", default="")
    parser.add_argument("--print-command", action="store_true")
    args = parser.parse_args()

    scene_path = resolve_path(args.scene)
    scene = load_json(scene_path)
    env_payload = load_env_payload(resolve_env_path(args.env_file, WORKSPACE_ROOT))
    validation = validate_scene_graph(
        scene,
        network_configured=bool(str(env_payload.get("network", {}).get("wifi_ssid", "")).strip()) if isinstance(env_payload.get("network"), dict) else False,
    )
    out_root = resolve_path(args.out_root) if args.out_root else BDD_ROOT / "debug" / "scene_runs" / f"{stamp()}_{scene.get('scene_id', scene_path.stem)}"
    out_root.mkdir(parents=True, exist_ok=True)
    write_json(out_root / "scene_validation.json", validation)
    if validation["result"] == "FAIL":
        print(out_root)
        print("result=FAIL scene_validation=FAIL")
        return 1

    node_results: List[Dict[str, Any]] = []
    for node in scene.get("nodes", []):
        if not isinstance(node, dict):
            continue
        mode = args.mode or str(node.get("mode", "") or "dry-run")
        cmd = [
            sys.executable,
            str(RUN_OPTIMIZED_TASK),
            "--task",
            str(resolve_path(str(node.get("task", "")))),
            "--mode",
            mode,
            "--env-file",
            str(resolve_path(args.env_file)),
            "--out-root",
            str(out_root / "optimized"),
            "--max-retries",
            str(args.max_retries),
        ]
        command_text = str(node.get("command_text", "") or "")
        if command_text:
            cmd.extend(["--command-text", command_text])
        if args.allow_side_effects:
            cmd.append("--allow-side-effects")
        if args.manage_session:
            cmd.append("--manage-session")
        if args.runtime_strict:
            cmd.append("--runtime-strict")
        if args.print_command:
            print("$ " + quote_cmd(cmd))
            node_results.append({"node_id": node.get("node_id"), "printed": True, "cmd": cmd})
            continue
        completed = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        node_out = out_root / f"{node.get('node_id', 'node')}_stdout.log"
        node_out.write_text(completed.stdout or "", encoding="utf-8")
        node_results.append(
            {
                "node_id": node.get("node_id"),
                "category": node.get("category", ""),
                "command_text": command_text,
                "returncode": completed.returncode,
                "stdout_log": str(node_out),
            }
        )
        print(f"node={node.get('node_id')} returncode={completed.returncode} command={command_text}")
        if completed.returncode != 0:
            break
    result = "PASS" if node_results and all(item.get("returncode", 0) == 0 for item in node_results if not item.get("printed")) else "FAIL"
    if args.print_command:
        result = "PLAN_OK"
    summary = {"scene": str(scene_path), "result": result, "validation": validation, "nodes": node_results}
    write_json(out_root / "scene_run_summary.json", summary)
    print(out_root)
    print(f"result={result} nodes={len(node_results)}")
    return 0 if result in {"PASS", "PLAN_OK"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
