#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the local Polaris Validation Kernel lifecycle."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from polaris_env import load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.validation_kernel import ValidationKernel  # noqa: E402


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Polaris Validation Kernel MVP")
    parser.add_argument("--task", required=True)
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--mode", choices=["plan-only", "dry-run", "execute"], default="dry-run")
    parser.add_argument("--tag", default="")
    parser.add_argument("--execute-runner", action="store_true", help="delegate to run_optimized_task.py")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--manage-session", action="store_true")
    parser.add_argument("--runtime-strict", action="store_true")
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--command-text", default="", help="覆盖 task 中的单条命令词/在线语料。")
    parser.add_argument("--observe-ms", default="", help="覆盖 task 中的观察窗口。")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    task_path = Path(args.task)
    if not task_path.is_absolute():
        task_path = (WORKSPACE_ROOT / task_path).resolve()
    env_path = resolve_env_path(args.env_file, WORKSPACE_ROOT)
    task = load_json(task_path)
    env_payload = load_env_payload(env_path)
    task_id = str(task.get("task_id") or task_path.stem)
    project_id = str(env_payload.get("project_id") or env_payload.get("_config_source", {}).get("active_project") or "unknown")
    out_dir = Path(args.out_dir) if args.out_dir else BDD_ROOT / "debug" / "kernel_runs" / f"{stamp()}_{project_id}_{task_id}"
    if not out_dir.is_absolute():
        out_dir = (WORKSPACE_ROOT / out_dir).resolve()

    kernel = ValidationKernel(workspace_root=WORKSPACE_ROOT, scripts_dir=SCRIPT_DIR, out_dir=out_dir)
    record = kernel.run(
        task_path=task_path,
        env_path=env_path,
        task=task,
        env_payload=env_payload,
        mode=args.mode,
        tag=args.tag,
        allow_side_effects=args.allow_side_effects,
        execute_runner=args.execute_runner,
        manage_session=args.manage_session,
        runtime_strict=args.runtime_strict,
        max_retries=args.max_retries,
        command_text=args.command_text,
        observe_ms=args.observe_ms,
    )
    print(out_dir)
    print(f"result={record.result} kernel_id={record.kernel_id}")
    return 0 if record.result in {"PASS", "PLAN_OK", "DRY_RUN_OK"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
