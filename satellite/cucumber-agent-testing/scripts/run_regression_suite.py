#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a non-invasive Polaris regression suite."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]


COMPILE_TARGETS = [
    "tools/logs/polaris_interaction_trace.py",
    "satellite/cucumber-agent-testing/scripts/extract_online_request_ids.py",
    "satellite/cucumber-agent-testing/scripts/build_latency_summary_report.py",
    "satellite/cucumber-agent-testing/scripts/run_online_mixed_stress.py",
    "satellite/cucumber-agent-testing/scripts/run_command_control_diagnosis.py",
    "satellite/cucumber-agent-testing/scripts/selftest_command_control_engine.py",
]

TEXT_TARGETS = [
    "README.md",
    "SKILL.md",
    "docs/skill/requirement-to-execution-workflow.md",
    "docs/html/polaris_framework_explorer.html",
    "tools/logs/polaris_interaction_trace.py",
    "satellite/cucumber-agent-testing/scripts/extract_online_request_ids.py",
    "satellite/cucumber-agent-testing/scripts/build_latency_summary_report.py",
    "satellite/cucumber-agent-testing/scripts/run_online_mixed_stress.py",
    "satellite/cucumber-agent-testing/scripts/run_command_control_diagnosis.py",
]

DEFAULT_LOG_SMOKES = {
    "wb01_online_mixed": "satellite/cucumber-agent-testing/debug/online_mixed_stress/20260528_135718/session/logs/live/merged.log",
    "ws63_control_full": "satellite/cucumber-agent-testing/debug/goal_command_beep/20260528_201015/runs/venusws63_full_baseline/20260528_204530/session/logs/live/merged.log",
    "ws63_online_media": "satellite/cucumber-agent-testing/debug/ws63_online_media_recheck/20260529_141521/session/logs/live/merged.log",
}

SKIP_DIRS = {
    ".git",
    "debug",
    "oldTime",
    "cache",
    "result",
    "results",
    "outputs",
    "_runtime",
    "__pycache__",
}


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT.resolve()))
    except Exception:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def step(name: str, status: str, **payload: Any) -> Dict[str, Any]:
    data = {"name": name, "status": status}
    data.update(payload)
    return data


def run_command(name: str, command: List[str], cwd: Path = WORKSPACE_ROOT) -> Dict[str, Any]:
    proc = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True)
    return step(
        name,
        "PASS" if proc.returncode == 0 else "FAIL",
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout[-8000:],
        stderr=proc.stderr[-8000:],
    )


def check_json_files() -> Dict[str, Any]:
    bad: List[Dict[str, str]] = []
    checked = 0
    for root, dirs, files in os.walk(WORKSPACE_ROOT, topdown=True, onerror=lambda _exc: None):
        dirs[:] = [item for item in dirs if item not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".json") or name == "polaris.local.json":
                continue
            path = Path(root) / name
            try:
                json.loads(path.read_text(encoding="utf-8-sig"))
                checked += 1
            except Exception as exc:
                bad.append({"path": rel(path), "error": str(exc)})
    return step("json_parse", "PASS" if not bad else "FAIL", checked=checked, bad=bad[:50])


def check_text_files() -> Dict[str, Any]:
    bad: List[Dict[str, str]] = []
    checked = 0
    for raw in TEXT_TARGETS:
        path = WORKSPACE_ROOT / raw
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig")
        checked += 1
        if "\ufffd" in text:
            bad.append({"path": raw, "error": "replacement_char"})
        if any(marker in text for marker in ("<<<<<<<", "=======", ">>>>>>>")):
            bad.append({"path": raw, "error": "conflict_marker"})
    return step("text_encoding_conflict", "PASS" if not bad else "FAIL", checked=checked, bad=bad)


def check_html() -> Dict[str, Any]:
    path = WORKSPACE_ROOT / "docs/html/polaris_framework_explorer.html"
    if not path.exists():
        return step("html_parse", "WARN", reason="html file missing", path=rel(path))
    try:
        parser = HTMLParser()
        parser.feed(path.read_text(encoding="utf-8-sig"))
        return step("html_parse", "PASS", path=rel(path))
    except Exception as exc:
        return step("html_parse", "FAIL", path=rel(path), error=str(exc))


def resolve_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = (WORKSPACE_ROOT / path).resolve()
    return path


def run_log_smoke(out_dir: Path, require_log_smoke: bool) -> Dict[str, Any]:
    smoke_root = out_dir / "latency_trace_smoke"
    generated_dirs: List[str] = []
    missing: List[str] = []
    failed: List[Dict[str, Any]] = []
    for name, raw in DEFAULT_LOG_SMOKES.items():
        log_path = resolve_path(raw)
        if not log_path.exists():
            missing.append(raw)
            continue
        trace_out = smoke_root / name
        result = run_command(
            f"extract_online_request_ids:{name}",
            [
                sys.executable,
                "satellite/cucumber-agent-testing/scripts/extract_online_request_ids.py",
                "--log",
                str(log_path),
                "--out-dir",
                str(trace_out),
            ],
        )
        if result["status"] != "PASS":
            failed.append(result)
        else:
            generated_dirs.append(str(trace_out))
    status = "PASS"
    if failed:
        status = "FAIL"
    elif missing and require_log_smoke:
        status = "FAIL"
    elif missing:
        status = "WARN"
    latency_report = None
    if generated_dirs:
        latency_out = out_dir / "latency_summary"
        command = [sys.executable, "satellite/cucumber-agent-testing/scripts/build_latency_summary_report.py", "--out-dir", str(latency_out)]
        for item in generated_dirs:
            command.extend(["--input", item])
        result = run_command("build_latency_summary_report", command)
        if result["status"] != "PASS":
            failed.append(result)
            status = "FAIL"
        else:
            latency_report = str(latency_out)
    return step(
        "latency_log_smoke",
        status,
        generated_dirs=generated_dirs,
        missing_logs=missing,
        failed=failed,
        latency_report=latency_report,
    )


def overall_status(steps: Iterable[Dict[str, Any]]) -> str:
    statuses = [str(item.get("status", "")) for item in steps]
    if any(status == "FAIL" for status in statuses):
        return "FAIL"
    if any(status == "WARN" for status in statuses):
        return "WARN"
    return "PASS"


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Polaris Regression Suite",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Status: `{report.get('status')}`",
        f"- Out dir: `{report.get('out_dir')}`",
        "",
        "## Steps",
        "",
        "| Step | Status | Notes |",
        "| --- | --- | --- |",
    ]
    for item in report.get("steps", []):
        notes = item.get("reason") or item.get("returncode") or item.get("checked") or ""
        if item.get("missing_logs"):
            notes = f"missing_logs={len(item.get('missing_logs', []))}"
        if item.get("latency_report"):
            notes = f"latency_report={item.get('latency_report')}"
        lines.append(f"| {item.get('name')} | {item.get('status')} | {notes} |")
    lines.extend(["", "## Next Actions", ""])
    if report.get("status") == "PASS":
        lines.append("- Static and historical-log regression passed; safe to proceed to low-round real-device smoke.")
    elif report.get("status") == "WARN":
        lines.append("- Static checks passed, but optional evidence is missing; run on a machine with historical debug logs for full smoke.")
    else:
        lines.append("- Fix failed steps before running real-device validation.")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run non-invasive Polaris regression checks.")
    parser.add_argument("--out-dir", default="", help="default: debug/regression_suite/<stamp>")
    parser.add_argument("--skip-log-smoke", action="store_true", help="skip historical log extraction smoke")
    parser.add_argument("--require-log-smoke", action="store_true", help="fail if historical smoke logs are missing")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    out_dir = Path(args.out_dir) if args.out_dir else BDD_ROOT / "debug" / "regression_suite" / stamp()
    if not out_dir.is_absolute():
        out_dir = (WORKSPACE_ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    steps: List[Dict[str, Any]] = []
    existing_compile = [str(WORKSPACE_ROOT / target) for target in COMPILE_TARGETS if (WORKSPACE_ROOT / target).exists()]
    steps.append(run_command("py_compile", [sys.executable, "-m", "py_compile", *existing_compile]))
    steps.append(run_command("selftest_command_control_engine", [sys.executable, "satellite/cucumber-agent-testing/scripts/selftest_command_control_engine.py"]))
    steps.append(check_json_files())
    steps.append(check_text_files())
    steps.append(check_html())
    if not args.skip_log_smoke:
        steps.append(run_log_smoke(out_dir, args.require_log_smoke))

    report = {
        "schema": "polaris.regression_suite.v1",
        "generated_at": now_iso(),
        "out_dir": str(out_dir),
        "status": overall_status(steps),
        "steps": steps,
    }
    write_json(out_dir / "regression_suite_summary.json", report)
    (out_dir / "regression_suite_report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "out_dir": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
