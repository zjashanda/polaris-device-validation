#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a Polaris task with execution records, preflight and retry.

This is the first operational layer above run_task.py. It does not replace the
existing Cucumber runner; it wraps it and writes deterministic evidence around
each attempt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from polaris_env import default_env_path, load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
RUN_TASK = SCRIPT_DIR / "run_task.py"
PLAN_ADAPTER_FLOW = SCRIPT_DIR / "plan_adapter_flow.py"
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.constraint_engine import evaluate_constraints  # noqa: E402
from runtime.resource_runtime import ResourceLockManager, build_resource_snapshot  # noqa: E402
from runtime.snapshots import (  # noqa: E402
    build_snapshot,
    diff_snapshots as build_snapshot_diff,
    write_coverage as write_snapshot_coverage,
    write_diff as write_snapshot_diff,
    write_snapshot,
)


PASS_RESULTS = {"PASS", "PASS_WITH_WARNINGS", "PASS_WITH_SKIPPED_TIMING", "DRY_RUN_OK", "PLAN_OK"}
BLOCKED_RESULTS = {"BLOCKED", "PRECHECK_BLOCKED"}
TIMING_RESULTS = {"TIMING_AMBIGUOUS"}
WARNING_RESULTS = {"WARN", "WARNING", "PASS_WITH_WARNINGS"}


def stamp() -> str:
    # Millisecond stamps can collide when precheck/print/dry-run are launched
    # together; include pid so evidence directories stay isolated.
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{os.getpid()}"


def safe_run_label(value: str, *, max_len: int = 16) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "task").strip("._-")
    if not label:
        label = "task"
    if len(label) <= max_len:
        return label
    return label[:max_len].rstrip("._-") or "task"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def nested(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def resolve_workspace_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (WORKSPACE_ROOT / path).resolve()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def quote_cmd(args: List[str]) -> str:
    result: List[str] = []
    for arg in args:
        if not arg:
            result.append('""')
        elif any(ch.isspace() for ch in arg) or any(ch in arg for ch in ['"', "'", "&"]):
            result.append('"' + arg.replace('"', '\\"') + '"')
        else:
            result.append(arg)
    return " ".join(result)


def resolve_bool(cli_value: Optional[bool], *values: Any) -> bool:
    if cli_value is not None:
        return bool(cli_value)
    for value in values:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "y"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"false", "0", "no", "n"}:
            return False
    return False


def resolve_mode(args: argparse.Namespace, task: Dict[str, Any]) -> str:
    return first_non_empty(args.mode, nested(task, "runner", "mode"), task.get("mode"), "dry-run")


def is_command_control_diagnosis_task(task: Dict[str, Any]) -> bool:
    runner = task.get("runner", {}) if isinstance(task.get("runner"), dict) else {}
    schema = str(task.get("schema", ""))
    runner_type = str(runner.get("type", ""))
    script = str(runner.get("script", "") or runner.get("entrypoint", ""))
    return (
        "command-control-diagnosis" in schema
        or runner_type == "command_control_diagnosis"
        or "run_command_control_diagnosis.py" in script
    )


def resolve_env_file(args: argparse.Namespace, task: Dict[str, Any]) -> Path:
    value = first_non_empty(args.env_file, nested(task, "environment", "env_file"), nested(task, "runner", "env_file"), str(default_env_path(WORKSPACE_ROOT)))
    return resolve_env_path(value, WORKSPACE_ROOT)


def default_out_root(env_payload: Dict[str, Any]) -> Path:
    debug_root = first_non_empty(nested(env_payload, "paths", "debug_root"), "satellite/cucumber-agent-testing/debug")
    return resolve_workspace_path(debug_root) / "optimized_runs"


def snapshot_state(
    env_path: Path,
    env_payload: Dict[str, Any],
    task: Dict[str, Any],
    label: str,
    *,
    run_root: Optional[Path] = None,
    evidence_roots: Optional[List[Path]] = None,
    extra_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return build_snapshot(
        env_path=env_path,
        env_payload=env_payload,
        task=task,
        phase=label,
        run_dir=run_root,
        evidence_roots=evidence_roots or ([run_root] if run_root else []),
        scope={"runner": "run_optimized_task", "task_id": first_non_empty(task.get("task_id"), "")},
        extra_state=extra_state,
    )


def diff_states(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    return build_snapshot_diff(before, after)


def build_run_task_command(args: argparse.Namespace, task_path: Path, env_path: Path, mode: str, allow_side_effects: bool, manage_session: bool, runtime_strict: bool, passthrough: List[str]) -> List[str]:
    cmd = [sys.executable, str(RUN_TASK), "--task", str(task_path), "--mode", mode, "--env-file", str(env_path)]
    if args.tag:
        cmd.extend(["--tag", args.tag])
    if args.device_key:
        cmd.extend(["--device-key", args.device_key])
    if args.wake_word:
        cmd.extend(["--wake-word", args.wake_word])
    if args.command_text:
        cmd.extend(["--command-text", args.command_text])
    if args.command_file:
        cmd.extend(["--command-file", args.command_file])
    if args.command_limit:
        cmd.extend(["--command-limit", args.command_limit])
    if args.observe_ms:
        cmd.extend(["--observe-ms", args.observe_ms])
    if args.compile_first:
        cmd.append("--compile-first")
    if args.strict:
        cmd.append("--strict")
    if allow_side_effects:
        cmd.append("--allow-side-effects")
    if manage_session:
        cmd.append("--manage-session")
    else:
        cmd.append("--no-manage-session")
    if runtime_strict:
        cmd.append("--runtime-strict")
    cmd.extend(passthrough)
    return cmd


def render_placeholders(value: Any, context: Dict[str, str]) -> str:
    text = str(value)
    for key, item in context.items():
        text = text.replace("{" + key + "}", str(item))
    return text


def adapter_flow_context(args: argparse.Namespace, task: Dict[str, Any], env_payload: Dict[str, Any], mode: str) -> Dict[str, str]:
    inputs = task.get("inputs", {}) if isinstance(task.get("inputs"), dict) else {}
    execution = task.get("execution", {}) if isinstance(task.get("execution"), dict) else {}
    return {
        "mode": mode,
        "wake_word": first_non_empty(args.wake_word, inputs.get("wake_word"), nested(env_payload, "device", "wake_word"), "小美小美"),
        "command_text": first_non_empty(args.command_text, inputs.get("command_text"), "打开空调"),
        "command_file": first_non_empty(args.command_file, inputs.get("command_file"), nested(env_payload, "paths", "command_file")),
        "command_limit": first_non_empty(args.command_limit, inputs.get("command_limit"), nested(env_payload, "limits", "command_limit"), "20"),
        "observe_ms": first_non_empty(args.observe_ms, nested(task, "execution", "observe_ms"), nested(env_payload, "timeouts", "observe_ms"), "15000"),
        "device_key": first_non_empty(args.device_key, nested(env_payload, "audio", "default_playback_device_key")),
        "wifi_ssid": first_non_empty(nested(env_payload, "network", "wifi_ssid"), env_payload.get("current_connected_ssid")),
        "wifi_password": first_non_empty(nested(env_payload, "network", "wifi_password"), env_payload.get("wifi_password")),
        "half_duplex_timeout_s": first_non_empty(execution.get("half_duplex_timeout_s"), nested(env_payload, "timeouts", "half_duplex_timeout_s"), "15"),
        "full_duplex_timeout_s": first_non_empty(execution.get("full_duplex_timeout_s"), nested(env_payload, "timeouts", "full_duplex_timeout_s"), "60"),
        "volume": first_non_empty(execution.get("volume"), nested(env_payload, "audio", "playback_volume"), "30"),
    }


def normalize_adapter_flows(task: Dict[str, Any], phase: str) -> List[Dict[str, Any]]:
    execution = task.get("execution", {}) if isinstance(task.get("execution"), dict) else {}
    result: List[Dict[str, Any]] = []
    legacy_key = f"{phase}_adapter_flows"
    raw_legacy = execution.get(legacy_key, [])
    if isinstance(raw_legacy, list):
        result.extend(raw_legacy)
    raw = execution.get("adapter_flows", {})
    if isinstance(raw, dict):
        value = raw.get(phase, [])
        if isinstance(value, list):
            result.extend(value)
    elif isinstance(raw, list) and phase == "pre":
        result.extend(raw)
    normalized: List[Dict[str, Any]] = []
    for item in result:
        if isinstance(item, str):
            normalized.append({"flow": item})
        elif isinstance(item, dict):
            normalized.append(dict(item))
    return normalized


def build_adapter_flow_command(
    *,
    flow: Dict[str, Any],
    phase: str,
    index: int,
    env_path: Path,
    out_dir: Path,
    mode: str,
    allow_side_effects: bool,
    context: Dict[str, str],
) -> tuple[List[str], Path, str]:
    flow_name = first_non_empty(flow.get("flow"), flow.get("name"))
    out_path = out_dir / f"{phase}_{index:02d}_{flow_name or 'adapter_flow'}.json"
    cmd = [sys.executable, str(PLAN_ADAPTER_FLOW), "--flow", flow_name, "--env-file", str(env_path), "--out", str(out_path)]
    params = flow.get("params", {}) if isinstance(flow.get("params"), dict) else {}
    for key, value in params.items():
        cmd.extend(["--param", f"{key}={render_placeholders(value, context)}"])
    should_execute = mode == "execute" and allow_side_effects and bool(flow.get("execute", True))
    if should_execute:
        cmd.extend(["--execute", "--allow-side-effects"])
    return cmd, out_path, flow_name


def flow_enabled_for_mode(flow: Dict[str, Any], mode: str) -> bool:
    when = first_non_empty(flow.get("when"), "always").lower()
    if when in {"always", "all"}:
        return True
    if when in {"execute", "dry-run", "plan-only"}:
        return when == mode
    if when == "not-plan-only":
        return mode != "plan-only"
    return True


def run_adapter_flow_phase(
    *,
    phase: str,
    flows: List[Dict[str, Any]],
    run_root: Path,
    env_path: Path,
    task: Dict[str, Any],
    args: argparse.Namespace,
    mode: str,
    allow_side_effects: bool,
    env_payload: Dict[str, Any],
) -> Dict[str, Any]:
    phase_dir = run_root / "adapter_flows" / phase
    phase_dir.mkdir(parents=True, exist_ok=True)
    context = adapter_flow_context(args, task, env_payload, mode)
    items: List[Dict[str, Any]] = []
    for index, flow in enumerate(flows, start=1):
        flow_name = first_non_empty(flow.get("flow"), flow.get("name"))
        required = resolve_bool(None, flow.get("required"), phase == "pre")
        if not flow_name:
            items.append({"index": index, "result": "BLOCKED", "required": required, "reason": "adapter flow name is empty"})
            break
        if not flow_enabled_for_mode(flow, mode):
            items.append({"index": index, "flow": flow_name, "result": "SKIPPED", "required": required, "reason": f"when={flow.get('when')} mode={mode}"})
            continue
        cmd, out_path, _ = build_adapter_flow_command(
            flow=flow,
            phase=phase,
            index=index,
            env_path=env_path,
            out_dir=phase_dir,
            mode=mode,
            allow_side_effects=allow_side_effects,
            context=context,
        )
        stdout_path = phase_dir / f"{phase}_{index:02d}_{flow_name}.log"
        completed = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        payload = load_json(out_path)
        result = first_non_empty(payload.get("result"), "FAIL" if completed.returncode else "PASS")
        item = {
            "index": index,
            "flow": flow_name,
            "phase": phase,
            "required": required,
            "returncode": completed.returncode,
            "result": result,
            "cmd": cmd,
            "cmdline": quote_cmd(cmd),
            "out": rel(out_path),
            "stdout_log": rel(stdout_path),
        }
        items.append(item)
        if required and result not in {"PASS", "PLAN_OK", "SKIPPED"}:
            break
    aggregate = "PASS"
    if any(item.get("result") in {"FAIL", "BLOCKED"} and item.get("required") for item in items):
        aggregate = str(next(item.get("result") for item in items if item.get("result") in {"FAIL", "BLOCKED"} and item.get("required")))
    elif any(item.get("result") == "PLAN_OK" for item in items):
        aggregate = "PLAN_OK"
    elif all(item.get("result") == "SKIPPED" for item in items) and items:
        aggregate = "SKIPPED"
    summary = {"schema": "polaris.adapter_flow_phase.v1", "phase": phase, "result": aggregate, "items": items}
    write_json(run_root / "adapter_flows" / f"{phase}.json", summary)
    return summary


def parse_run_dir(output: str) -> str:
    candidates: List[str] = []
    for raw in output.splitlines():
        line = raw.strip().strip('"')
        if not line or line.startswith("$ "):
            continue
        if "cucumber-agent-testing" in line and ("debug" in line or "runs" in line):
            candidates.append(line)
    for candidate in reversed(candidates):
        if candidate.endswith(".md"):
            candidate = str(Path(candidate).parent)
        path = Path(candidate)
        if not path.is_absolute():
            path = WORKSPACE_ROOT / path
        if path.exists():
            return str(path.resolve())
    pattern = re.compile(r"([A-Za-z]:\\[^\r\n]+?cucumber-agent-testing\\debug\\[^\r\n ]+)")
    matches = pattern.findall(output)
    for candidate in reversed(matches):
        path = Path(candidate.strip())
        if path.exists():
            return str(path.resolve())
    return ""


def extract_bdd_result(run_dir: str) -> Dict[str, Any]:
    if not run_dir:
        return {}
    root = Path(run_dir)
    summary = load_json(root / "bdd_run_summary.json")
    run_summary = load_json(root / "run_summary.json")
    runtime_summary = load_json(root / "runtime_replay_summary.json")
    scenario_results = summary.get("scenario_results", [])
    scenario_statuses = [
        str(item.get("result", "") or "").upper()
        for item in scenario_results
        if isinstance(item, dict) and str(item.get("result", "") or "").strip()
    ]
    if scenario_statuses:
        if any(item in {"FAIL", "ERROR"} for item in scenario_statuses):
            result = "FAIL"
        elif any(item in BLOCKED_RESULTS for item in scenario_statuses):
            result = "BLOCKED"
        elif any(item in TIMING_RESULTS for item in scenario_statuses):
            result = "TIMING_AMBIGUOUS"
        elif all(item in PASS_RESULTS for item in scenario_statuses):
            result = "PASS"
        else:
            result = scenario_statuses[0]
    else:
        result = first_non_empty(summary.get("result"), run_summary.get("result"), summary.get("status"), run_summary.get("status"))
    runtime_results: List[str] = []
    if isinstance(runtime_summary, dict):
        for item in runtime_summary.values():
            if isinstance(item, dict):
                runtime_results.append(first_non_empty(item.get("result"), nested(item, "assertion_summary", "result")))
    return {
        "result": result,
        "scenario_results": scenario_results,
        "runtime_results": [item for item in runtime_results if item],
        "bdd_run_summary": rel(root / "bdd_run_summary.json") if (root / "bdd_run_summary.json").exists() else "",
        "runtime_replay_summary": rel(root / "runtime_replay_summary.json") if (root / "runtime_replay_summary.json").exists() else "",
    }


def classify_command_control_summary(summary: Dict[str, Any]) -> str:
    summary_result = str(summary.get("result", "") or "").upper()
    if summary_result in BLOCKED_RESULTS:
        return "BLOCKED"
    results = summary.get("results", [])
    if not isinstance(results, list):
        results = []
    case_results = [str(item.get("result", "") or "").upper() for item in results if isinstance(item, dict)]
    if not case_results:
        return "BLOCKED" if summary_result == "BLOCKED" else first_non_empty(summary_result, "FAIL").upper()
    if any(item in BLOCKED_RESULTS for item in case_results):
        return "BLOCKED"
    if any(item in {"FAIL", "ERROR"} for item in case_results):
        return "FAIL"
    if any(item in WARNING_RESULTS for item in case_results):
        return "PASS_WITH_WARNINGS"
    if all(item in PASS_RESULTS for item in case_results):
        return "PASS"
    return first_non_empty(summary_result, case_results[0], "FAIL").upper()


def extract_command_control_result(run_dir: str) -> Dict[str, Any]:
    if not run_dir:
        return {}
    root = Path(run_dir)
    summary_path = root / "summary.json"
    summary = load_json(summary_path)
    if not summary:
        return {"kind": "command_control", "result": "", "summary_json": "", "reason": "summary.json not found"}
    results = summary.get("results", [])
    if not isinstance(results, list):
        results = []
    case_results = [str(item.get("result", "") or "").upper() for item in results if isinstance(item, dict)]
    counts: Dict[str, int] = {}
    for item in case_results:
        counts[item] = counts.get(item, 0) + 1
    blocked_cases = [item for item in results if isinstance(item, dict) and str(item.get("result", "")).upper() in BLOCKED_RESULTS]
    failed_cases = [item for item in results if isinstance(item, dict) and str(item.get("result", "")).upper() in {"FAIL", "ERROR"}]
    warning_cases = [item for item in results if isinstance(item, dict) and str(item.get("result", "")).upper() in WARNING_RESULTS]
    return {
        "kind": "command_control",
        "result": classify_command_control_summary(summary),
        "summary_result": summary.get("result", ""),
        "case_result_counts": counts,
        "case_count": len(results),
        "root_cause": summary.get("root_cause", {}),
        "serial_coverage": summary.get("serial_coverage", {}),
        "blocked_cases": [
            {"case_id": item.get("case_id"), "reason": item.get("reason"), "attribution": item.get("attribution")}
            for item in blocked_cases[:5]
        ],
        "failed_cases": [
            {"case_id": item.get("case_id"), "reason": item.get("reason"), "attribution": item.get("attribution")}
            for item in failed_cases[:5]
        ],
        "warning_cases": [
            {"case_id": item.get("case_id"), "reason": item.get("reason"), "attribution": item.get("attribution")}
            for item in warning_cases[:5]
        ],
        "summary_json": rel(summary_path),
        "matrix_csv": rel(root / "matrix.csv") if (root / "matrix.csv").exists() else "",
        "report": rel(root / "report.md") if (root / "report.md").exists() else "",
    }


def extract_task_result(task: Dict[str, Any], run_dir: str) -> Dict[str, Any]:
    if is_command_control_diagnosis_task(task):
        return extract_command_control_result(run_dir)
    result = extract_bdd_result(run_dir)
    if result:
        result["kind"] = "bdd"
    return result


def classify_attempt(mode: str, returncode: int, task_result: Dict[str, Any]) -> str:
    if returncode != 0:
        return "FAIL"
    if mode == "dry-run":
        return "DRY_RUN_OK"
    if mode == "plan-only":
        return "PLAN_OK"
    result = first_non_empty(task_result.get("result"))
    runtime_results = [str(item).upper() for item in task_result.get("runtime_results", [])]
    if result.upper() in PASS_RESULTS:
        return result.upper()
    if result.upper() in BLOCKED_RESULTS:
        return "BLOCKED"
    if result.upper() in TIMING_RESULTS:
        return "TIMING_AMBIGUOUS"
    if result.upper() in {"FAIL", "ERROR"}:
        return "FAIL"
    if runtime_results:
        if all(item in PASS_RESULTS for item in runtime_results):
            return "PASS"
        if any(item == "BLOCKED" for item in runtime_results):
            return "BLOCKED"
        if any(item == "FAIL" for item in runtime_results):
            return "FAIL"
        if any(item in TIMING_RESULTS for item in runtime_results):
            return "TIMING_AMBIGUOUS"
    return "PASS"


def aggregate_attempts(attempts: List[Dict[str, Any]], max_attempts: int) -> Dict[str, Any]:
    results = [str(item.get("result", "")).upper() for item in attempts]
    if any(item == "PASS" for item in results):
        first_pass = next(index for index, item in enumerate(results, start=1) if item == "PASS")
        stability = "PASS" if first_pass == 1 else "FLAKY_PASS"
        return {"result": "PASS", "stability": stability, "passed_attempt": first_pass, "attempts": len(attempts)}
    if any(item == "PASS_WITH_WARNINGS" for item in results):
        first_pass = next(index for index, item in enumerate(results, start=1) if item == "PASS_WITH_WARNINGS")
        stability = "PASS_WITH_WARNINGS" if first_pass == 1 else "FLAKY_PASS_WITH_WARNINGS"
        return {"result": "PASS_WITH_WARNINGS", "stability": stability, "passed_attempt": first_pass, "attempts": len(attempts)}
    if any(item in PASS_RESULTS for item in results):
        first_pass = next(index for index, item in enumerate(results, start=1) if item in PASS_RESULTS)
        result = results[first_pass - 1]
        stability = result if first_pass == 1 else f"FLAKY_{result}"
        return {"result": result, "stability": stability, "passed_attempt": first_pass, "attempts": len(attempts)}
    if any(item in BLOCKED_RESULTS for item in results):
        return {"result": "BLOCKED", "stability": "ENV_RELATED", "attempts": len(attempts)}
    if any(item in TIMING_RESULTS for item in results):
        return {"result": "TIMING_AMBIGUOUS", "stability": "TIMING_AMBIGUOUS", "attempts": len(attempts)}
    if len(set(results)) <= 1 and len(attempts) >= max_attempts:
        return {"result": "FAIL", "stability": "STABLE_FAIL", "attempts": len(attempts)}
    return {"result": "FAIL", "stability": "FLAKY_FAIL", "attempts": len(attempts)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Polaris task with execution record, retry and resource/constraint preflight.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--mode", choices=["plan-only", "dry-run", "execute"], default="")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--device-key", default="")
    parser.add_argument("--wake-word", default="")
    parser.add_argument("--command-text", default="")
    parser.add_argument("--command-file", default="")
    parser.add_argument("--command-limit", default="")
    parser.add_argument("--observe-ms", default="")
    parser.add_argument("--compile-first", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--allow-side-effects", dest="allow_side_effects", action="store_true", default=None)
    parser.add_argument("--no-allow-side-effects", dest="allow_side_effects", action="store_false")
    parser.add_argument("--manage-session", dest="manage_session", action="store_true", default=None)
    parser.add_argument("--no-manage-session", dest="manage_session", action="store_false")
    parser.add_argument("--runtime-strict", dest="runtime_strict", action="store_true", default=None)
    parser.add_argument("--no-runtime-strict", dest="runtime_strict", action="store_false")
    parser.add_argument("--max-retries", type=int, default=-1, help="失败后重试次数；-1 使用 task/policy 默认。")
    parser.add_argument("--retry-blocked", action="store_true", help="默认不重试 BLOCKED；开启后 BLOCKED 也重试。")
    parser.add_argument("--out-root", default="")
    parser.add_argument("--precheck-only", action="store_true")
    parser.add_argument("--allow-precheck-blocked", action="store_true")
    parser.add_argument("--no-resource-lock", action="store_true", help="跳过本地资源锁；仅建议调试时使用。")
    parser.add_argument("--lock-root", default="", help="资源锁目录，默认 debug/resource_locks")
    parser.add_argument("--print-command", action="store_true")
    args, passthrough = parser.parse_known_args()

    task_path = resolve_workspace_path(args.task).resolve()
    task = load_json(task_path)
    env_path = resolve_env_file(args, task).resolve()
    env_payload = load_env_payload(env_path)
    mode = resolve_mode(args, task)
    execution = task.get("execution", {}) if isinstance(task.get("execution"), dict) else {}
    policy = task.get("policy", {}) if isinstance(task.get("policy"), dict) else {}
    allow_side_effects = resolve_bool(args.allow_side_effects, execution.get("allow_side_effects"), policy.get("allow_side_effects"))
    manage_session = resolve_bool(args.manage_session, execution.get("manage_session"), policy.get("manage_session"))
    runtime_strict = resolve_bool(args.runtime_strict, execution.get("runtime_strict"), policy.get("runtime_strict"))
    max_retries = args.max_retries if args.max_retries >= 0 else int(first_non_empty(execution.get("max_retries"), policy.get("max_retries"), 0) or 0)
    max_attempts = max(1, max_retries + 1)
    out_root = resolve_workspace_path(args.out_root).resolve() if args.out_root else default_out_root(env_payload).resolve()
    task_id = first_non_empty(task.get("task_id"), task_path.stem)
    run_root = out_root / f"{stamp()}_{safe_run_label(task_id)}"
    run_root.mkdir(parents=True, exist_ok=True)

    preflight = evaluate_constraints(task=task, env_payload=env_payload, mode=mode, allow_side_effects=allow_side_effects, tag=args.tag)
    before = snapshot_state(env_path, env_payload, task, "before", run_root=run_root, evidence_roots=[run_root])
    write_json(run_root / "preflight.json", preflight)
    write_json(run_root / "state" / "before.json", before)
    write_snapshot(run_root, before, file_name="snapshot_before.json")

    cmd = build_run_task_command(args, task_path, env_path, mode, allow_side_effects, manage_session, runtime_strict, passthrough)
    if (
        is_command_control_diagnosis_task(task)
        and "--out-root" not in passthrough
        and not first_non_empty(nested(task, "runner", "out_root"), nested(task, "execution", "out_root"))
    ):
        cmd.extend(["--out-root", str(run_root / "runner_output")])
    pre_adapter_flows = normalize_adapter_flows(task, "pre")
    post_adapter_flows = normalize_adapter_flows(task, "post")
    adapter_flow_print_commands = []
    adapter_context = adapter_flow_context(args, task, env_payload, mode)
    for phase, flows in (("pre", pre_adapter_flows), ("post", post_adapter_flows)):
        for index, flow in enumerate(flows, start=1):
            if not flow_enabled_for_mode(flow, mode):
                continue
            flow_cmd, flow_out, flow_name = build_adapter_flow_command(
                flow=flow,
                phase=phase,
                index=index,
                env_path=env_path,
                out_dir=run_root / "adapter_flows" / phase,
                mode=mode,
                allow_side_effects=allow_side_effects,
                context=adapter_context,
            )
            adapter_flow_print_commands.append({"phase": phase, "flow": flow_name, "cmd": flow_cmd, "cmdline": quote_cmd(flow_cmd), "out": rel(flow_out)})
    write_json(
        run_root / "command.json",
        {
            "cmd": cmd,
            "cmdline": quote_cmd(cmd),
            "adapter_flows": adapter_flow_print_commands,
            "created_at": now_iso(),
        },
    )
    if args.print_command:
        print(run_root)
        for item in adapter_flow_print_commands:
            print(f"# adapter_flow phase={item['phase']} flow={item['flow']}")
            print("$ " + item["cmdline"])
        print("$ " + quote_cmd(cmd))
        return 0
    if args.precheck_only:
        print(run_root)
        print(f"preflight={preflight['result']}")
        return 0 if preflight["result"] != "FAIL" else 1
    if preflight["result"] in {"FAIL", "BLOCKED"} and not args.allow_precheck_blocked:
        after_payload = load_env_payload(env_path)
        after = snapshot_state(env_path, after_payload, task, "after", run_root=run_root, evidence_roots=[run_root])
        state_diff = diff_states(before, after)
        write_json(run_root / "state" / "after.json", after)
        write_json(run_root / "state_diff.json", state_diff)
        write_snapshot(run_root, after, file_name="snapshot_after.json")
        diff_path = write_snapshot_diff(run_root, state_diff)
        coverage_path = write_snapshot_coverage(run_root, state_diff.get("coverage", {}))
        record = {
            "schema": "polaris.execution_record.v1",
            "task": rel(task_path),
            "env_file": rel(env_path),
            "created_at": now_iso(),
            "result": preflight["result"],
            "stability": "PRECHECK_BLOCKED",
            "preflight": preflight,
            "snapshot": {
                "before": rel(run_root / "snapshot_before.json"),
                "after": rel(run_root / "snapshot_after.json"),
                "diff": rel(diff_path),
                "coverage": rel(coverage_path),
                "coverage_summary": state_diff.get("coverage", {}),
                "diff_summary": state_diff.get("summary", {}),
            },
            "attempts": [],
        }
        write_json(run_root / "execution_record.json", record)
        print(run_root)
        print(f"result={record['result']} stability={record['stability']}")
        return 1

    attempts: List[Dict[str, Any]] = []
    adapter_flow_records: Dict[str, Any] = {}
    adapter_flow_blocker: Dict[str, Any] = {}
    locks = []
    lock_root = resolve_workspace_path(args.lock_root).resolve() if args.lock_root else default_out_root(env_payload).parent / "resource_locks"
    lock_manager = ResourceLockManager(lock_root)
    try:
        if mode == "execute" and not args.no_resource_lock:
            locks = lock_manager.acquire(build_resource_snapshot(env_payload, task).claims, run_id=run_root.name, owner=task_id)
            write_json(run_root / "resource_locks.json", {"lock_root": str(lock_root), "locks": [item.to_dict() for item in locks]})

        if pre_adapter_flows:
            adapter_flow_records["pre"] = run_adapter_flow_phase(
                phase="pre",
                flows=pre_adapter_flows,
                run_root=run_root,
                env_path=env_path,
                task=task,
                args=args,
                mode=mode,
                allow_side_effects=allow_side_effects,
                env_payload=env_payload,
            )
            if adapter_flow_records["pre"].get("result") in {"FAIL", "BLOCKED"}:
                adapter_flow_blocker = {"phase": "pre", "result": adapter_flow_records["pre"].get("result"), "reason": "required adapter flow failed or was blocked"}

        if not adapter_flow_blocker:
            for attempt_index in range(1, max_attempts + 1):
                attempt_dir = run_root / f"attempt_{attempt_index:02d}"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                started = now_iso()
                completed = subprocess.run(
                    cmd,
                    cwd=str(WORKSPACE_ROOT),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
                finished = now_iso()
                output = completed.stdout or ""
                (attempt_dir / "stdout.log").write_text(output, encoding="utf-8")
                run_dir = parse_run_dir(output)
                task_result = extract_task_result(task, run_dir)
                result = classify_attempt(mode, completed.returncode, task_result)
                attempt = {
                    "attempt": attempt_index,
                    "started_at": started,
                    "finished_at": finished,
                    "returncode": completed.returncode,
                    "result": result,
                    "run_dir": rel(Path(run_dir)) if run_dir else "",
                    "stdout_log": rel(attempt_dir / "stdout.log"),
                    "evidence": task_result,
                }
                if task_result.get("kind") == "command_control":
                    attempt["command_control"] = task_result
                else:
                    attempt["bdd"] = task_result
                attempts.append(attempt)
                write_json(attempt_dir / "attempt.json", attempt)
                append_jsonl(run_root / "attempts.jsonl", attempt)
                print(f"attempt={attempt_index} result={result} returncode={completed.returncode} run_dir={attempt['run_dir']}")
                if result in PASS_RESULTS:
                    break
                if result in BLOCKED_RESULTS and not args.retry_blocked:
                    break

        if post_adapter_flows:
            adapter_flow_records["post"] = run_adapter_flow_phase(
                phase="post",
                flows=post_adapter_flows,
                run_root=run_root,
                env_path=env_path,
                task=task,
                args=args,
                mode=mode,
                allow_side_effects=allow_side_effects,
                env_payload=env_payload,
            )
    except RuntimeError as exc:
        attempt = {
            "attempt": len(attempts) + 1,
            "started_at": now_iso(),
            "finished_at": now_iso(),
            "returncode": 2,
            "result": "BLOCKED",
            "run_dir": "",
            "stdout_log": "",
            "reason": str(exc),
        }
        attempts.append(attempt)
        append_jsonl(run_root / "attempts.jsonl", attempt)
        write_json(run_root / "resource_lock_error.json", {"error": str(exc), "lock_root": str(lock_root)})
    finally:
        if locks:
            lock_manager.release(locks)

    after_payload = load_env_payload(env_path)
    evidence_roots = [run_root]
    for attempt in attempts:
        run_dir_value = first_non_empty(attempt.get("run_dir"))
        if run_dir_value:
            evidence_roots.append(resolve_workspace_path(run_dir_value))
    after = snapshot_state(
        env_path,
        after_payload,
        task,
        "after",
        run_root=run_root,
        evidence_roots=evidence_roots,
        extra_state={"attempts": attempts, "adapter_flow_blocker": adapter_flow_blocker},
    )
    state_diff = diff_states(before, after)
    write_json(run_root / "state" / "after.json", after)
    write_json(run_root / "state_diff.json", state_diff)
    write_snapshot(run_root, after, file_name="snapshot_after.json")
    diff_path = write_snapshot_diff(run_root, state_diff)
    coverage_path = write_snapshot_coverage(run_root, state_diff.get("coverage", {}))
    if adapter_flow_blocker and not attempts:
        aggregate = {"result": adapter_flow_blocker.get("result", "BLOCKED"), "stability": "ADAPTER_FLOW_BLOCKED", "attempts": 0}
    else:
        aggregate = aggregate_attempts(attempts, max_attempts)
    record = {
        "schema": "polaris.execution_record.v1",
        "task": rel(task_path),
        "task_id": task_id,
        "env_file": rel(env_path),
        "mode": mode,
        "created_at": now_iso(),
        "result": aggregate["result"],
        "stability": aggregate["stability"],
        "max_retries": max_retries,
        "preflight": preflight,
        "adapter_flows": adapter_flow_records,
        "state": {
            "before": rel(run_root / "state" / "before.json"),
            "after": rel(run_root / "state" / "after.json"),
            "diff": rel(run_root / "state_diff.json"),
        },
        "snapshot": {
            "before": rel(run_root / "snapshot_before.json"),
            "after": rel(run_root / "snapshot_after.json"),
            "diff": rel(diff_path),
            "coverage": rel(coverage_path),
            "coverage_summary": state_diff.get("coverage", {}),
            "diff_summary": state_diff.get("summary", {}),
        },
        "attempts": attempts,
    }
    write_json(run_root / "execution_record.json", record)
    print(run_root)
    print(f"result={record['result']} stability={record['stability']} attempts={len(attempts)}")
    return 0 if str(record["result"]).upper() in PASS_RESULTS else 1


if __name__ == "__main__":
    raise SystemExit(main())
