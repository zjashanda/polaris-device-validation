#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate simulation-lite logs and optionally build a replay package."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
RUNTIME_REPLAY = SCRIPT_DIR / "runtime_replay.py"
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.simulation import write_simulated_log  # noqa: E402


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate simulated runtime logs.")
    parser.add_argument("--events", default="AudioInjected,WakeDetected,ASRDetected,CommandDetected,TTSStarted")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--profile", default="first_wake")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()

    out_dir = resolve_path(args.out_dir) if args.out_dir else BDD_ROOT / "debug" / "simulation" / f"{stamp()}_{args.profile}"
    log_path = write_simulated_log(out_dir / "simulated.log", [item.strip() for item in args.events.split(",") if item.strip()])
    print(log_path)
    if args.replay:
        replay_out = out_dir / "replay"
        cmd = [sys.executable, str(RUNTIME_REPLAY), "--input-dir", str(out_dir), "--out-dir", str(replay_out), "--profile", args.profile]
        completed = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, encoding="utf-8", errors="replace")
        return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
