#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect the local device adapter registry for one Polaris project."""

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

from runtime.device_adapter import AdapterRegistry, build_adapter_registry  # noqa: E402


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_markdown(registry: AdapterRegistry) -> str:
    lines = [
        "# Polaris Device Adapter Registry",
        "",
        f"- project_id: `{registry.project_id}`",
        f"- project_type: `{registry.project_type}`",
        f"- adapter_count: `{len(registry.adapters)}`",
        "",
        "| Adapter | Kind | Status | Resources | Capabilities |",
        "|---|---|---|---|---|",
    ]
    for adapter in registry.adapters:
        lines.append(
            f"| `{adapter.adapter_id}` | `{adapter.kind}` | `{adapter.status}` | "
            f"{'<br>'.join(adapter.resources)} | {'<br>'.join(adapter.capabilities)} |"
        )
    if registry.warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in registry.warnings:
            lines.append(f"- {warning}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Polaris local device adapters")
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    env_path = resolve_env_path(args.env_file, WORKSPACE_ROOT)
    env_payload: Dict[str, Any] = load_env_payload(env_path)
    registry = build_adapter_registry(env_payload)
    out_dir = Path(args.out_dir) if args.out_dir else BDD_ROOT / "debug" / "adapter_registry" / registry.project_id
    if not out_dir.is_absolute():
        out_dir = (WORKSPACE_ROOT / out_dir).resolve()
    write_json(out_dir / "adapter_registry.json", registry.to_dict())
    (out_dir / "adapter_registry.md").write_text(render_markdown(registry), encoding="utf-8")
    print(out_dir)
    print(f"adapters={len(registry.adapters)} warnings={len(registry.warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
