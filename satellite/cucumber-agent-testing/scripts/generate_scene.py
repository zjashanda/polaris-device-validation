#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a deterministic Polaris scene graph from a strategy pool."""

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

from runtime.scene_engine import generate_scene_graph, mutate_scene_graph, validate_scene_graph  # noqa: E402


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Polaris scene graph.")
    parser.add_argument("--strategy-file", default="satellite/cucumber-agent-testing/references/scene_strategy_pool.json")
    parser.add_argument("--strategy-name", default="online_mixed_stress")
    parser.add_argument("--task", default="satellite/cucumber-agent-testing/tasks/examples/basic_command.example.json")
    parser.add_argument("--mode", choices=["plan-only", "dry-run", "execute"], default="dry-run")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--mutation", choices=["none", "shuffle", "timing_jitter", "insert_network_recovery"], default="none")
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    strategy_pool = load_json(resolve_path(args.strategy_file))
    scene_id = f"{args.strategy_name}_{args.seed}_{stamp()}"
    scene = generate_scene_graph(
        strategy_pool=strategy_pool,
        strategy_name=args.strategy_name,
        scene_id=scene_id,
        seed=args.seed,
        count=args.count,
        task=args.task,
        mode=args.mode,
    )
    scene = mutate_scene_graph(scene, args.mutation, seed=args.seed + 17)
    env_payload = load_env_payload(resolve_env_path(args.env_file, WORKSPACE_ROOT))
    validation = validate_scene_graph(
        scene.to_dict(),
        network_configured=bool(str(env_payload.get("network", {}).get("wifi_ssid", "")).strip()) if isinstance(env_payload.get("network"), dict) else False,
    )
    payload = scene.to_dict()
    payload["validation"] = validation
    out = resolve_path(args.out) if args.out else BDD_ROOT / "debug" / "scenes" / f"{scene_id}.json"
    write_json(out, payload)
    print(out)
    print(f"result={validation['result']} nodes={len(payload['nodes'])}")
    return 0 if validation["result"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
