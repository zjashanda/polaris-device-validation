#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a scene graph through Validation Kernel per node.

This keeps scene scheduling deterministic: each node compiles IR, captures
adapter/capability/resource/constraint snapshots, optionally delegates to
run_optimized_task.py, then writes one scene-level record.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from polaris_env import load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.scene_engine import validate_scene_graph  # noqa: E402
from runtime.validation_kernel import ValidationKernel  # noqa: E402
from runtime.validation_ir import build_scene_validation_ir_bundle  # noqa: E402


PASS_RESULTS = {"PASS", "PLAN_OK", "DRY_RUN_OK", "PASS_WITH_SKIPPED_TIMING"}


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def safe_run_label(value: str, *, max_len: int = 28) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "scene").strip("._-")
    if not label:
        label = "scene"
    if len(label) <= max_len:
        return label
    return label[:max_len].rstrip("._-") or "scene"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / value).resolve()


def quote_cmd(args: List[str]) -> str:
    rendered: List[str] = []
    for item in args:
        if not item:
            rendered.append('""')
        elif any(ch.isspace() for ch in item) or any(ch in item for ch in ['"', "'", "&"]):
            rendered.append('"' + item.replace('"', '\\"') + '"')
        else:
            rendered.append(item)
    return " ".join(rendered)


def network_configured(env_payload: Dict[str, Any]) -> bool:
    network = env_payload.get("network", {}) if isinstance(env_payload.get("network"), dict) else {}
    return bool(str(network.get("wifi_ssid", "") or "").strip())


def node_dependencies_passed(node: Dict[str, Any], results_by_id: Dict[str, Dict[str, Any]]) -> bool:
    for dep in node.get("depends_on", []) or []:
        dep_result = str((results_by_id.get(str(dep)) or {}).get("result", "") or "")
        if dep_result not in PASS_RESULTS:
            return False
    return True


def render_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        "# Kernel Scene Run",
        "",
        f"- scene: `{summary.get('scene_id', '')}`",
        f"- result: `{summary.get('result', '')}`",
        f"- node_count: `{len(summary.get('nodes', []))}`",
        "",
        "| Node | Category | Command | Result | Kernel Record |",
        "|---|---|---|---|---|",
    ]
    for item in summary.get("nodes", []):
        lines.append(
            "| {node} | `{category}` | {command} | `{result}` | `{record}` |".format(
                node=item.get("node_id", ""),
                category=item.get("category", ""),
                command=str(item.get("command_text", "")).replace("|", "\\|"),
                result=item.get("result", ""),
                record=item.get("kernel_record", ""),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Polaris scene graph through Validation Kernel.")
    parser.add_argument("--scene", required=True)
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--mode", choices=["plan-only", "dry-run", "execute"], default="")
    parser.add_argument("--execute-runner", action="store_true", help="每个 scene node 委托 run_optimized_task.py。")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--manage-session", action="store_true")
    parser.add_argument("--runtime-strict", action="store_true")
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--retry-blocked", action="store_true", help="节点 runner 遇到 BLOCKED 时也按 max-retries 重试。")
    parser.add_argument("--honor-gaps", action="store_true", help="execute 模式按 node.metadata.random_gap_s 等待。")
    parser.add_argument("--emit-ir-bundle", action="store_true", help="输出 scene 级 Validation IR bundle，用于检查 scene/task 统一 IR。")
    parser.add_argument("--out-root", default="")
    parser.add_argument("--print-command", action="store_true")
    args = parser.parse_args()

    scene_path = resolve_path(args.scene)
    env_path = resolve_env_path(args.env_file, WORKSPACE_ROOT)
    scene = load_json(scene_path)
    env_payload = load_env_payload(env_path)
    validation = validate_scene_graph(scene, network_configured=network_configured(env_payload))
    scene_id = str(scene.get("scene_id", scene_path.stem) or scene_path.stem)
    out_root = resolve_path(args.out_root) if args.out_root else BDD_ROOT / "debug" / "kernel_scene_runs" / f"{stamp()}_{safe_run_label(scene_id)}"
    out_root.mkdir(parents=True, exist_ok=True)
    write_json(out_root / "scene_validation.json", validation)
    ir_bundle_path = ""
    if args.emit_ir_bundle:
        def load_task_from_scene(value: str) -> Dict[str, Any]:
            return load_json(resolve_path(value))

        ir_bundle = build_scene_validation_ir_bundle(
            scene=scene,
            env_payload=env_payload,
            load_task=load_task_from_scene,
            scene_path=str(scene_path),
            env_file=str(env_path),
            mode=args.mode,
            allow_side_effects=args.allow_side_effects,
        )
        ir_bundle_path = str(out_root / "scene_validation_ir_bundle.json")
        write_json(Path(ir_bundle_path), ir_bundle)

    if validation.get("result") == "FAIL":
        summary = {"schema": "polaris.kernel_scene_record.v1", "scene": str(scene_path), "scene_id": scene_id, "result": "FAIL", "validation": validation, "ir_bundle": ir_bundle_path, "nodes": []}
        write_json(out_root / "kernel_scene_record.json", summary)
        print(out_root)
        print("result=FAIL scene_validation=FAIL")
        return 1

    if args.print_command:
        for node in scene.get("nodes", []):
            if not isinstance(node, dict):
                continue
            mode = args.mode or str(node.get("mode", "") or "dry-run")
            cmd = [
                sys.executable,
                str(SCRIPT_DIR / "run_validation_kernel.py"),
                "--task",
                str(resolve_path(str(node.get("task", "")))),
                "--env-file",
                str(env_path),
                "--mode",
                mode,
                "--out-dir",
                str(out_root / "nodes" / str(node.get("node_id", "node"))),
                "--max-retries",
                str(args.max_retries),
            ]
            if args.execute_runner:
                cmd.append("--execute-runner")
            command_text = str(node.get("command_text", "") or "")
            if command_text:
                cmd.extend(["--command-text", command_text])
            if args.allow_side_effects:
                cmd.append("--allow-side-effects")
            if args.manage_session:
                cmd.append("--manage-session")
            if args.runtime_strict:
                cmd.append("--runtime-strict")
            if args.retry_blocked:
                cmd.append("--retry-blocked")
            print("$ " + quote_cmd(cmd))
        print(out_root)
        if ir_bundle_path:
            print(f"ir_bundle={ir_bundle_path}")
        print(f"result=PLAN_OK nodes={len(scene.get('nodes', []) or [])}")
        return 0

    node_results: List[Dict[str, Any]] = []
    results_by_id: Dict[str, Dict[str, Any]] = {}
    for node in scene.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id", f"node_{len(node_results) + 1:03d}"))
        if not node_dependencies_passed(node, results_by_id):
            skipped = {
                "node_id": node_id,
                "category": node.get("category", ""),
                "command_text": node.get("command_text", ""),
                "result": "SKIPPED",
                "reason": "dependency did not pass",
            }
            node_results.append(skipped)
            results_by_id[node_id] = skipped
            continue
        if args.honor_gaps and (args.mode or node.get("mode", "")) == "execute":
            gap = int((node.get("metadata", {}) or {}).get("random_gap_s", 0) or 0)
            if gap > 0:
                time.sleep(gap)

        task_path = resolve_path(str(node.get("task", "")))
        task = load_json(task_path)
        mode = args.mode or str(node.get("mode", "") or "dry-run")
        kernel = ValidationKernel(workspace_root=WORKSPACE_ROOT, scripts_dir=SCRIPT_DIR, out_dir=out_root / "nodes" / node_id)
        record = kernel.run(
            task_path=task_path,
            env_path=env_path,
            task=task,
            env_payload=env_payload,
            mode=mode,
            tag="",
            allow_side_effects=args.allow_side_effects,
            execute_runner=args.execute_runner,
            manage_session=args.manage_session,
            runtime_strict=args.runtime_strict,
            max_retries=args.max_retries,
            retry_blocked=args.retry_blocked,
            command_text=str(node.get("command_text", "") or ""),
        )
        item = {
            "node_id": node_id,
            "category": node.get("category", ""),
            "command_text": node.get("command_text", ""),
            "result": record.result,
            "kernel_id": record.kernel_id,
            "kernel_record": str(out_root / "nodes" / node_id / "kernel_record.json"),
            "created_at": now_iso(),
        }
        node_results.append(item)
        results_by_id[node_id] = item
        print(f"node={node_id} result={record.result} command={node.get('command_text', '')}")
        if record.result not in PASS_RESULTS:
            break

    final_result = "PASS" if node_results and all(str(item.get("result", "")) in PASS_RESULTS for item in node_results) else "FAIL"
    if any(str(item.get("result", "")) == "SKIPPED" for item in node_results):
        final_result = "BLOCKED"
    summary = {
        "schema": "polaris.kernel_scene_record.v1",
        "scene": str(scene_path),
        "scene_id": scene_id,
        "created_at": now_iso(),
        "result": final_result,
        "validation": validation,
        "ir_bundle": ir_bundle_path,
        "nodes": node_results,
    }
    write_json(out_root / "kernel_scene_record.json", summary)
    (out_root / "kernel_scene_report.md").write_text(render_markdown(summary), encoding="utf-8")
    print(out_root)
    print(f"result={final_result} nodes={len(node_results)}")
    return 0 if final_result in PASS_RESULTS else 1


if __name__ == "__main__":
    raise SystemExit(main())
