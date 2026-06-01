#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit legacy long-flow runners for direct low-level hardware calls."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
ROOT = SCRIPT_DIR.parents[2]

DEFAULT_TARGETS = [
    "satellite/cucumber-agent-testing/scripts/run_online_mixed_stress.py",
    "satellite/cucumber-agent-testing/scripts/run_wake_stress.py",
    "satellite/cucumber-agent-testing/scripts/run_wake_matrix.py",
    "satellite/cucumber-agent-testing/scripts/run_false_wake_playback.py",
    "satellite/cucumber-agent-testing/scripts/run_online_vad_special.py",
    "satellite/cucumber-agent-testing/scripts/run_interrupt_injection.py",
    "satellite/cucumber-agent-testing/scripts/measure_interrupt_prerequisites.py",
    "satellite/cucumber-agent-testing/scripts/run_network_recovery_basic.py",
    "satellite/cucumber-agent-testing/scripts/run_oneshot_matrix.py",
    "tools/validation/polaris_fa2_command_batch.py",
    "tools/execution/polaris_case_runner.py",
    "tools/execution/polaris_doc_case_runner.py",
]

FORBIDDEN = [
    (re.compile(r"\bserial\.Serial\b"), "direct pyserial use"),
    (re.compile(r"listenai_play\.py|listenai-play"), "direct listenai playback tool reference"),
    (re.compile(r"tools[/\\]device[/\\]polaris_power_control\.py"), "direct power helper subprocess"),
    (re.compile(r"tools[/\\]device[/\\]polaris_network_orchestrator\.py"), "direct network helper subprocess"),
    (re.compile(r"tools[/\\]validation[/\\]polaris_fa2_command_batch\.py"), "direct FA2 subprocess"),
    (re.compile(r"subprocess\.(?:run|Popen)\("), "direct subprocess execution"),
]

ALLOW_LINE_PATTERNS = [
    re.compile(r"subprocess\.CompletedProcess"),
    re.compile(r"ffmpeg_run = subprocess\.run"),
    re.compile(r"from tools\.device\.polaris_network_orchestrator import (?:collect_window|command_window)"),
    re.compile(r"from tools\.validation\.polaris_fa2_command_batch import (?:pcm_duration_ms|run_playback|run_command_batch)"),
]

ALLOW_FILES = {
    # These are execution orchestrators, not hardware action implementations.
    "satellite/cucumber-agent-testing/scripts/run_task.py",
    "satellite/cucumber-agent-testing/scripts/run_scene.py",
    "satellite/cucumber-agent-testing/scripts/run_optimized_task.py",
    "satellite/cucumber-agent-testing/runtime/validation_kernel.py",
}


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return str(path)


def is_allowed_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in ALLOW_LINE_PATTERNS)


def audit_file(path: Path) -> List[Dict[str, object]]:
    findings: List[Dict[str, object]] = []
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for index, line in enumerate(text, start=1):
        if is_allowed_line(line):
            continue
        for pattern, reason in FORBIDDEN:
            if pattern.search(line):
                findings.append({"path": rel(path), "line": index, "reason": reason, "text": line.strip()})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit adapter-only long-flow runner policy.")
    parser.add_argument("--target", action="append", default=[], help="workspace-relative file to audit; default audits known long runners")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    targets = args.target or DEFAULT_TARGETS
    findings: List[Dict[str, object]] = []
    checked: List[str] = []
    for item in targets:
        normalized = item.replace("\\", "/")
        if normalized in ALLOW_FILES:
            continue
        path = (ROOT / item).resolve()
        if not path.exists():
            findings.append({"path": item, "line": 0, "reason": "missing target", "text": ""})
            continue
        checked.append(rel(path))
        findings.extend(audit_file(path))

    payload = {
        "schema": "polaris.adapter_only_audit.v1",
        "result": "PASS" if not findings else "FAIL",
        "checked": checked,
        "finding_count": len(findings),
        "findings": findings,
    }
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = (ROOT / out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
