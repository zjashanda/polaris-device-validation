#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capture/diff Polaris evidence snapshots from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from polaris_env import default_env_path, load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.snapshots import (  # noqa: E402
    build_snapshot,
    diff_snapshots,
    read_json_safe,
    snapshot_coverage,
    write_coverage,
    write_diff,
    write_json,
    write_snapshot,
)


def load_task(path: str) -> Dict[str, Any]:
    if not path:
        return {"schema": "polaris.snapshot.task.v1", "task_id": "snapshot"}
    task_path = Path(path)
    if not task_path.is_absolute():
        task_path = WORKSPACE_ROOT / task_path
    return json.loads(task_path.read_text(encoding="utf-8-sig"))


def parse_extra(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {"value": payload}
    except Exception:
        return {"raw": raw}


def resolve_roots(values: List[str], fallback: Path) -> List[Path]:
    roots = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = WORKSPACE_ROOT / path
        roots.append(path)
    return roots or [fallback]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polaris snapshot capture/diff/restore-check helper")
    sub = parser.add_subparsers(dest="action", required=True)

    capture = sub.add_parser("capture", help="capture one snapshot")
    capture.add_argument("--phase", default="snapshot")
    capture.add_argument("--env-file", default="")
    capture.add_argument("--task", default="")
    capture.add_argument("--run-dir", default=".")
    capture.add_argument("--evidence-root", action="append", default=[])
    capture.add_argument("--out", required=True)
    capture.add_argument("--extra-json", default="")

    for action in ("capture-before", "capture-after"):
        item = sub.add_parser(action, help=f"{action} shorthand")
        item.add_argument("--env-file", default="")
        item.add_argument("--task", default="")
        item.add_argument("--run-dir", default=".")
        item.add_argument("--evidence-root", action="append", default=[])
        item.add_argument("--out", required=True)
        item.add_argument("--extra-json", default="")

    diff = sub.add_parser("diff", help="diff two snapshots")
    diff.add_argument("--before", required=True)
    diff.add_argument("--after", required=True)
    diff.add_argument("--out", required=True)
    diff.add_argument("--coverage-out", default="")

    restore = sub.add_parser("restore-check", help="compare before/after and classify restore evidence")
    restore.add_argument("--before", required=True)
    restore.add_argument("--after", required=True)
    restore.add_argument("--out", required=True)
    return parser


def capture_action(args: argparse.Namespace, phase: str) -> int:
    env_path = resolve_env_path(args.env_file or str(default_env_path(WORKSPACE_ROOT)), WORKSPACE_ROOT)
    env_payload = load_env_payload(env_path)
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = WORKSPACE_ROOT / run_dir
    snapshot = build_snapshot(
        env_path=env_path,
        env_payload=env_payload,
        task=load_task(args.task),
        phase=phase,
        run_dir=run_dir,
        evidence_roots=resolve_roots(args.evidence_root, run_dir),
        scope={"runner": "run_snapshot.py", "action": args.action},
        extra_state=parse_extra(args.extra_json),
    )
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = WORKSPACE_ROOT / out_path
    write_json(out_path, snapshot)
    print(out_path)
    print(f"snapshot_id={snapshot.get('snapshot_id')} confidence={snapshot.get('confidence', {}).get('overall')}")
    return 0


def diff_action(args: argparse.Namespace) -> int:
    before = read_json_safe(Path(args.before))
    after = read_json_safe(Path(args.after))
    diff = diff_snapshots(before, after)
    out_path = Path(args.out)
    write_diff(out_path.parent, diff, file_name=out_path.name)
    if args.coverage_out:
        coverage_path = Path(args.coverage_out)
        write_coverage(coverage_path.parent, diff.get("coverage", snapshot_coverage(after, diff)), file_name=coverage_path.name)
    print(out_path)
    print(json.dumps(diff.get("summary", {}), ensure_ascii=False))
    return 0


def restore_action(args: argparse.Namespace) -> int:
    before = read_json_safe(Path(args.before))
    after = read_json_safe(Path(args.after))
    diff = diff_snapshots(before, after)
    changed = diff.get("changed", []) or []
    unknown = diff.get("unknown", []) or []
    status = "PASS" if not changed and not unknown else ("UNKNOWN" if unknown else "CHANGED")
    payload = {
        "schema": "polaris.snapshot_restore_check.v1",
        "result": status,
        "generated_at": diff.get("generated_at"),
        "diff_summary": diff.get("summary", {}),
        "changed_fields": changed[:80],
        "unknown_fields": unknown[:80],
        "rule": "PASS only when no changed/unknown fields remain between before and after snapshots",
    }
    out_path = Path(args.out)
    write_json(out_path, payload)
    print(out_path)
    print(f"result={status}")
    return 0 if status in {"PASS", "UNKNOWN"} else 1


def main() -> int:
    args = build_parser().parse_args()
    if args.action == "capture":
        return capture_action(args, args.phase)
    if args.action == "capture-before":
        return capture_action(args, "before")
    if args.action == "capture-after":
        return capture_action(args, "after")
    if args.action == "diff":
        return diff_action(args)
    if args.action == "restore-check":
        return restore_action(args)
    raise SystemExit(f"unknown action: {args.action}")


if __name__ == "__main__":
    raise SystemExit(main())
