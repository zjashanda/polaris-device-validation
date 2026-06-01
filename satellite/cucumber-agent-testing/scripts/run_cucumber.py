#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量 Cucumber/Gherkin 计划生成器。

本 runner 的默认目标是把 feature 文件转成 Agent Testing 执行计划，
并把所有调试产物写入 satellite/cucumber-agent-testing/debug/runs。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from polaris_env import load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_FEATURE = BDD_ROOT / "features" / "polaris_voice_core.feature"
DEFAULT_MAPPING = BDD_ROOT / "references" / "voice_core_mapping.json"
DEFAULT_DEBUG_ROOT = BDD_ROOT / "debug"
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.capabilities import infer_from_env_file, infer_from_project_name  # noqa: E402
from runtime.replay import build_replay_package  # noqa: E402
from runtime.snapshots import (  # noqa: E402
    build_snapshot,
    diff_snapshots,
    render_snapshot_report,
    write_coverage as write_snapshot_coverage,
    write_diff as write_snapshot_diff,
    write_snapshot,
)

STEP_PREFIXES = (
    "假如 ",
    "当 ",
    "那么 ",
    "而且 ",
    "并且 ",
    "Given ",
    "When ",
    "Then ",
    "And ",
    "But ",
)
RUNTIME_PROFILE_BY_SCENARIO = {
    "first_wake": "first_wake",
    "recognition_mode_wake": "recognition_mode_wake",
    "half_duplex_recognition": "half_duplex_recognition",
    "full_duplex_recognition": "full_duplex_recognition",
    "basic_command_recognition": "basic_command",
    "requirement_command_smoke": "basic_command",
    "requirement_free_speech_smoke": "command_batch_exploratory",
    "interrupt_prerequisite_measurement": "interrupt_prerequisite_measurement",
    "wake_interrupt": "wake_interrupt",
    "command_interrupt": "command_interrupt",
    "network_recovery_basic": "network_recovery_basic",
    "offline_oneshot_matrix": "offline_oneshot_matrix",
    "online_oneshot_matrix": "online_oneshot_matrix",
    "wake_latency_smoke": "wake_matrix",
    "continuous_wake_smoke": "wake_matrix",
    "random_interval_wake_smoke": "wake_matrix",
    "online_vad_special_smoke": "online_vad_special",
    "false_wake_quiet_basic": "false_wake_quiet",
    "false_wake_human_speech_smoke": "false_wake_playback",
    "false_wake_white_noise_smoke": "false_wake_playback",
    "attribution_validator_smoke": "attribution_validator",
}


@dataclass
class ParsedScenario:
    name: str
    tags: List[str]
    steps: List[str]
    line: int


@dataclass
class CommandPlan:
    name: str
    cmd: List[str]
    cmdline: str


@dataclass
class ScenarioPlan:
    scenario_id: str
    scenario_name: str
    tags: List[str]
    feature_steps: List[str]
    mapping_title: str
    source_test_item: str
    validation_module: str
    agent_goal: str
    preconditions: List[str]
    commands: List[CommandPlan]
    assertions: List[Dict[str, str]]
    failure_split: List[str]


@dataclass
class ManagedSession:
    session_dir: Path
    logger_pid: int
    logger_log: Path
    previous_result_marker: Optional[str]
    previous_logger_marker: Optional[str]
    heartbeat: Dict[str, Any]


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


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


def marker_path(name: str) -> Path:
    return WORKSPACE_ROOT / name


def read_marker(name: str) -> Optional[str]:
    path = marker_path(name)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace").strip()


def write_marker(name: str, value: str) -> None:
    marker_path(name).write_text(value, encoding="utf-8")


def restore_marker(name: str, previous: Optional[str]) -> None:
    path = marker_path(name)
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(previous, encoding="utf-8")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def snapshot_task_payload(plans: List[ScenarioPlan], args: argparse.Namespace) -> Dict[str, Any]:
    scenario_ids = [plan.scenario_id for plan in plans]
    tags = sorted({tag for plan in plans for tag in plan.tags})
    return {
        "schema": "polaris.cucumber.run.v1",
        "task_id": first_non_empty(args.tag, "cucumber_run"),
        "scenario": {"tag": args.tag, "scenario_ids": scenario_ids, "tags": tags},
        "expected_state": {
            "scenario_ids": scenario_ids,
            "runtime_profiles": [runtime_profile_for_plan(plan) for plan in plans if runtime_profile_for_plan(plan)],
        },
    }


def env_for_snapshot(args: argparse.Namespace) -> Tuple[Path, Dict[str, Any]]:
    env_path = resolve_env_path(getattr(args, "env_file", ""), WORKSPACE_ROOT)
    return env_path, load_env_payload(env_path)


def capture_cucumber_snapshot(
    run_dir: Path,
    args: argparse.Namespace,
    plans: List[ScenarioPlan],
    phase: str,
    *,
    evidence_roots: Optional[List[Path]] = None,
    extra_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    env_path, env_payload = env_for_snapshot(args)
    return build_snapshot(
        env_path=env_path,
        env_payload=env_payload,
        task=snapshot_task_payload(plans, args),
        phase=phase,
        run_dir=run_dir,
        evidence_roots=evidence_roots or [run_dir],
        scope={"runner": "run_cucumber", "mode": args.mode, "tag": args.tag},
        extra_state=extra_state,
    )


def write_cucumber_before_snapshot(run_dir: Path, args: argparse.Namespace, plans: List[ScenarioPlan]) -> Dict[str, Any]:
    snapshot = capture_cucumber_snapshot(run_dir, args, plans, "before", evidence_roots=[run_dir])
    write_snapshot(run_dir, snapshot, file_name="snapshot_before.json")
    return snapshot


def finalize_cucumber_snapshots(
    run_dir: Path,
    args: argparse.Namespace,
    plans: List[ScenarioPlan],
    before_snapshot: Optional[Dict[str, Any]],
    *,
    run_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    before = before_snapshot or read_json_safe(run_dir / "snapshot_before.json") or capture_cucumber_snapshot(run_dir, args, plans, "before_reconstructed", evidence_roots=[run_dir])
    after = capture_cucumber_snapshot(run_dir, args, plans, "after", evidence_roots=[run_dir], extra_state={"run_summary": run_summary or {}})
    write_snapshot(run_dir, before, file_name="snapshot_before.json")
    write_snapshot(run_dir, after, file_name="snapshot_after.json")
    diff = diff_snapshots(before, after)
    diff_path = write_snapshot_diff(run_dir, diff)
    coverage_path = write_snapshot_coverage(run_dir, diff.get("coverage", {}))
    return {
        "before": rel(run_dir / "snapshot_before.json"),
        "after": rel(run_dir / "snapshot_after.json"),
        "diff": rel(diff_path),
        "coverage": rel(coverage_path),
        "coverage_summary": diff.get("coverage", {}),
        "diff_summary": diff.get("summary", {}),
    }


def parse_feature(path: Path) -> Dict[str, Any]:
    feature_name = ""
    background_steps: List[str] = []
    scenarios: List[ParsedScenario] = []
    pending_tags: List[str] = []
    current: Optional[ParsedScenario] = None
    in_background = False

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            pending_tags = [part.strip() for part in line.split() if part.strip()]
            continue
        if line.startswith("功能:"):
            feature_name = line.split(":", 1)[1].strip()
            continue
        if line.startswith("背景:"):
            in_background = True
            current = None
            continue
        if line.startswith("场景:") or line.startswith("Scenario:"):
            name = line.split(":", 1)[1].strip()
            current = ParsedScenario(name=name, tags=pending_tags, steps=[], line=line_no)
            pending_tags = []
            scenarios.append(current)
            in_background = False
            continue
        if line.startswith(STEP_PREFIXES):
            if current is not None:
                current.steps.append(line)
            elif in_background:
                background_steps.append(line)

    return {
        "feature": feature_name,
        "background_steps": background_steps,
        "scenarios": scenarios,
    }


def scenario_id_from_tags(tags: Iterable[str], mapping: Dict[str, Any]) -> Optional[str]:
    scenario_map = mapping.get("scenarios", {})
    clean_tags = {tag.lstrip("@") for tag in tags}
    for key in scenario_map:
        if key in clean_tags:
            return key
    return None


def quote_cmd(args: List[str]) -> str:
    quoted: List[str] = []
    for arg in args:
        if not arg:
            quoted.append('""')
        elif any(ch.isspace() for ch in arg) or any(ch in arg for ch in ['"', "'", "&"]):
            quoted.append('"' + arg.replace('"', '\\"') + '"')
        else:
            quoted.append(arg)
    return " ".join(quoted)


def resolve_context(args: argparse.Namespace, mapping: Dict[str, Any], run_dir: Path) -> Dict[str, str]:
    defaults = mapping.get("defaults", {})
    env_payload: Dict[str, Any] = {}
    env_path = resolve_env_path(getattr(args, "env_file", ""), WORKSPACE_ROOT)
    env_payload = load_env_payload(env_path)
    device_key = first_non_empty(
        args.device_key,
        defaults.get("device_key", ""),
        env_payload.get("default_playback_device_key", ""),
        nested(env_payload, "audio", "default_playback_device_key"),
    )
    wake_word = first_non_empty(
        args.wake_word,
        defaults.get("wake_word", ""),
        env_payload.get("current_wakeup_word", ""),
        nested(env_payload, "device", "wake_word"),
        "小美小美",
    )
    command_file = first_non_empty(
        args.command_file,
        defaults.get("command_file", ""),
        nested(env_payload, "paths", "command_file"),
        "docs/fa2命令词.txt",
    )
    observe_ms = first_non_empty(args.observe_ms, defaults.get("observe_ms", ""), nested(env_payload, "timeouts", "observe_ms"), "15000")
    command_limit = first_non_empty(args.command_limit, defaults.get("command_limit", ""), nested(env_payload, "limits", "command_limit"), "20")
    wifi_ssid = first_non_empty(
        getattr(args, "wifi_ssid", ""),
        defaults.get("wifi_ssid", ""),
        env_payload.get("current_connected_ssid", ""),
        nested(env_payload, "network", "wifi_ssid"),
        os.environ.get("POLARIS_LOCAL_HOTSPOT_SSID", ""),
    )
    wifi_password = first_non_empty(
        getattr(args, "wifi_password", ""),
        defaults.get("wifi_password", ""),
        env_payload.get("wifi_password", ""),
        nested(env_payload, "network", "wifi_password"),
        os.environ.get("POLARIS_LOCAL_HOTSPOT_PASSWORD", ""),
    )
    return {
        "python": sys.executable,
        "root": str(WORKSPACE_ROOT),
        "debug_dir": str(run_dir),
        "env_file": str(env_path),
        "wake_word": wake_word,
        "command_text": args.command_text or str(defaults.get("command_text", "打开空调")),
        "command_file": command_file,
        "device_key": device_key,
        "wifi_ssid": wifi_ssid,
        "wifi_password": wifi_password,
        "observe_ms": observe_ms,
        "command_limit": command_limit,
        "ap_port": first_non_empty(nested(env_payload, "serial", "ports", "ap"), nested(env_payload, "ports", "ap")),
        "cp_port": first_non_empty(nested(env_payload, "serial", "ports", "cp"), nested(env_payload, "ports", "cp")),
        "asr_port": first_non_empty(nested(env_payload, "serial", "ports", "asr"), nested(env_payload, "ports", "asr")),
        "control_port": first_non_empty(nested(env_payload, "serial", "ports", "control"), nested(env_payload, "ports", "control")),
        "baudrate": first_non_empty(nested(env_payload, "serial", "baudrate"), env_payload.get("baudrate", ""), "115200"),
    }


def fill_placeholders(value: str, context: Dict[str, str]) -> str:
    result = value
    for key, item in context.items():
        result = result.replace("{" + key + "}", item)
    return result


OPTIONAL_VALUE_OPTIONS = {"--device-key", "--left-device-key", "--right-device-key"}


def drop_empty_optional_values(cmd: List[str]) -> List[str]:
    cleaned: List[str] = []
    index = 0
    while index < len(cmd):
        item = cmd[index]
        if item in OPTIONAL_VALUE_OPTIONS and index + 1 < len(cmd) and not str(cmd[index + 1]).strip():
            index += 2
            continue
        cleaned.append(item)
        index += 1
    return cleaned


def build_command_plans(raw_commands: List[Dict[str, Any]], context: Dict[str, str]) -> List[CommandPlan]:
    plans: List[CommandPlan] = []
    for raw in raw_commands:
        cmd = drop_empty_optional_values([fill_placeholders(str(part), context) for part in raw.get("cmd", [])])
        plans.append(CommandPlan(name=str(raw.get("name", "command")), cmd=cmd, cmdline=quote_cmd(cmd)))
    return plans


def build_plans(parsed: Dict[str, Any], mapping: Dict[str, Any], context: Dict[str, str]) -> List[ScenarioPlan]:
    plans: List[ScenarioPlan] = []
    scenarios: List[ParsedScenario] = parsed["scenarios"]
    for scenario in scenarios:
        scenario_id = scenario_id_from_tags(scenario.tags, mapping)
        if not scenario_id:
            continue
        raw = mapping["scenarios"][scenario_id]
        plans.append(
            ScenarioPlan(
                scenario_id=scenario_id,
                scenario_name=scenario.name,
                tags=scenario.tags,
                feature_steps=scenario.steps,
                mapping_title=str(raw.get("title", scenario.name)),
                source_test_item=str(raw.get("source_test_item", "")),
                validation_module=str(raw.get("validation_module", "")),
                agent_goal=str(raw.get("agent_goal", "")),
                preconditions=[str(item) for item in raw.get("preconditions", [])],
                commands=build_command_plans(raw.get("commands", []), context),
                assertions=[dict(item) for item in raw.get("assertions", [])],
                failure_split=[str(item) for item in raw.get("failure_split", [])],
            )
        )
    return plans


def filter_plans(plans: List[ScenarioPlan], tag: str) -> List[ScenarioPlan]:
    if not tag:
        return plans
    normalized = tag if tag.startswith("@") else f"@{tag}"
    return [plan for plan in plans if normalized in plan.tags or normalized.lstrip("@") == plan.scenario_id]


def render_markdown(run_dir: Path, parsed: Dict[str, Any], mode: str, plans: List[ScenarioPlan]) -> str:
    lines = [
        "# Polaris Cucumber Agent Testing 执行计划",
        "",
        f"- 模式：`{mode}`",
        f"- Feature：`{parsed.get('feature', '')}`",
        f"- 调试目录：`{rel(run_dir)}`",
        f"- 场景数：`{len(plans)}`",
        "",
        "## 背景步骤",
        "",
    ]
    for step in parsed.get("background_steps", []):
        lines.append(f"- {step}")
    lines.extend(["", "## 场景计划", ""])

    for index, plan in enumerate(plans, start=1):
        lines.extend(
            [
                f"### {index}. {plan.scenario_name}",
                "",
                f"- 场景 ID：`{plan.scenario_id}`",
                f"- 测试项：`{plan.source_test_item}`",
                f"- 验证模块：`{plan.validation_module}`",
                f"- Agent 目标：{plan.agent_goal}",
                "",
                "#### Feature 步骤",
            ]
        )
        for step in plan.feature_steps:
            lines.append(f"- {step}")
        lines.extend(["", "#### 前置条件"])
        for item in plan.preconditions:
            lines.append(f"- {item}")
        lines.extend(["", "#### 命令计划"])
        for command in plan.commands:
            lines.append(f"- `{command.name}`：`{command.cmdline}`")
        lines.extend(["", "#### 断言"])
        for assertion in plan.assertions:
            lines.append(
                f"- `{assertion.get('name', '')}` expected=`{assertion.get('expected', '')}` owner=`{assertion.get('owner', '')}`"
            )
        lines.extend(["", "#### 失败归因"])
        for item in plan.failure_split:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def execute_plans(plans: List[ScenarioPlan], run_dir: Path, allow_side_effects: bool) -> List[Dict[str, Any]]:
    if not allow_side_effects:
        raise RuntimeError("execute 模式需要显式增加 --allow-side-effects，避免误占用串口/播放设备/云端。")

    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    execution_plan = load_json(run_dir / "execution_plan.json")
    plan_context = execution_plan.get("context", {}) if isinstance(execution_plan.get("context"), dict) else {}
    env_file = str(plan_context.get("env_file", "") or "")
    results: List[Dict[str, Any]] = []
    for plan in plans:
        for command in plan.commands:
            log_path = logs_dir / f"{len(results) + 1:02d}_{plan.scenario_id}_{command.name}.log"
            started_at = datetime.now().isoformat(timespec="seconds")
            with log_path.open("w", encoding="utf-8", newline="") as log:
                log.write(f"$ {command.cmdline}\n")
                log.flush()
                proc = subprocess.Popen(
                    command.cmd,
                    cwd=str(WORKSPACE_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env={**os.environ, "POLARIS_BDD_RUN_DIR": str(run_dir), "POLARIS_ENV_FILE": env_file, "PYTHONIOENCODING": "utf-8"},
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                returncode = proc.wait()
            results.append(
                {
                    "scenario_id": plan.scenario_id,
                    "command": command.name,
                    "returncode": returncode,
                    "log_path": rel(log_path),
                    "started_at": started_at,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            if returncode != 0:
                break
    return results


def wait_for_managed_heartbeat(session_dir: Path, timeout_s: float = 25.0) -> Dict[str, Any]:
    heartbeat = session_dir / "logs" / "live" / "heartbeat.json"
    deadline = time.time() + timeout_s
    last_payload: Dict[str, Any] = {}
    while time.time() < deadline:
        if heartbeat.exists():
            try:
                last_payload = load_json(heartbeat)
                ports = last_payload.get("ports", {})
                if ports and all(bool(item.get("is_open")) for item in ports.values()):
                    return last_payload
            except Exception:
                pass
        time.sleep(0.5)
    if last_payload:
        return last_payload
    raise RuntimeError(f"managed logger heartbeat not ready: {heartbeat}")


def start_managed_session(run_dir: Path, context: Optional[Dict[str, Any]] = None) -> tuple[ManagedSession, subprocess.Popen[str]]:
    session_dir = run_dir / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    previous_result = read_marker(".current_result_dir")
    previous_logger = read_marker(".current_logger_pid")
    write_marker(".current_result_dir", str(session_dir))

    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger_log = logs_dir / "managed_serial_logger.log"
    cmd = [
        sys.executable,
        "tools/device/polaris_serial_harness.py",
        "start",
        "--session-dir",
        str(session_dir),
        "--no-sync-config",
    ]
    context = context or {}
    baudrate = str(context.get("baudrate", "") or "").strip()
    if baudrate:
        cmd.extend(["--baudrate", baudrate])
    for key, option in (("ap_port", "--ap-port"), ("cp_port", "--cp-port"), ("asr_port", "--asr-port")):
        value = str(context.get(key, "") or "").strip()
        if value:
            cmd.extend([option, value])
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_handle = logger_log.open("w", encoding="utf-8", newline="")
    log_handle.write(f"$ {quote_cmd(cmd)}\n")
    log_handle.flush()
    proc = subprocess.Popen(
        cmd,
        cwd=str(WORKSPACE_ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    # Let the process start before checking heartbeat; fail fast if it exits.
    time.sleep(1.0)
    if proc.poll() is not None:
        log_handle.close()
        restore_marker(".current_result_dir", previous_result)
        raise RuntimeError(f"managed serial logger exited early, rc={proc.returncode}, log={logger_log}")
    write_marker(".current_logger_pid", str(proc.pid))
    heartbeat = wait_for_managed_heartbeat(session_dir)
    managed = ManagedSession(
        session_dir=session_dir,
        logger_pid=proc.pid,
        logger_log=logger_log,
        previous_result_marker=previous_result,
        previous_logger_marker=previous_logger,
        heartbeat=heartbeat,
    )
    # Keep the handle attached to the process object so Windows can continue writing.
    proc._polaris_log_handle = log_handle  # type: ignore[attr-defined]
    return managed, proc


def stop_managed_session(managed: ManagedSession, proc: subprocess.Popen[str]) -> None:
    try:
        subprocess.run(
            [
                sys.executable,
                "tools/device/polaris_serial_harness.py",
                "stop",
                "--session-dir",
                str(managed.session_dir),
            ],
            cwd=str(WORKSPACE_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    finally:
        handle = getattr(proc, "_polaris_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        restore_marker(".current_result_dir", managed.previous_result_marker)
        restore_marker(".current_logger_pid", managed.previous_logger_marker)


def managed_session_payload(managed: Optional[ManagedSession]) -> Optional[Dict[str, Any]]:
    if managed is None:
        return None
    return {
        "session_dir": rel(managed.session_dir),
        "logger_pid": managed.logger_pid,
        "logger_log": rel(managed.logger_log),
        "previous_result_marker": managed.previous_result_marker,
        "previous_logger_marker": managed.previous_logger_marker,
        "heartbeat": managed.heartbeat,
    }


def read_json_safe(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def latest_path(paths: Iterable[Path]) -> Optional[Path]:
    items = [path for path in paths if path.exists()]
    if not items:
        return None
    return max(items, key=lambda path: path.stat().st_mtime)


def int_metric(metrics: Dict[str, Any], name: str) -> int:
    value = metrics.get(name, 0)
    try:
        return int(value)
    except Exception:
        return 0


def extract_case_id(plan: ScenarioPlan) -> str:
    for command in plan.commands:
        for index, item in enumerate(command.cmd):
            if item == "--case-id" and index + 1 < len(command.cmd):
                return command.cmd[index + 1]
    return ""


def compact_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    names = [
        "cp_wake_count",
        "ap_wake_count",
        "wb_wake_count",
        "asr_total",
        "cp_command_count",
        "unique_command_keyword_count",
        "ap_cloud_tts_play_count",
        "ap_cloud_tts_recv_count",
        "ap_instruction_broadcast_count",
        "wake_during_playback_count",
        "interrupt_reset_count",
        "tone_ids",
    ]
    return {name: metrics.get(name) for name in names if name in metrics}


def summarize_first_wake(run_dir: Path) -> Dict[str, Any]:
    summary_path = latest_path(
        run_dir.glob("session/artifacts/probe/phrase/*bdd_first_wake*/probe_summary.json")
    ) or latest_path(run_dir.glob("session/artifacts/probe/phrase/*/probe_summary.json"))
    if summary_path is None:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": "未找到首次唤醒 probe_summary.json，无法判定。",
            "evidence_path": "",
            "metrics": {},
        }

    payload = read_json_safe(summary_path) or {}
    aggregate: Dict[str, Any] = {}
    playback_returncodes: List[int] = []
    total_lines = 0
    for step in payload.get("steps", []):
        playback = step.get("playback", {})
        if "returncode" in playback:
            try:
                playback_returncodes.append(int(playback.get("returncode")))
            except Exception:
                playback_returncodes.append(-1)
        metrics = step.get("metrics", {})
        for name in [
            "cp_wake_count",
            "ap_wake_count",
            "wb_wake_count",
            "wb_online_wake_count",
            "asr_total",
            "cp_command_count",
            "unique_command_keyword_count",
        ]:
            aggregate[name] = int_metric(aggregate, name) + int_metric(metrics, name)
        tones = aggregate.setdefault("tone_ids", [])
        for tone_id in metrics.get("tone_ids", []):
            if tone_id not in tones:
                tones.append(tone_id)
        line_counts = step.get("window_summary", {}).get("line_counts", {})
        for value in line_counts.values():
            try:
                total_lines += int(value)
            except Exception:
                pass

    aggregate["playback_returncodes"] = playback_returncodes
    aggregate["line_count"] = total_lines
    cp_wake = int_metric(aggregate, "cp_wake_count")
    ap_wake = int_metric(aggregate, "ap_wake_count")
    asr_wake = int_metric(aggregate, "wb_wake_count") + int_metric(aggregate, "wb_online_wake_count")
    aggregate["asr_wake_count"] = asr_wake
    playback_ok = bool(playback_returncodes) and all(code == 0 for code in playback_returncodes)
    plan_payload = read_json_safe(run_dir / "execution_plan.json") or {}
    context = plan_payload.get("context", {}) if isinstance(plan_payload.get("context"), dict) else {}
    managed_payload = plan_payload.get("managed_session", {}) if isinstance(plan_payload.get("managed_session"), dict) else {}
    heartbeat = managed_payload.get("heartbeat", {}) if isinstance(managed_payload.get("heartbeat"), dict) else {}
    heartbeat_ports = heartbeat.get("ports", {}) if isinstance(heartbeat.get("ports"), dict) else {}
    serial_open_errors: List[Dict[str, Any]] = []
    for port, info in heartbeat_ports.items():
        if not isinstance(info, dict):
            continue
        if info.get("is_open") is False or info.get("last_error"):
            serial_open_errors.append(
                {
                    "port": port,
                    "role": info.get("role", ""),
                    "last_error": info.get("last_error", ""),
                }
            )
    cp_required = bool(str(context.get("cp_port", "") or "").strip())
    try:
        _project, capabilities = runtime_capabilities_from_run(run_dir)
        if capabilities.get("cp_log") is False:
            cp_required = False
    except Exception:
        pass
    asr_required = bool(str(context.get("asr_port", "") or "").strip())
    missing_sources: List[str] = []
    if cp_required and cp_wake < 1:
        missing_sources.append("CP")
    if ap_wake < 1:
        missing_sources.append("AP")
    if asr_required and asr_wake < 1:
        missing_sources.append("ASR")

    if not playback_ok:
        result = "BLOCKED"
        attribution = "audio_playback_or_device_key"
        reason = f"播放链路未通过，returncode={playback_returncodes}。"
    elif serial_open_errors:
        result = "BLOCKED"
        attribution = "serial_logger_or_ports"
        reason = f"串口日志采集存在端口打开失败，不能把证据缺失归因为固件失败：{serial_open_errors}。"
    elif total_lines == 0:
        result = "BLOCKED"
        attribution = "serial_logger_or_ports"
        reason = "播放成功但串口窗口无日志，优先检查 logger/串口。"
    elif not missing_sources:
        result = "PASS"
        attribution = "pass"
        expected = ["AP"]
        if cp_required:
            expected.insert(0, "CP")
        if asr_required:
            expected.append("ASR")
        reason = f"播放成功，{'/'.join(expected)} 均观察到唤醒闭环。"
    else:
        result = "FAIL"
        attribution = "firmware_device_or_audio_path"
        reason = f"播放成功但唤醒证据不完整：CP={cp_wake}, AP={ap_wake}, ASR={asr_wake}，缺失={','.join(missing_sources)}。"

    return {
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "evidence_path": rel(summary_path),
        "metrics": aggregate,
    }


def classify_doc_case_attribution(result: str, reason: str) -> str:
    if result == "PASS":
        return "pass"
    if "code\":\"501" in reason or "code': '501" in reason or "设备离线" in reason:
        return "cloud_or_device_online_precondition"
    if "APP/cloud" in reason or "在线语音开关" in reason:
        return "cloud_or_config_precondition"
    if result == "BLOCKED":
        return "precondition_or_test_environment"
    return "firmware_device_or_requirement"


def summarize_doc_case(run_dir: Path, plan: ScenarioPlan) -> Dict[str, Any]:
    case_id = extract_case_id(plan)
    matched: List[tuple[Path, Dict[str, Any]]] = []
    for judge_path in run_dir.glob("session/artifacts/doc_cases/runs/*/judge.json"):
        payload = read_json_safe(judge_path)
        if not payload:
            continue
        if not case_id or str(payload.get("case_id", "")) == case_id:
            matched.append((judge_path, payload))

    if not matched:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": f"未找到 doc case judge.json，case_id={case_id or 'unknown'}。",
            "evidence_path": "",
            "metrics": {},
        }

    judge_path, payload = max(matched, key=lambda item: item[0].stat().st_mtime)
    result = str(payload.get("result", "UNKNOWN") or "UNKNOWN")
    reason = str(payload.get("reason", ""))
    metrics = compact_metrics(payload.get("metrics", {}))
    return {
        "result": result,
        "attribution": classify_doc_case_attribution(result, reason),
        "reason": reason or f"doc case {case_id} result={result}",
        "evidence_path": rel(judge_path),
        "metrics": metrics,
        "case_id": case_id,
    }


def extract_option_value(plan: ScenarioPlan, option: str) -> str:
    for command in plan.commands:
        for index, item in enumerate(command.cmd):
            if item == option and index + 1 < len(command.cmd):
                return command.cmd[index + 1]
    return ""


def summarize_basic_command(run_dir: Path, label_hint: str = "", exploratory: bool = False) -> Dict[str, Any]:
    if label_hint:
        summary_path = latest_path(
            run_dir.glob(f"session/artifacts/misc/fa2/*{label_hint}*/fa2_command_batch_summary.json")
        )
    else:
        summary_path = None
    summary_path = summary_path or latest_path(
        run_dir.glob("session/artifacts/misc/fa2/*bdd_basic_command*/fa2_command_batch_summary.json")
    ) or latest_path(run_dir.glob("session/artifacts/misc/fa2/*/fa2_command_batch_summary.json"))
    if summary_path is None:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": "未找到 fa2_command_batch_summary.json，无法判定基础命令词。",
            "evidence_path": "",
            "metrics": {},
        }

    payload = read_json_safe(summary_path) or {}
    counts = dict(payload.get("counts", {}))
    total = int_metric(payload, "total")
    playback_returncode = payload.get("playback_returncode")
    pass_count = int(counts.get("PASS", 0) or 0)
    fail_count = int(counts.get("FAIL", 0) or 0)
    blocked_count = int(counts.get("BLOCKED", 0) or 0)
    metrics = {
        "total": total,
        "counts": counts,
        "playback_returncode": playback_returncode,
        "audio_duration_ms": payload.get("audio_duration_ms"),
        "command_file": payload.get("command_file"),
        "label_hint": label_hint,
        "exploratory": exploratory,
    }
    subject = "探索性自由说语料" if exploratory else ("需求命令词" if label_hint and label_hint != "bdd_basic_command" else "基础命令词")
    if playback_returncode != 0:
        result = "BLOCKED"
        attribution = "audio_playback_or_device_key"
        reason = f"{subject}批量音频播放失败，returncode={playback_returncode}。"
    elif total > 0 and pass_count == total and fail_count == 0 and blocked_count == 0:
        result = "PASS"
        attribution = "pass"
        reason = f"{total} 条{subject}均有唤醒与识别闭环证据。"
    elif blocked_count > 0 and fail_count == 0:
        result = "BLOCKED"
        attribution = "partial_precondition_or_environment"
        reason = f"{subject}存在阻塞项：{counts}。"
    else:
        result = "FAIL"
        attribution = "firmware_asr_or_command_mapping"
        reason = f"{subject}存在失败项：{counts}。"

    return {
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "evidence_path": rel(summary_path),
        "metrics": metrics,
    }


def summarize_interrupt_prerequisite(run_dir: Path) -> Dict[str, Any]:
    measurement_path = latest_path(
        [
            run_dir / "interrupt_measurement" / "interrupt_prerequisite_measurement.json",
            *run_dir.glob("**/interrupt_prerequisite_measurement.json"),
        ]
    )
    if measurement_path is None:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": "未找到 interrupt_prerequisite_measurement.json，无法判定打断前置测量。",
            "evidence_path": "",
            "metrics": {},
        }
    payload = read_json_safe(measurement_path) or {}
    counts = dict(payload.get("counts", {}))
    selected = payload.get("selected") or {}
    metrics = {
        "counts": counts,
        "total": payload.get("total", 0),
        "selected_phrase": selected.get("phrase", ""),
        "selected_duration_ms": selected.get("self_play_duration_ms", 0),
        "selected_injection_offset_ms": selected.get("injection_offset_ms", ""),
        "minimum_duration_ms": payload.get("minimum_duration_ms"),
    }
    if payload.get("status") == "BLOCKED":
        result = "BLOCKED"
        attribution = payload.get("attribution", "fa2_artifact_missing")
        reason = str(payload.get("reason", "打断前置测量阻塞。"))
    elif selected:
        result = "PASS"
        attribution = "pass"
        reason = (
            f"已选出打断前置 `{selected.get('phrase')}`，"
            f"自播 {selected.get('self_play_duration_ms')}ms，"
            f"建议 +{selected.get('injection_offset_ms')}ms 注入。"
        )
    elif counts.get("NEEDS_REVIEW", 0) or counts.get("UNUSABLE", 0):
        result = "BLOCKED"
        attribution = "no_usable_interrupt_prerequisite"
        reason = f"候选已测量但未选出可用自播前置：{counts}。"
    else:
        result = "BLOCKED"
        attribution = "measurement_unknown"
        reason = f"打断前置测量无可用候选：{counts}。"
    return {
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "evidence_path": rel(measurement_path),
        "metrics": metrics,
    }


def summarize_interrupt_injection(run_dir: Path) -> Dict[str, Any]:
    result_path = latest_path(
        [
            run_dir / "interrupt_execution" / "interrupt_injection_result.json",
            *run_dir.glob("**/interrupt_injection_result.json"),
        ]
    )
    if result_path is None:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": "未找到 interrupt_injection_result.json，无法判定打断注入结果。",
            "evidence_path": "",
            "metrics": {},
        }
    payload = read_json_safe(result_path) or {}
    raw_result = str(payload.get("result", "BLOCKED") or "BLOCKED")
    result = "BLOCKED" if raw_result == "TIMING_AMBIGUOUS" else raw_result
    evidence = payload.get("evidence", {})
    timing = payload.get("timing", {})
    metrics = {
        "raw_result": raw_result,
        "cp_wake_after_injection": evidence.get("cp_wake_after_injection", 0),
        "ap_wake_after_injection": evidence.get("ap_wake_after_injection", 0),
        "asr_wake_after_injection": evidence.get("asr_wake_after_injection", 0),
        "asr_texts_after_injection": evidence.get("asr_texts_after_injection", []),
        "cp_command_keywords_after_injection": evidence.get("cp_command_keywords_after_injection", []),
        "containing_self_play_windows": len(timing.get("containing_self_play_windows", [])),
        "planned_injection_start": timing.get("planned_injection_start", ""),
    }
    return {
        "result": result,
        "attribution": payload.get("attribution", "unknown"),
        "reason": str(payload.get("reason", "")),
        "evidence_path": rel(result_path),
        "metrics": metrics,
    }


def summarize_network_recovery(run_dir: Path) -> Dict[str, Any]:
    summary_path = latest_path(
        [
            run_dir / "network_recovery" / "network_recovery_summary.json",
            *run_dir.glob("**/network_recovery_summary.json"),
        ]
    )
    if summary_path is None:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": "未找到 network_recovery_summary.json，无法判定联网恢复。",
            "evidence_path": "",
            "metrics": {},
        }
    payload = read_json_safe(summary_path) or {}
    metrics = {
        **dict(payload.get("checks", {})),
        **dict(payload.get("metrics", {})),
    }
    return {
        "result": str(payload.get("result", "BLOCKED") or "BLOCKED"),
        "attribution": payload.get("attribution", "unknown"),
        "reason": str(payload.get("reason", "")),
        "evidence_path": rel(summary_path),
        "metrics": metrics,
    }


def summarize_oneshot_matrix(run_dir: Path) -> Dict[str, Any]:
    summary_path = latest_path(
        [
            run_dir / "oneshot_matrix" / "oneshot_matrix_summary.json",
            *run_dir.glob("**/oneshot_matrix_summary.json"),
        ]
    )
    if summary_path is None:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": "未找到 oneshot_matrix_summary.json，无法判定 one-shot 矩阵。",
            "evidence_path": "",
            "metrics": {},
        }
    payload = read_json_safe(summary_path) or {}
    metrics = {
        "counts": payload.get("counts", {}),
        "intervals": payload.get("intervals", []),
        "command_text": payload.get("command_text", ""),
    }
    return {
        "result": str(payload.get("result", "BLOCKED") or "BLOCKED"),
        "attribution": payload.get("attribution", "unknown"),
        "reason": str(payload.get("reason", "")),
        "evidence_path": rel(summary_path),
        "metrics": metrics,
    }


def summarize_false_wake_quiet(run_dir: Path) -> Dict[str, Any]:
    summary_path = latest_path(
        [
            run_dir / "false_wake_quiet" / "false_wake_quiet_summary.json",
            *run_dir.glob("**/false_wake_quiet_summary.json"),
        ]
    )
    if summary_path is None:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": "未找到 false_wake_quiet_summary.json，无法判定静默误唤醒。",
            "evidence_path": "",
            "metrics": {},
        }
    payload = read_json_safe(summary_path) or {}
    return {
        "result": str(payload.get("result", "BLOCKED") or "BLOCKED"),
        "attribution": payload.get("attribution", "unknown"),
        "reason": str(payload.get("reason", "")),
        "evidence_path": rel(summary_path),
        "metrics": dict(payload.get("metrics", {})),
    }


def summarize_wake_matrix(run_dir: Path) -> Dict[str, Any]:
    summary_path = latest_path(
        [
            run_dir / "wake_matrix" / "wake_matrix_summary.json",
            *run_dir.glob("**/wake_matrix_summary.json"),
        ]
    )
    if summary_path is None:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": "未找到 wake_matrix_summary.json，无法判定唤醒矩阵。",
            "evidence_path": "",
            "metrics": {},
        }
    payload = read_json_safe(summary_path) or {}
    metrics = {
        "scenario": payload.get("scenario", ""),
        "counts": payload.get("counts", {}),
        "rate": payload.get("rate"),
        "latency": payload.get("latency", {}),
        "wake_audio_duration_ms": payload.get("wake_audio_duration_ms"),
    }
    return {
        "result": str(payload.get("result", "BLOCKED") or "BLOCKED"),
        "attribution": payload.get("attribution", "unknown"),
        "reason": str(payload.get("reason", "")),
        "evidence_path": rel(summary_path),
        "metrics": metrics,
    }


def summarize_wake_stress(run_dir: Path, scenario_key: str) -> Dict[str, Any]:
    summaries = sorted(run_dir.rglob("stress_summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not summaries:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": "未找到 stress_summary.json，无法判定识别模式唤醒。",
            "evidence_path": "",
            "metrics": {},
        }
    summary_path = summaries[0]
    payload = read_json_safe(summary_path) or {}
    scenario = (payload.get("scenarios") or {}).get(scenario_key, {}) if isinstance(payload.get("scenarios"), dict) else {}
    counts = scenario.get("counts", {}) if isinstance(scenario.get("counts"), dict) else {}
    counted_rounds = int(scenario.get("counted_rounds", 0) or 0)
    pass_count = int(scenario.get("pass", 0) or 0)
    fail_count = int(scenario.get("fail", 0) or 0)
    latest = [
        row for row in payload.get("latest_rounds", [])
        if isinstance(row, dict) and str(row.get("scenario_id", "")) == scenario_key
    ]
    first_non_pass = next((row for row in latest if row.get("result") != "PASS"), {})
    if counted_rounds > 0 and pass_count == counted_rounds and fail_count == 0:
        result = "PASS"
        attribution = "pass"
        reason = f"{scenario_key} counted_rounds={counted_rounds}, pass={pass_count}。"
    elif any(key in counts for key in ("BLOCKED", "PRECONDITION_FAIL", "OUT_OF_WINDOW", "TIMING_AMBIGUOUS")) and fail_count == 0:
        result = "BLOCKED"
        attribution = str(first_non_pass.get("attribution") or "precondition_or_timing")
        reason = str(first_non_pass.get("reason") or f"{scenario_key} 未形成可计入成功率的 PASS 轮。")
    else:
        result = "FAIL"
        attribution = str(first_non_pass.get("attribution") or "firmware_device_or_audio_path")
        reason = str(first_non_pass.get("reason") or f"{scenario_key} fail={fail_count}, counts={counts}。")
    return {
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "evidence_path": rel(summary_path),
        "metrics": {
            "scenario": scenario,
            "cp_log_required": payload.get("cp_log_required"),
            "round_total": payload.get("round_total"),
        },
    }


def summarize_named_summary(run_dir: Path, filename: str, missing_reason: str, metric_keys: List[str]) -> Dict[str, Any]:
    summary_path = latest_path([run_dir / filename, *run_dir.glob(f"**/{filename}")])
    if summary_path is None:
        return {
            "result": "BLOCKED",
            "attribution": "test_artifact_missing",
            "reason": missing_reason,
            "evidence_path": "",
            "metrics": {},
        }
    payload = read_json_safe(summary_path) or {}
    metrics = {key: payload.get(key) for key in metric_keys if key in payload}
    return {
        "result": str(payload.get("result", "BLOCKED") or "BLOCKED"),
        "attribution": payload.get("attribution", "unknown"),
        "reason": str(payload.get("reason", "")),
        "evidence_path": rel(summary_path),
        "metrics": metrics,
    }


def summarize_scenario(run_dir: Path, plan: ScenarioPlan) -> Dict[str, Any]:
    if plan.scenario_id == "first_wake":
        item = summarize_first_wake(run_dir)
    elif plan.scenario_id == "recognition_mode_wake":
        item = summarize_wake_stress(run_dir, "recognition_mode_wake_rate")
    elif plan.scenario_id in {"basic_command_recognition", "requirement_command_smoke", "requirement_free_speech_smoke"}:
        label_hint = extract_option_value(plan, "--label")
        item = summarize_basic_command(
            run_dir,
            label_hint=label_hint,
            exploratory=plan.scenario_id == "requirement_free_speech_smoke",
        )
    elif plan.scenario_id == "interrupt_prerequisite_measurement":
        item = summarize_interrupt_prerequisite(run_dir)
    elif plan.scenario_id in {"wake_interrupt", "command_interrupt"}:
        item = summarize_interrupt_injection(run_dir)
    elif plan.scenario_id == "network_recovery_basic":
        item = summarize_network_recovery(run_dir)
    elif plan.scenario_id in {"offline_oneshot_matrix", "online_oneshot_matrix"}:
        item = summarize_oneshot_matrix(run_dir)
    elif plan.scenario_id == "false_wake_quiet_basic":
        item = summarize_false_wake_quiet(run_dir)
    elif plan.scenario_id in {"wake_latency_smoke", "continuous_wake_smoke", "random_interval_wake_smoke"}:
        item = summarize_wake_matrix(run_dir)
    elif plan.scenario_id == "online_vad_special_smoke":
        item = summarize_named_summary(
            run_dir,
            "online_vad_special_summary.json",
            "未找到 online_vad_special_summary.json，无法判定在线 VAD 专项。",
            ["counts", "candidate_count", "needs_review_count"],
        )
    elif plan.scenario_id == "attribution_validator_smoke":
        item = summarize_named_summary(
            run_dir,
            "attribution_validator_summary.json",
            "未找到 attribution_validator_summary.json，无法判定归因复核。",
            ["run_count", "finding_count", "error_count", "warn_count"],
        )
    elif plan.scenario_id in {"false_wake_human_speech_smoke", "false_wake_white_noise_smoke"}:
        item = summarize_named_summary(
            run_dir,
            "false_wake_playback_summary.json",
            "未找到 false_wake_playback_summary.json，无法判定干扰误唤醒。",
            ["kind", "metrics"],
        )
    else:
        item = summarize_doc_case(run_dir, plan)
    item.update(
        {
            "scenario_id": plan.scenario_id,
            "scenario_name": plan.scenario_name,
            "mapping_title": plan.mapping_title,
            "validation_module": plan.validation_module,
        }
    )
    return item


def runtime_profile_for_plan(plan: ScenarioPlan) -> str:
    return RUNTIME_PROFILE_BY_SCENARIO.get(plan.scenario_id, "")


def execution_context_from_run(run_dir: Path) -> Dict[str, Any]:
    payload = read_json_safe(run_dir / "execution_plan.json") or {}
    context = payload.get("context", {})
    return context if isinstance(context, dict) else {}


def resolve_env_file_from_run(run_dir: Path) -> Path:
    context = execution_context_from_run(run_dir)
    env_file = str(context.get("env_file", "") or "")
    if env_file:
        path = Path(env_file)
        if not path.is_absolute():
            path = WORKSPACE_ROOT / path
        return path
    return resolve_env_path("", WORKSPACE_ROOT)


def runtime_capabilities_from_run(run_dir: Path) -> tuple[str, Dict[str, Any]]:
    context = execution_context_from_run(run_dir)
    env_file = resolve_env_file_from_run(run_dir)
    project = ""
    capabilities: Dict[str, Any] = {}
    try:
        if env_file.exists():
            project, capabilities = infer_from_env_file(env_file)
    except Exception:
        project, capabilities = "", infer_from_project_name("")
    cp_port = str(context.get("cp_port", "") or "").strip()
    asr_port = str(context.get("asr_port", "") or "").strip()
    if "cp_port" in context and capabilities.get("cp_log", True) is not False:
        capabilities["cp_log"] = bool(cp_port)
    if "asr_port" in context:
        capabilities["asr_log"] = bool(asr_port)
    return project, capabilities or infer_from_project_name(project)


def build_runtime_sidecars(run_dir: Path, plans: List[ScenarioPlan]) -> Dict[str, Dict[str, Any]]:
    project, capabilities = runtime_capabilities_from_run(run_dir)
    sidecars: Dict[str, Dict[str, Any]] = {}
    for plan in plans:
        profile = runtime_profile_for_plan(plan)
        if not profile:
            continue
        out_dir = run_dir / "runtime_replay" / plan.scenario_id
        try:
            package = build_replay_package(
                input_dir=run_dir,
                out_dir=out_dir,
                profile=profile,
                project=project,
                capabilities=capabilities,
            )
            assertion_summary = dict(package.get("assertion_summary", {}))
            sidecars[plan.scenario_id] = {
                "profile": profile,
                "project": project,
                "capabilities": capabilities,
                "result": assertion_summary.get("result", "UNKNOWN"),
                "event_count": package.get("timeline", {}).get("event_count", 0),
                "event_counts": package.get("timeline", {}).get("event_counts", {}),
                "assertion_summary": assertion_summary,
                "replay_dir": rel(out_dir),
                "report_path": rel(out_dir / "runtime_replay_report.md"),
            }
        except Exception as exc:
            sidecars[plan.scenario_id] = {
                "profile": profile,
                "project": project,
                "capabilities": capabilities,
                "result": "ERROR",
                "event_count": 0,
                "event_counts": {},
                "error": str(exc),
                "replay_dir": rel(out_dir),
            }
    if sidecars:
        write_json(run_dir / "runtime_replay_summary.json", sidecars)
    return sidecars


def render_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_bdd_report(run_dir: Path, summary: Dict[str, Any]) -> str:
    lines = [
        "# Polaris Cucumber 真机执行报告",
        "",
        f"- 运行目录：`{rel(run_dir)}`",
        f"- 生成时间：`{datetime.now().isoformat(timespec='seconds')}`",
        f"- 模式：`{summary.get('mode', '')}`",
        f"- 场景数：`{summary.get('scenario_count', 0)}`",
        f"- 汇总：`{render_value(summary.get('overall_counts', {}))}`",
        "",
        "## 场景结论",
        "",
        "| 场景 | 结果 | Runtime | 归因 | 关键原因 | 证据 |",
        "|---|---|---|---|---|---|",
    ]
    for item in summary.get("scenario_results", []):
        reason = str(item.get("reason", "")).replace("\n", " ")
        if len(reason) > 120:
            reason = reason[:117] + "..."
        runtime = item.get("runtime_replay", {})
        runtime_text = ""
        if runtime:
            runtime_text = f"{runtime.get('result', 'UNKNOWN')} / {runtime.get('event_count', 0)} events"
        lines.append(
            "| {name} | `{result}` | `{runtime}` | `{attr}` | {reason} | `{path}` |".format(
                name=item.get("mapping_title") or item.get("scenario_name"),
                result=item.get("result", "UNKNOWN"),
                runtime=runtime_text,
                attr=item.get("attribution", ""),
                reason=reason.replace("|", "\\|"),
                path=item.get("evidence_path", ""),
            )
        )
    lines.extend(["", "## 关键指标", ""])
    for item in summary.get("scenario_results", []):
        lines.append(f"### {item.get('mapping_title') or item.get('scenario_name')}")
        metrics = item.get("metrics", {})
        if metrics:
            for name, value in metrics.items():
                lines.append(f"- `{name}`：`{render_value(value)}`")
        else:
            lines.append("- 无可用指标。")
        runtime = item.get("runtime_replay", {})
        if runtime:
            lines.append(f"- `runtime_result`：`{runtime.get('result', 'UNKNOWN')}`")
            lines.append(f"- `runtime_event_count`：`{runtime.get('event_count', 0)}`")
            lines.append(f"- `runtime_report`：`{runtime.get('report_path', '')}`")
        lines.append("")
    snapshots = summary.get("snapshots", {}) if isinstance(summary.get("snapshots"), dict) else {}
    if snapshots:
        lines.extend(render_snapshot_report(snapshots))
    lines.extend(
        [
            "## 判定说明",
            "",
            "- `PASS`：目标功能证据完整，命令 returncode 与业务断言均通过。",
            "- `FAIL`：前置与环境可用，但设备/固件/ASR/需求行为证据不满足期望。",
            "- `BLOCKED`：前置条件、云端配置、联网状态、播放设备或串口日志导致无法验证目标功能。",
        ]
    )
    return "\n".join(lines)


def scenario_plan_from_payload(payload: Dict[str, Any]) -> ScenarioPlan:
    commands = [
        CommandPlan(
            name=str(command.get("name", "command")),
            cmd=[str(part) for part in command.get("cmd", [])],
            cmdline=str(command.get("cmdline", "")),
        )
        for command in payload.get("commands", [])
    ]
    return ScenarioPlan(
        scenario_id=str(payload.get("scenario_id", "")),
        scenario_name=str(payload.get("scenario_name", payload.get("mapping_title", ""))),
        tags=[str(item) for item in payload.get("tags", [])],
        feature_steps=[str(item) for item in payload.get("feature_steps", [])],
        mapping_title=str(payload.get("mapping_title", payload.get("scenario_name", ""))),
        source_test_item=str(payload.get("source_test_item", "")),
        validation_module=str(payload.get("validation_module", "")),
        agent_goal=str(payload.get("agent_goal", "")),
        preconditions=[str(item) for item in payload.get("preconditions", [])],
        commands=commands,
        assertions=[dict(item) for item in payload.get("assertions", [])],
        failure_split=[str(item) for item in payload.get("failure_split", [])],
    )


def load_plans_from_run(run_dir: Path) -> List[ScenarioPlan]:
    payload = load_json(run_dir / "execution_plan.json")
    return [scenario_plan_from_payload(item) for item in payload.get("plans", [])]


def apply_runtime_strict_result(item: Dict[str, Any]) -> None:
    runtime = item.get("runtime_replay") or {}
    runtime_result = str(runtime.get("result", "") or "")
    if not runtime_result or runtime_result in {"PASS", "PASS_WITH_SKIPPED_TIMING"}:
        return
    original = str(item.get("result", "UNKNOWN") or "UNKNOWN")
    item["bdd_result_without_runtime"] = original
    item["runtime_strict_applied"] = True
    if runtime_result in {"FAIL", "ERROR"}:
        item["result"] = "FAIL"
        item["attribution"] = "runtime_strict"
    elif runtime_result == "BLOCKED":
        item["result"] = "BLOCKED"
        item["attribution"] = "runtime_strict_blocked"
    elif runtime_result == "TIMING_AMBIGUOUS":
        item["result"] = "TIMING_AMBIGUOUS"
        item["attribution"] = "runtime_strict_timing_ambiguous"
    else:
        item["result"] = runtime_result
        item["attribution"] = "runtime_strict"
    prefix = f"Runtime strict 将原 BDD={original} 调整为 {item['result']}，runtime={runtime_result}。"
    item["reason"] = prefix + " " + str(item.get("reason", ""))


def write_bdd_summary(run_dir: Path, plans: List[ScenarioPlan], run_summary: Dict[str, Any], *, runtime_strict: bool = False) -> Dict[str, Any]:
    scenario_results = [summarize_scenario(run_dir, plan) for plan in plans]
    runtime_sidecars = build_runtime_sidecars(run_dir, plans)
    for item in scenario_results:
        sidecar = runtime_sidecars.get(str(item.get("scenario_id", "")))
        if sidecar:
            item["runtime_replay"] = sidecar
            if runtime_strict:
                apply_runtime_strict_result(item)
    counts: Dict[str, int] = {}
    for item in scenario_results:
        result = str(item.get("result", "UNKNOWN") or "UNKNOWN")
        counts[result] = counts.get(result, 0) + 1
    payload = {
        "status": "DONE",
        "mode": run_summary.get("mode", ""),
        "run_dir": rel(run_dir),
        "scenario_count": len(scenario_results),
        "overall_counts": counts,
        "command_execution_results": run_summary.get("execution_results", []),
        "managed_session": run_summary.get("managed_session"),
        "snapshots": run_summary.get("snapshots", {}),
        "runtime_strict": runtime_strict,
        "runtime_replay_summary": runtime_sidecars,
        "scenario_results": scenario_results,
    }
    write_json(run_dir / "bdd_run_summary.json", payload)
    (run_dir / "bdd_run_report.md").write_text(render_bdd_report(run_dir, payload), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Polaris Cucumber Agent Testing in plan-only/dry-run/execute mode.")
    parser.add_argument("--feature", default=str(DEFAULT_FEATURE))
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--env-file", default="", help="本地环境配置 JSON，默认读取 polaris.local.json，兼容 config/polaris_env.json")
    parser.add_argument("--debug-root", default=str(DEFAULT_DEBUG_ROOT))
    parser.add_argument("--mode", choices=["plan-only", "dry-run", "execute"], default="plan-only")
    parser.add_argument("--tag", default="", help="按 @tag 或 scenario_id 过滤")
    parser.add_argument("--wake-word", default="")
    parser.add_argument("--command-text", default="")
    parser.add_argument("--command-file", default="")
    parser.add_argument("--command-limit", default="")
    parser.add_argument("--device-key", default=None)
    parser.add_argument("--observe-ms", default="")
    parser.add_argument("--wifi-ssid", default="")
    parser.add_argument("--wifi-password", default="")
    parser.add_argument("--allow-side-effects", action="store_true", help="execute 模式确认允许占用串口/播放/云端")
    parser.add_argument("--manage-session", action="store_true", help="execute 模式下创建 BDD 专用 session 并自动启动/停止串口 logger")
    parser.add_argument("--runtime-strict", action="store_true", help="将 Runtime sidecar 非 PASS 结果升级为 bdd_run_summary 主结果；默认只作为旁路证据")
    parser.add_argument("--summarize-run", default="", help="仅解析已有 run_dir 并生成 bdd_run_summary/report，不新建执行")
    parser.add_argument("--compiled-plan", default="", help="执行 compile_feature.py 生成的 compiled_plan.json")
    args = parser.parse_args()

    if args.summarize_run:
        run_dir = Path(args.summarize_run).resolve()
        plans = load_plans_from_run(run_dir)
        run_summary = read_json_safe(run_dir / "run_summary.json") or {"mode": "execute", "execution_results": []}
        run_summary["snapshots"] = finalize_cucumber_snapshots(run_dir, args, plans, None, run_summary=run_summary)
        write_json(run_dir / "run_summary.json", run_summary)
        write_bdd_summary(run_dir, plans, run_summary, runtime_strict=args.runtime_strict)
        print(run_dir / "bdd_run_report.md")
        return 0

    if args.compiled_plan:
        compiled_path = Path(args.compiled_plan).resolve()
        compiled_payload = load_json(compiled_path)
        debug_root = Path(args.debug_root).resolve()
        run_dir = debug_root / "runs" / f"{stamp()}_{args.mode.replace('-', '_')}_compiled"
        run_dir.mkdir(parents=True, exist_ok=True)
        plans = [scenario_plan_from_payload(item) for item in compiled_payload.get("plans", [])]
        plans = filter_plans(plans, args.tag)
        if not plans:
            raise SystemExit("compiled plan 中没有匹配到任何场景，请检查 --tag。")
        mapping_path = Path(args.mapping).resolve()
        mapping = load_json(mapping_path)
        runtime_context = resolve_context(args, mapping, run_dir)
        # A compiled plan can still contain late-bound placeholders such as
        # {debug_dir}. Re-render commands against the actual execute run_dir.
        for plan in plans:
            for command in plan.commands:
                command.cmd = drop_empty_optional_values([fill_placeholders(str(part), runtime_context) for part in command.cmd])
                command.cmdline = quote_cmd(command.cmd)

        parsed_for_report = {
            "feature": compiled_payload.get("feature", ""),
            "background_steps": [
                (item.get("keyword", "") + " " + item.get("text", "")).strip()
                for item in compiled_payload.get("background_steps", [])
            ],
        }
        write_json(
            run_dir / "gherkin_scenarios.json",
            {
                "compiled_plan": rel(compiled_path),
                "feature": compiled_payload.get("feature", ""),
                "background_steps": compiled_payload.get("background_steps", []),
                "scenarios": compiled_payload.get("scenarios", []),
            },
        )
        write_json(
            run_dir / "execution_plan.json",
            {
                "mode": args.mode,
                "compiled_plan": rel(compiled_path),
                "context": {**(compiled_payload.get("context", {}) if isinstance(compiled_payload.get("context"), dict) else {}), **runtime_context},
                "compile_errors": compiled_payload.get("compile_errors", []),
                "plans": [asdict(plan) for plan in plans],
            },
        )
        (run_dir / "execution_plan.md").write_text(render_markdown(run_dir, parsed_for_report, args.mode, plans), encoding="utf-8")
        before_snapshot = write_cucumber_before_snapshot(run_dir, args, plans)

        execution_results: List[Dict[str, Any]] = []
        managed_session: Optional[ManagedSession] = None
        logger_proc: Optional[subprocess.Popen[str]] = None
        try:
            if args.mode == "execute" and args.manage_session:
                managed_session, logger_proc = start_managed_session(run_dir, runtime_context)
                plan_payload = load_json(run_dir / "execution_plan.json")
                plan_payload["managed_session"] = managed_session_payload(managed_session)
                write_json(run_dir / "execution_plan.json", plan_payload)
            if args.mode == "execute":
                execution_results = execute_plans(plans, run_dir, args.allow_side_effects)
            elif args.mode == "dry-run":
                print(render_markdown(run_dir, parsed_for_report, args.mode, plans))
        finally:
            if managed_session is not None and logger_proc is not None:
                stop_managed_session(managed_session, logger_proc)

        run_summary = {
            "status": "DONE",
            "mode": args.mode,
            "scenario_count": len(plans),
            "run_dir": rel(run_dir),
            "compiled_plan": rel(compiled_path),
            "managed_session": managed_session_payload(managed_session),
            "execution_results": execution_results,
            "side_effect_policy": "execute requires --allow-side-effects",
        }
        write_json(run_dir / "run_summary.json", run_summary)
        run_summary["snapshots"] = finalize_cucumber_snapshots(run_dir, args, plans, before_snapshot, run_summary=run_summary)
        write_json(run_dir / "run_summary.json", run_summary)
        if args.mode == "execute":
            write_bdd_summary(run_dir, plans, run_summary, runtime_strict=args.runtime_strict)
        print(run_dir)
        return 0

    feature_path = Path(args.feature).resolve()
    mapping_path = Path(args.mapping).resolve()
    debug_root = Path(args.debug_root).resolve()
    run_dir = debug_root / "runs" / f"{stamp()}_{args.mode.replace('-', '_')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    parsed = parse_feature(feature_path)
    mapping = load_json(mapping_path)
    context = resolve_context(args, mapping, run_dir)
    plans = filter_plans(build_plans(parsed, mapping, context), args.tag)
    if not plans:
        raise SystemExit("没有匹配到任何 Cucumber 场景，请检查 feature tag 与 mapping。")

    write_json(
        run_dir / "gherkin_scenarios.json",
        {
            "feature": parsed.get("feature", ""),
            "background_steps": parsed.get("background_steps", []),
            "scenarios": [asdict(item) for item in parsed["scenarios"]],
        },
    )
    write_json(
        run_dir / "execution_plan.json",
        {
            "mode": args.mode,
            "feature": rel(feature_path),
            "mapping": rel(mapping_path),
            "context": context,
            "plans": [asdict(plan) for plan in plans],
        },
    )
    (run_dir / "execution_plan.md").write_text(render_markdown(run_dir, parsed, args.mode, plans), encoding="utf-8")
    before_snapshot = write_cucumber_before_snapshot(run_dir, args, plans)

    execution_results: List[Dict[str, Any]] = []
    managed_session: Optional[ManagedSession] = None
    logger_proc: Optional[subprocess.Popen[str]] = None
    try:
        if args.mode == "execute" and args.manage_session:
            managed_session, logger_proc = start_managed_session(run_dir, context)
            # Re-resolve context after marker update, but keep user-provided overrides.
            context = resolve_context(args, mapping, run_dir)
            plans = filter_plans(build_plans(parsed, mapping, context), args.tag)
            write_json(
                run_dir / "execution_plan.json",
                {
                    "mode": args.mode,
                    "feature": rel(feature_path),
                    "mapping": rel(mapping_path),
                    "context": context,
                    "managed_session": managed_session_payload(managed_session),
                    "plans": [asdict(plan) for plan in plans],
                },
            )
            (run_dir / "execution_plan.md").write_text(render_markdown(run_dir, parsed, args.mode, plans), encoding="utf-8")

        if args.mode == "execute":
            execution_results = execute_plans(plans, run_dir, args.allow_side_effects)
        elif args.mode == "dry-run":
            print(render_markdown(run_dir, parsed, args.mode, plans))
    finally:
        if managed_session is not None and logger_proc is not None:
            stop_managed_session(managed_session, logger_proc)

    run_summary = {
        "status": "DONE",
        "mode": args.mode,
        "scenario_count": len(plans),
        "run_dir": rel(run_dir),
        "managed_session": managed_session_payload(managed_session),
        "execution_results": execution_results,
        "side_effect_policy": "execute requires --allow-side-effects",
    }
    write_json(run_dir / "run_summary.json", run_summary)
    run_summary["snapshots"] = finalize_cucumber_snapshots(run_dir, args, plans, before_snapshot, run_summary=run_summary)
    write_json(run_dir / "run_summary.json", run_summary)
    if args.mode == "execute":
        write_bdd_summary(run_dir, plans, run_summary, runtime_strict=args.runtime_strict)
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
