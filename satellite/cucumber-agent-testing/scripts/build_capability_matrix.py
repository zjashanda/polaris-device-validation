#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a deterministic capability matrix from polaris.local.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from polaris_env import load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.capability_runtime import build_capability_matrix, render_capability_markdown  # noqa: E402


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Polaris capability matrix")
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    env_path = resolve_env_path(args.env_file, WORKSPACE_ROOT)
    matrix = build_capability_matrix(load_env_payload(env_path))
    out_dir = Path(args.out_dir) if args.out_dir else BDD_ROOT / "debug" / "capability_matrix" / matrix.project_id
    if not out_dir.is_absolute():
        out_dir = (WORKSPACE_ROOT / out_dir).resolve()
    write_json(out_dir / "capability_matrix.json", matrix.to_dict())
    (out_dir / "capability_matrix.md").write_text(render_capability_markdown(matrix), encoding="utf-8")
    print(out_dir)
    print(f"supported={matrix.summary().get('supported', 0)} config_required={matrix.summary().get('config_required', 0)} not_applicable={matrix.summary().get('not_applicable', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
