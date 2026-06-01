#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a run_command_control_diagnosis matrix from the FA2 command list."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def default_command_file() -> Path:
    docs_dir = WORKSPACE_ROOT / "docs"
    matches = sorted(docs_dir.glob("fa2*.txt"))
    if not matches:
        raise SystemExit("FA2 command file not found under docs/fa2*.txt")
    return matches[0]


def build_matrix(command_file: Path, delay_ms: int, observe_ms: int, sequence_mode: str) -> dict:
    commands = [line.strip() for line in command_file.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    rows = []
    for index, command in enumerate(commands, start=1):
        rows.append(
            {
                "case_id": f"F{index:03d}",
                "mode": "keep",
                "command": command,
                "expected": command,
                "delay_ms": delay_ms,
                "observe_ms": observe_ms,
                "variable": "fa2_all_commands_baseline",
                "timeout_s": 15,
                "sequence_mode": sequence_mode,
            }
        )
    return {
        "schema": "polaris.command-control-diagnosis.matrix.v1",
        "description": "FA2 all command words baseline split wake validation",
        "source_file": str(command_file),
        "command_count": len(commands),
        "matrix": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build FA2 all-command diagnosis matrix")
    parser.add_argument("--command-file", default="", help="Defaults to docs/fa2*.txt")
    parser.add_argument("--output", required=True)
    parser.add_argument("--delay-ms", type=int, default=1600)
    parser.add_argument("--observe-ms", type=int, default=9000)
    parser.add_argument("--sequence-mode", choices=["split", "oneshot"], default="split")
    args = parser.parse_args()

    command_file = resolve_path(args.command_file) if args.command_file else default_command_file()
    payload = build_matrix(command_file, args.delay_ms, args.observe_ms, args.sequence_mode)
    output = resolve_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(output)
    print(f"command_count={payload['command_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
