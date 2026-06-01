#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operate on a replay_package.json with Replay VM-lite."""

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

from runtime.replay_vm import ReplayVM  # noqa: E402


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay VM-lite for Polaris replay packages.")
    parser.add_argument("--package", required=True)
    parser.add_argument("--event-id", default="")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--steps", type=int, default=0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    package_path = resolve_path(args.package)
    package = json.loads(package_path.read_text(encoding="utf-8-sig"))
    vm = ReplayVM(package)
    if args.event_id:
        vm.time_travel(args.event_id, offset=args.offset)
    else:
        for _ in range(max(0, args.steps)):
            if not vm.step():
                break
        vm.snapshot()
    payload = vm.to_dict()
    out = resolve_path(args.out) if args.out else package_path.parent / "replay_vm_state.json"
    write_json(out, payload)
    print(out)
    print(f"cursor={payload['cursor']} events={payload['event_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
