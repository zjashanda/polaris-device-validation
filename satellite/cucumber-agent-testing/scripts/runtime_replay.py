#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse existing logs into Event Runtime replay artifacts.

This is the first landing step for the runtime rearchitecture: it is offline,
deterministic, and does not touch the DUT.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(BDD_ROOT))

from runtime.capabilities import infer_from_env_file, infer_from_project_name  # noqa: E402
from runtime.replay import build_replay_package  # noqa: E402


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path


def default_capabilities(project: str) -> Dict[str, Any]:
    return infer_from_project_name(project)


def load_capabilities(args: argparse.Namespace) -> Dict[str, Any]:
    project = args.project
    caps = default_capabilities(project)
    if args.env_file:
        project, env_caps = infer_from_env_file(resolve_path(args.env_file), project=project)
        caps.update(env_caps)
        args.project = project
    if args.capabilities:
        caps.update(json.loads(args.capabilities))
    if args.cp_log is not None:
        caps["cp_log"] = args.cp_log
    if args.asr_log is not None:
        caps["asr_log"] = args.asr_log
    return caps


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Event Runtime replay package from existing logs.")
    parser.add_argument("--input-dir", required=True, help="包含 *.log 的证据目录")
    parser.add_argument("--out-dir", default="", help="输出目录，默认写入 debug/runtime_replay")
    parser.add_argument(
        "--profile",
        choices=[
            "first_wake",
            "recognition_mode_wake",
            "basic_command",
            "half_duplex_recognition",
            "full_duplex_recognition",
            "command_batch",
            "command_batch_exploratory",
            "offline_oneshot_matrix",
            "online_oneshot_matrix",
            "wake_matrix",
            "online_vad_special",
            "false_wake_quiet",
            "false_wake_playback",
            "attribution_validator",
            "interrupt_prerequisite_measurement",
            "wake_interrupt",
            "command_interrupt",
            "network_recovery_basic",
        ],
        default="first_wake",
    )
    parser.add_argument("--project", default="", help="项目名，用于默认 capability，例如 cskwb01 / venusws63")
    parser.add_argument("--env-file", default="", help="可选：从 polaris.local.json 推导 active_project 和 capability")
    parser.add_argument("--capabilities", default="", help="JSON 覆盖 capability，例如 {\"cp_log\":false}")
    parser.add_argument("--cp-log", dest="cp_log", action="store_true", default=None)
    parser.add_argument("--no-cp-log", dest="cp_log", action="store_false")
    parser.add_argument("--asr-log", dest="asr_log", action="store_true", default=None)
    parser.add_argument("--no-asr-log", dest="asr_log", action="store_false")
    parser.add_argument("--strict-result", action="store_true", help="结果非 PASS/PASS_WITH_SKIPPED_TIMING 时返回 1")
    args = parser.parse_args()

    input_dir = resolve_path(args.input_dir).resolve()
    if not input_dir.exists():
        raise SystemExit(f"input dir not found: {input_dir}")
    if args.out_dir:
        out_dir = resolve_path(args.out_dir).resolve()
    else:
        out_dir = BDD_ROOT / "debug" / "runtime_replay" / f"{stamp()}_{args.profile}"

    package = build_replay_package(
        input_dir=input_dir,
        out_dir=out_dir,
        profile=args.profile,
        project=args.project,
        capabilities=load_capabilities(args),
    )
    result = package["assertion_summary"].get("result", "UNKNOWN")
    print(out_dir)
    print(f"result={result} events={package['timeline']['event_count']}")
    if args.strict_result and result not in {"PASS", "PASS_WITH_SKIPPED_TIMING"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
