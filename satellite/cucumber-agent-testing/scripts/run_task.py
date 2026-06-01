#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从任务配置文件触发 Polaris Cucumber 测试。

任务文件是给新使用者的稳定入口：他们只需要复制 env 模板、选择一个
task JSON，再执行本脚本即可。脚本本身不访问网络、不调用大模型。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from polaris_env import default_env_path, load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
RUN_CUCUMBER = SCRIPT_DIR / "run_cucumber.py"
COMPILE_FEATURE = SCRIPT_DIR / "compile_feature.py"
ONLINE_MIXED_STRESS = SCRIPT_DIR / "run_online_mixed_stress.py"
COMMAND_CONTROL_DIAGNOSIS = SCRIPT_DIR / "run_command_control_diagnosis.py"


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


def resolve_workspace_path(value: str) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((WORKSPACE_ROOT / value).resolve())


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


def add_optional(cmd: List[str], option: str, value: Any, *, path_value: bool = False) -> None:
    text = first_non_empty(value)
    if not text:
        return
    cmd.extend([option, resolve_workspace_path(text) if path_value else text])


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


def build_common_args(
    args: argparse.Namespace,
    task: Dict[str, Any],
    env_payload: Dict[str, Any],
    default_env_file: str,
) -> Dict[str, str]:
    runner = task.get("runner", {})
    scenario = task.get("scenario", {})
    environment = task.get("environment", {})
    inputs = task.get("inputs", {})
    execution = task.get("execution", {})
    network = environment.get("network", {})

    env_file = first_non_empty(args.env_file, environment.get("env_file"), runner.get("env_file"), default_env_file)
    tag = first_non_empty(args.tag, scenario.get("tag"), task.get("scenario_tag"))
    mode = first_non_empty(args.mode, runner.get("mode"), task.get("mode"), "plan-only")

    return {
        "env_file": env_file,
        "tag": tag,
        "mode": mode,
        "feature": first_non_empty(args.feature, runner.get("feature"), "satellite/cucumber-agent-testing/features/polaris_voice_core.feature"),
        "mapping": first_non_empty(args.mapping, runner.get("mapping"), "satellite/cucumber-agent-testing/references/voice_core_mapping.json"),
        "debug_root": first_non_empty(runner.get("debug_root"), nested(env_payload, "paths", "debug_root"), "satellite/cucumber-agent-testing/debug"),
        "wake_word": first_non_empty(
            args.wake_word,
            environment.get("wake_word"),
            nested(environment, "device", "wake_word"),
            env_payload.get("current_wakeup_word", ""),
            nested(env_payload, "device", "wake_word"),
        ),
        "device_key": first_non_empty(
            args.device_key,
            environment.get("device_key"),
            nested(environment, "audio", "default_playback_device_key"),
            env_payload.get("default_playback_device_key", ""),
            nested(env_payload, "audio", "default_playback_device_key"),
        ),
        "command_text": first_non_empty(args.command_text, inputs.get("command_text")),
        "command_file": first_non_empty(args.command_file, inputs.get("command_file"), nested(env_payload, "paths", "command_file")),
        "command_limit": first_non_empty(args.command_limit, inputs.get("command_limit"), nested(env_payload, "limits", "command_limit")),
        "observe_ms": first_non_empty(args.observe_ms, execution.get("observe_ms"), nested(env_payload, "timeouts", "observe_ms")),
        "wifi_ssid": first_non_empty(
            args.wifi_ssid,
            environment.get("wifi_ssid"),
            network.get("wifi_ssid"),
            env_payload.get("current_connected_ssid", ""),
            nested(env_payload, "network", "wifi_ssid"),
        ),
        "wifi_password": first_non_empty(
            args.wifi_password,
            environment.get("wifi_password"),
            network.get("wifi_password"),
            env_payload.get("wifi_password", ""),
            nested(env_payload, "network", "wifi_password"),
        ),
        "recognition_timeout_s": first_non_empty(execution.get("recognition_timeout_s"), nested(env_payload, "timeouts", "recognition_timeout_s")),
        "half_duplex_timeout_s": first_non_empty(execution.get("half_duplex_timeout_s"), nested(env_payload, "timeouts", "half_duplex_timeout_s")),
        "full_duplex_timeout_s": first_non_empty(execution.get("full_duplex_timeout_s"), nested(env_payload, "timeouts", "full_duplex_timeout_s")),
    }


def build_compile_command(common: Dict[str, str], strict: bool) -> List[str]:
    cmd = [
        sys.executable,
        str(COMPILE_FEATURE),
        "--feature",
        resolve_workspace_path(common["feature"]),
        "--mapping",
        resolve_workspace_path(common["mapping"]),
        "--env-file",
        resolve_workspace_path(common["env_file"]),
        "--debug-root",
        resolve_workspace_path(common["debug_root"]),
    ]
    add_optional(cmd, "--tag", common["tag"])
    add_optional(cmd, "--wake-word", common["wake_word"])
    add_optional(cmd, "--command-text", common["command_text"])
    add_optional(cmd, "--command-file", common["command_file"])
    add_optional(cmd, "--command-limit", common["command_limit"])
    add_optional(cmd, "--device-key", common["device_key"])
    add_optional(cmd, "--observe-ms", common["observe_ms"])
    add_optional(cmd, "--wifi-ssid", common["wifi_ssid"])
    add_optional(cmd, "--wifi-password", common["wifi_password"])
    add_optional(cmd, "--recognition-timeout-s", common["recognition_timeout_s"])
    add_optional(cmd, "--half-duplex-timeout-s", common["half_duplex_timeout_s"])
    add_optional(cmd, "--full-duplex-timeout-s", common["full_duplex_timeout_s"])
    if strict:
        cmd.append("--strict")
    return cmd


def build_run_command(
    common: Dict[str, str],
    compiled_plan: str,
    allow_side_effects: bool,
    manage_session: bool,
    runtime_strict: bool,
) -> List[str]:
    cmd = [
        sys.executable,
        str(RUN_CUCUMBER),
        "--mode",
        common["mode"],
        "--env-file",
        resolve_workspace_path(common["env_file"]),
        "--debug-root",
        resolve_workspace_path(common["debug_root"]),
    ]
    if compiled_plan:
        cmd.extend(["--compiled-plan", compiled_plan])
    else:
        cmd.extend(["--feature", resolve_workspace_path(common["feature"]), "--mapping", resolve_workspace_path(common["mapping"])])
    add_optional(cmd, "--tag", common["tag"])
    add_optional(cmd, "--wake-word", common["wake_word"])
    add_optional(cmd, "--command-text", common["command_text"])
    add_optional(cmd, "--command-file", common["command_file"])
    add_optional(cmd, "--command-limit", common["command_limit"])
    add_optional(cmd, "--device-key", common["device_key"])
    add_optional(cmd, "--observe-ms", common["observe_ms"])
    add_optional(cmd, "--wifi-ssid", common["wifi_ssid"])
    add_optional(cmd, "--wifi-password", common["wifi_password"])
    if allow_side_effects:
        cmd.append("--allow-side-effects")
    if manage_session:
        cmd.append("--manage-session")
    if runtime_strict:
        cmd.append("--runtime-strict")
    return cmd


def is_online_stress_task(task: Dict[str, Any]) -> bool:
    runner = task.get("runner", {}) if isinstance(task.get("runner"), dict) else {}
    schema = str(task.get("schema", ""))
    entrypoint = str(runner.get("entrypoint", ""))
    return "online-stress" in schema or "run_online_mixed_stress.py" in entrypoint


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


def build_online_stress_command(args: argparse.Namespace, task: Dict[str, Any], env_file: str) -> List[str]:
    runner = task.get("runner", {}) if isinstance(task.get("runner"), dict) else {}
    environment = task.get("environment", {}) if isinstance(task.get("environment"), dict) else {}
    execution = task.get("execution", {}) if isinstance(task.get("execution"), dict) else {}
    overrides = task.get("overrides", {}) if isinstance(task.get("overrides"), dict) else {}
    entrypoint = first_non_empty(runner.get("entrypoint"), str(ONLINE_MIXED_STRESS))
    cmd = [
        sys.executable,
        resolve_workspace_path(entrypoint),
        "--env-file",
        resolve_workspace_path(env_file),
    ]
    add_optional(cmd, "--project", environment.get("project"))
    add_optional(cmd, "--strategy-file", runner.get("strategy_file"), path_value=True)
    add_optional(cmd, "--strategy-name", runner.get("strategy_name"))
    add_optional(cmd, "--end-at", execution.get("end_at"))
    add_optional(cmd, "--max-rounds", execution.get("max_rounds"))
    add_optional(cmd, "--seed", execution.get("seed"))
    add_optional(cmd, "--summary-every", execution.get("summary_every"))
    add_optional(cmd, "--sample-window-every", execution.get("sample_window_every"))
    random_gap = overrides.get("random_gap_s", [])
    if isinstance(random_gap, list) and len(random_gap) >= 2:
        cmd.extend(["--min-gap-s", str(random_gap[0]), "--max-gap-s", str(random_gap[1])])
    observe = overrides.get("observe_s", [])
    if isinstance(observe, list) and len(observe) >= 2:
        cmd.extend(["--min-observe-s", str(observe[0]), "--max-observe-s", str(observe[1])])
    add_optional(cmd, "--device-key", args.device_key)
    add_optional(cmd, "--wake-text", args.wake_word)
    return cmd


def build_command_control_diagnosis_command(args: argparse.Namespace, task: Dict[str, Any], env_file: str) -> List[str]:
    runner = task.get("runner", {}) if isinstance(task.get("runner"), dict) else {}
    execution = task.get("execution", {}) if isinstance(task.get("execution"), dict) else {}
    script = first_non_empty(runner.get("script"), runner.get("entrypoint"), str(COMMAND_CONTROL_DIAGNOSIS))
    out_root = first_non_empty(
        args.out_root,
        execution.get("out_root"),
        runner.get("out_root"),
        "satellite/cucumber-agent-testing/debug/command_control_diagnosis",
    )
    cmd = [
        sys.executable,
        resolve_workspace_path(script),
        "--env-file",
        resolve_workspace_path(env_file),
        "--out-root",
        resolve_workspace_path(out_root),
    ]
    add_optional(cmd, "--matrix-file", first_non_empty(args.matrix_file, runner.get("matrix_file")), path_value=True)
    add_optional(cmd, "--device-key", first_non_empty(args.device_key, nested(task, "environment", "device_key")))
    add_optional(cmd, "--wake-text", first_non_empty(args.wake_word, nested(task, "environment", "wake_word")))
    add_optional(cmd, "--max-cases", first_non_empty(args.max_cases, execution.get("max_cases"), runner.get("max_cases")))
    add_optional(cmd, "--required-serial-roles", first_non_empty(args.required_serial_roles, nested(task, "serial_coverage", "required_serial_roles"), execution.get("required_serial_roles")))
    add_optional(cmd, "--wait-serial-coverage-s", first_non_empty(args.wait_serial_coverage_s, execution.get("wait_serial_coverage_s")))
    if args.no_preflight or bool(execution.get("no_preflight")):
        cmd.append("--no-preflight")
    # The diagnosis runner restores half-duplex by default; only expose the
    # explicit positive flag because argparse currently has no --no counterpart.
    if args.restore_half_duplex or bool(execution.get("restore_half_duplex", False)):
        cmd.append("--restore-half-duplex")
    if resolve_bool(None, args.allow_side_effects, execution.get("allow_side_effects"), nested(task, "policy", "allow_side_effects")):
        cmd.append("--allow-side-effects")
    return cmd


def run_and_echo(cmd: List[str], *, capture_plan_path: bool = False) -> str:
    print("$ " + quote_cmd(cmd), flush=True)
    completed = subprocess.run(
        cmd,
        cwd=str(WORKSPACE_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture_plan_path else None,
        stderr=subprocess.STDOUT if capture_plan_path else None,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if capture_plan_path:
        output = completed.stdout or ""
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        for line in reversed([item.strip() for item in output.splitlines() if item.strip()]):
            if line.endswith(".json") or Path(line).exists():
                return line
        raise SystemExit("compile_feature 未输出 compiled_plan 路径。")
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Polaris Cucumber by a task JSON file.")
    parser.add_argument("--task", required=True, help="任务配置 JSON，例如 tasks/examples/first_wake.example.json")
    parser.add_argument("--mode", choices=["plan-only", "dry-run", "execute"], default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--feature", default="")
    parser.add_argument("--mapping", default="")
    parser.add_argument("--wake-word", default="")
    parser.add_argument("--command-text", default="")
    parser.add_argument("--command-file", default="")
    parser.add_argument("--command-limit", default="")
    parser.add_argument("--device-key", default="")
    parser.add_argument("--observe-ms", default="")
    parser.add_argument("--wifi-ssid", default="")
    parser.add_argument("--wifi-password", default="")
    parser.add_argument("--out-root", default="", help="专项 runner 输出根目录。")
    parser.add_argument("--matrix-file", default="", help="command-control diagnosis 矩阵文件。")
    parser.add_argument("--max-cases", default="", help="command-control diagnosis 最大用例数。")
    parser.add_argument("--required-serial-roles", default="", help="command-control diagnosis 必须打开的串口角色。")
    parser.add_argument("--wait-serial-coverage-s", default="", help="等待串口 coverage heartbeat 的秒数。")
    parser.add_argument("--no-preflight", action="store_true", help="command-control diagnosis 跳过前置预检。")
    parser.add_argument("--restore-half-duplex", action="store_true", help="command-control diagnosis 结束后恢复半双工。")
    parser.add_argument("--compile-first", action="store_true", help="先用 step/action/assertion registry 离线编译，再执行 compiled plan")
    parser.add_argument("--no-compile-first", dest="compile_first", action="store_false")
    parser.set_defaults(compile_first=None)
    parser.add_argument("--strict", action="store_true", help="compile-first 时启用严格编译")
    parser.add_argument("--allow-side-effects", dest="allow_side_effects", action="store_true", default=None)
    parser.add_argument("--no-allow-side-effects", dest="allow_side_effects", action="store_false")
    parser.add_argument("--manage-session", dest="manage_session", action="store_true", default=None)
    parser.add_argument("--no-manage-session", dest="manage_session", action="store_false")
    parser.add_argument("--runtime-strict", dest="runtime_strict", action="store_true", default=None)
    parser.add_argument("--no-runtime-strict", dest="runtime_strict", action="store_false")
    parser.add_argument("--print-command", action="store_true", help="只打印最终命令，不执行")
    args = parser.parse_args()

    task_path = Path(args.task)
    if not task_path.is_absolute():
        task_path = WORKSPACE_ROOT / task_path
    task = load_json(task_path.resolve())
    runner = task.get("runner", {})
    execution = task.get("execution", {})
    policy = task.get("policy", {})

    env_file = first_non_empty(
        args.env_file,
        task.get("environment", {}).get("env_file"),
        runner.get("env_file"),
        str(default_env_path(WORKSPACE_ROOT)),
    )
    env_path = resolve_env_path(env_file, WORKSPACE_ROOT)
    env_payload = load_env_payload(env_path)
    common = build_common_args(args, task, env_payload, str(env_path))
    online_stress_task = is_online_stress_task(task)
    command_control_task = is_command_control_diagnosis_task(task)

    if not online_stress_task and not command_control_task and not common["tag"] and not runner.get("allow_all_scenarios", False):
        raise SystemExit("任务文件没有指定 scenario.tag；如需跑全量，请在 runner.allow_all_scenarios=true 后再执行。")

    allow_side_effects = resolve_bool(args.allow_side_effects, execution.get("allow_side_effects"), policy.get("allow_side_effects"))
    manage_session = resolve_bool(args.manage_session, execution.get("manage_session"), policy.get("manage_session"))
    runtime_strict = resolve_bool(args.runtime_strict, execution.get("runtime_strict"), policy.get("runtime_strict"))
    compile_first = resolve_bool(args.compile_first, runner.get("compile_first"))

    if online_stress_task:
        stress_cmd = build_online_stress_command(args, task, str(env_path))
        if common["mode"] != "execute":
            print("$ " + quote_cmd(stress_cmd))
            print(f"result=PLAN_OK mode={common['mode']}")
            return 0
        if not allow_side_effects:
            raise SystemExit("在线压测会占用串口/声卡/云端，任务或命令行必须设置 allow_side_effects=true。")
        if args.print_command:
            print("$ " + quote_cmd(stress_cmd))
            return 0
        run_and_echo(stress_cmd)
        return 0

    if command_control_task:
        diagnosis_cmd = build_command_control_diagnosis_command(args, task, str(env_path))
        if common["mode"] != "execute":
            print("$ " + quote_cmd(diagnosis_cmd))
            print(f"result=PLAN_OK mode={common['mode']}")
            return 0
        if not allow_side_effects:
            raise SystemExit("控制命令完整链路诊断会占用串口/声卡/云端，任务或命令行必须设置 allow_side_effects=true。")
        if args.print_command:
            print("$ " + quote_cmd(diagnosis_cmd))
            return 0
        run_and_echo(diagnosis_cmd)
        return 0

    compiled_plan = ""
    if compile_first:
        compile_cmd = build_compile_command(common, strict=bool(args.strict or runner.get("strict_compile", False)))
        if args.print_command:
            print("$ " + quote_cmd(compile_cmd))
        else:
            compiled_plan = run_and_echo(compile_cmd, capture_plan_path=True)

    run_cmd = build_run_command(common, compiled_plan, allow_side_effects, manage_session, runtime_strict)
    if args.print_command:
        print("$ " + quote_cmd(run_cmd))
        return 0
    run_and_echo(run_cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
