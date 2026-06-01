#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线编译 Gherkin feature 为可执行 plan。

这个脚本不调用大模型、不访问网络。它只使用本地 registry：
- step_registry.json：受控中文步骤 -> action/assertion
- action_registry.json：action -> 本地命令模板
- feature_contracts.json：功能意图、证据和归因规则
- voice_core_mapping.json：已验证场景的兼容映射
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from polaris_env import load_env_payload, resolve_env_path


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_FEATURE = BDD_ROOT / "features" / "polaris_voice_core.feature"
DEFAULT_MAPPING = BDD_ROOT / "references" / "voice_core_mapping.json"
DEFAULT_STEP_REGISTRY = BDD_ROOT / "references" / "step_registry.json"
DEFAULT_ACTION_REGISTRY = BDD_ROOT / "references" / "action_registry.json"
DEFAULT_CONTRACTS = BDD_ROOT / "references" / "feature_contracts.json"
DEFAULT_DEBUG_ROOT = BDD_ROOT / "debug"

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


@dataclass
class ParsedStep:
    keyword: str
    text: str
    line: int
    table: List[Dict[str, str]]


@dataclass
class ParsedScenario:
    name: str
    tags: List[str]
    steps: List[ParsedStep]
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


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def split_step(line: str) -> Tuple[str, str]:
    for prefix in STEP_PREFIXES:
        if line.startswith(prefix):
            return prefix.strip(), line[len(prefix) :].strip()
    return "", line.strip()


def parse_table_row(line: str) -> List[str]:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def rows_to_dicts(rows: List[List[str]]) -> List[Dict[str, str]]:
    if len(rows) < 2:
        return []
    headers = rows[0]
    result: List[Dict[str, str]] = []
    for row in rows[1:]:
        item = {headers[index]: row[index] if index < len(row) else "" for index in range(len(headers))}
        result.append(item)
    return result


def parse_feature(path: Path) -> Dict[str, Any]:
    feature_name = ""
    background_steps: List[ParsedStep] = []
    scenarios: List[ParsedScenario] = []
    pending_tags: List[str] = []
    current: Optional[ParsedScenario] = None
    in_background = False
    last_step: Optional[ParsedStep] = None
    pending_table_rows: List[List[str]] = []

    def flush_table() -> None:
        nonlocal pending_table_rows
        if last_step is not None and pending_table_rows:
            last_step.table = rows_to_dicts(pending_table_rows)
        pending_table_rows = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        table_row = parse_table_row(line)
        if table_row:
            pending_table_rows.append(table_row)
            continue
        flush_table()

        if line.startswith("@"):
            pending_tags = [part.strip() for part in line.split() if part.strip()]
            continue
        if line.startswith("功能:"):
            feature_name = line.split(":", 1)[1].strip()
            continue
        if line.startswith("背景:"):
            in_background = True
            current = None
            last_step = None
            continue
        if line.startswith("场景:") or line.startswith("Scenario:"):
            name = line.split(":", 1)[1].strip()
            current = ParsedScenario(name=name, tags=pending_tags, steps=[], line=line_no)
            pending_tags = []
            scenarios.append(current)
            in_background = False
            last_step = None
            continue
        if line.startswith(STEP_PREFIXES):
            keyword, text = split_step(line)
            step = ParsedStep(keyword=keyword, text=text, line=line_no, table=[])
            if current is not None:
                current.steps.append(step)
            elif in_background:
                background_steps.append(step)
            last_step = step

    flush_table()
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


def resolve_context(args: argparse.Namespace, mapping: Dict[str, Any]) -> Dict[str, str]:
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
    command_limit = first_non_empty(args.command_limit, defaults.get("command_limit", ""), nested(env_payload, "limits", "command_limit"), "20")
    observe_ms = first_non_empty(args.observe_ms, defaults.get("observe_ms", ""), nested(env_payload, "timeouts", "observe_ms"), "15000")
    wifi_ssid = first_non_empty(
        args.wifi_ssid,
        defaults.get("wifi_ssid", ""),
        env_payload.get("current_connected_ssid", ""),
        nested(env_payload, "network", "wifi_ssid"),
        "",
    )
    wifi_password = first_non_empty(
        args.wifi_password,
        defaults.get("wifi_password", ""),
        env_payload.get("wifi_password", ""),
        nested(env_payload, "network", "wifi_password"),
        "",
    )
    recognition_timeout_s = first_non_empty(args.recognition_timeout_s, nested(env_payload, "timeouts", "recognition_timeout_s"), "15")
    half_duplex_timeout_s = first_non_empty(args.half_duplex_timeout_s, nested(env_payload, "timeouts", "half_duplex_timeout_s"), "15")
    full_duplex_timeout_s = first_non_empty(args.full_duplex_timeout_s, nested(env_payload, "timeouts", "full_duplex_timeout_s"), "60")
    return {
        "python": sys.executable,
        "root": str(WORKSPACE_ROOT),
        "env_file": str(env_path),
        "wake_word": wake_word,
        "command_text": args.command_text or str(defaults.get("command_text", "打开空调")),
        "command_file": command_file,
        "command_limit": command_limit,
        "device_key": device_key,
        "observe_ms": observe_ms,
        "wifi_ssid": wifi_ssid,
        "wifi_password": wifi_password,
        "recognition_timeout_s": recognition_timeout_s,
        "half_duplex_timeout_s": half_duplex_timeout_s,
        "full_duplex_timeout_s": full_duplex_timeout_s,
        "ap_port": first_non_empty(nested(env_payload, "serial", "ports", "ap"), nested(env_payload, "ports", "ap")),
        "cp_port": first_non_empty(nested(env_payload, "serial", "ports", "cp"), nested(env_payload, "ports", "cp")),
        "asr_port": first_non_empty(nested(env_payload, "serial", "ports", "asr"), nested(env_payload, "ports", "asr")),
        "control_port": first_non_empty(nested(env_payload, "serial", "ports", "control"), nested(env_payload, "ports", "control")),
        "baudrate": first_non_empty(nested(env_payload, "serial", "baudrate"), env_payload.get("baudrate", ""), "115200"),
    }


def fill_placeholders(value: str, context: Dict[str, str]) -> str:
    result = value
    for key, item in context.items():
        result = result.replace("{" + key + "}", str(item))
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


def render_params(params: Dict[str, Any], context: Dict[str, str]) -> Dict[str, str]:
    rendered = dict(context)
    for key, value in params.items():
        rendered[key] = fill_placeholders(str(value), rendered)
    mode = rendered.get("mode", "")
    if mode == "全双工":
        rendered["mode_enable"] = "1"
        rendered.setdefault("timeout_s", rendered.get("full_duplex_timeout_s", "60"))
    elif mode == "半双工":
        rendered["mode_enable"] = "0"
        rendered.setdefault("timeout_s", rendered.get("half_duplex_timeout_s", "15"))
    return rendered


def build_command_from_template(name: str, template: List[str], context: Dict[str, str]) -> CommandPlan:
    cmd = drop_empty_optional_values([fill_placeholders(str(part), context) for part in template])
    return CommandPlan(name=name, cmd=cmd, cmdline=quote_cmd(cmd))


def build_commands_from_mapping(raw_commands: List[Dict[str, Any]], context: Dict[str, str]) -> List[CommandPlan]:
    plans: List[CommandPlan] = []
    for raw in raw_commands:
        plans.append(
            build_command_from_template(
                str(raw.get("name", "command")),
                [str(part) for part in raw.get("cmd", [])],
                context,
            )
        )
    return plans


def load_step_patterns(step_registry: Dict[str, Any]) -> List[Tuple[Dict[str, Any], re.Pattern[str]]]:
    patterns: List[Tuple[Dict[str, Any], re.Pattern[str]]] = []
    for step in step_registry.get("steps", []):
        patterns.append((step, re.compile(str(step["pattern"]))))
    return patterns


def match_step(step: ParsedStep, patterns: List[Tuple[Dict[str, Any], re.Pattern[str]]]) -> Dict[str, Any]:
    for spec, pattern in patterns:
        match = pattern.match(step.text)
        if not match:
            continue
        return {
            "matched": True,
            "step_id": spec.get("id"),
            "kind": spec.get("kind"),
            "contract": spec.get("contract", ""),
            "params": {key: value for key, value in match.groupdict().items() if value is not None},
            "actions": spec.get("actions", []),
            "assertions": spec.get("assertions", []),
            "table": step.table,
        }
    return {
        "matched": False,
        "step_id": "",
        "kind": "",
        "contract": "",
        "params": {},
        "actions": [],
        "assertions": [],
        "table": step.table,
    }


def commands_from_step_matches(
    matches: List[Dict[str, Any]],
    action_registry: Dict[str, Any],
    context: Dict[str, str],
) -> List[CommandPlan]:
    commands: List[CommandPlan] = []
    actions = action_registry.get("actions", {})
    for match in matches:
        for index, action_item in enumerate(match.get("actions", []), start=1):
            action_id = str(action_item.get("action", ""))
            action_spec = actions.get(action_id)
            if not action_spec:
                continue
            params = {}
            params.update(match.get("params", {}))
            params.update(action_item.get("params", {}))
            action_context = render_params(params, context)
            label = action_context.get("label", action_id.replace(".", "_"))
            action_context.setdefault("label", label)
            name = f"{action_id.replace('.', '_')}_{index}"
            commands.append(build_command_from_template(name, action_spec.get("command_template", []), action_context))
    return commands


def scenario_assertions_from_matches(matches: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    assertions: List[Dict[str, str]] = []
    seen = set()
    for match in matches:
        for item in match.get("assertions", []):
            key = str(item)
            if key in seen:
                continue
            seen.add(key)
            assertions.append({"name": key, "expected": "see assertion_registry", "owner": "registry"})
        for row in match.get("table", []) or []:
            metric = row.get("指标") or row.get("metric") or row.get("Metric")
            expected = row.get("条件") or row.get("expected") or row.get("Expected")
            if metric:
                assertions.append({"name": metric, "expected": expected or "", "owner": "feature-table"})
    return assertions


def filter_scenarios(scenarios: List[ParsedScenario], tag: str) -> List[ParsedScenario]:
    if not tag:
        return scenarios
    normalized = tag if tag.startswith("@") else f"@{tag}"
    return [
        scenario
        for scenario in scenarios
        if normalized in scenario.tags or normalized.lstrip("@") in {tag.lstrip("@") for tag in scenario.tags}
    ]


def compile_feature(args: argparse.Namespace) -> Tuple[Path, Dict[str, Any]]:
    feature_path = Path(args.feature).resolve()
    mapping = load_json(Path(args.mapping).resolve())
    step_registry = load_json(Path(args.step_registry).resolve())
    action_registry = load_json(Path(args.action_registry).resolve())
    contracts = load_json(Path(args.contracts).resolve())
    context = resolve_context(args, mapping)

    parsed = parse_feature(feature_path)
    patterns = load_step_patterns(step_registry)
    background_matches = [match_step(step, patterns) for step in parsed["background_steps"]]
    scenarios = filter_scenarios(parsed["scenarios"], args.tag)

    plans: List[ScenarioPlan] = []
    compile_errors: List[str] = []
    compiled_scenarios: List[Dict[str, Any]] = []

    for scenario in scenarios:
        scenario_id = scenario_id_from_tags(scenario.tags, mapping) or re.sub(r"\W+", "_", scenario.name).strip("_")
        raw_mapping = mapping.get("scenarios", {}).get(scenario_id)
        step_matches = [match_step(step, patterns) for step in scenario.steps]
        all_matches = background_matches + step_matches
        for step, match in zip(parsed["background_steps"] + scenario.steps, all_matches):
            if not match["matched"]:
                compile_errors.append(f"未匹配步骤 line {step.line}: {step.text}")

        if raw_mapping:
            commands = build_commands_from_mapping(raw_mapping.get("commands", []), context)
            assertions = [dict(item) for item in raw_mapping.get("assertions", [])]
            preconditions = [str(item) for item in raw_mapping.get("preconditions", [])]
            failure_split = [str(item) for item in raw_mapping.get("failure_split", [])]
            mapping_title = str(raw_mapping.get("title", scenario.name))
            source_test_item = str(raw_mapping.get("source_test_item", ""))
            validation_module = str(raw_mapping.get("validation_module", ""))
            agent_goal = str(raw_mapping.get("agent_goal", ""))
            executable_source = "voice_core_mapping"
        else:
            commands = commands_from_step_matches(all_matches, action_registry, context)
            assertions = scenario_assertions_from_matches(all_matches)
            contracts_used = sorted({str(match.get("contract", "")) for match in all_matches if match.get("contract")})
            preconditions = []
            failure_split = []
            for contract_id in contracts_used:
                contract = contracts.get("contracts", {}).get(contract_id, {})
                preconditions.extend([str(item) for item in contract.get("preconditions", [])])
                failure_split.extend([str(item) for item in contract.get("failure_split", [])])
            mapping_title = scenario.name
            source_test_item = ",".join(contracts_used)
            validation_module = ",".join(contracts_used)
            agent_goal = "由 step/action/assertion registry 离线编译。"
            executable_source = "step_registry"
            if not commands:
                compile_errors.append(f"场景 `{scenario.name}` 没有可执行 command，需补 action registry 或 mapping。")

        plan = ScenarioPlan(
            scenario_id=scenario_id,
            scenario_name=scenario.name,
            tags=scenario.tags,
            feature_steps=[step.text for step in scenario.steps],
            mapping_title=mapping_title,
            source_test_item=source_test_item,
            validation_module=validation_module,
            agent_goal=agent_goal,
            preconditions=preconditions,
            commands=commands,
            assertions=assertions,
            failure_split=failure_split,
        )
        plans.append(plan)
        compiled_scenarios.append(
            {
                "scenario_id": scenario_id,
                "scenario_name": scenario.name,
                "line": scenario.line,
                "tags": scenario.tags,
                "executable_source": executable_source,
                "step_matches": [
                    {
                        "text": step.text,
                        "line": step.line,
                        **match,
                    }
                    for step, match in zip(scenario.steps, step_matches)
                ],
                "commands": [asdict(command) for command in commands],
                "assertions": assertions,
            }
        )

    if args.strict and compile_errors:
        raise SystemExit("Feature 编译失败：\n" + "\n".join(f"- {item}" for item in compile_errors))

    out_dir = Path(args.output_dir).resolve() if args.output_dir else Path(args.debug_root).resolve() / "compiled_plans" / stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "polaris.cucumber.compiled-plan.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "feature": rel(feature_path),
        "context": context,
        "strict": bool(args.strict),
        "compile_errors": compile_errors,
        "background_steps": [asdict(step) for step in parsed["background_steps"]],
        "background_matches": background_matches,
        "scenarios": compiled_scenarios,
        "plans": [asdict(plan) for plan in plans],
    }
    plan_path = out_dir / "compiled_plan.json"
    write_json(plan_path, payload)
    (out_dir / "compiled_plan.md").write_text(render_markdown(payload), encoding="utf-8")
    return plan_path, payload


def render_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# Polaris Cucumber 离线编译计划",
        "",
        f"- Feature：`{payload.get('feature', '')}`",
        f"- 生成时间：`{payload.get('generated_at', '')}`",
        f"- 场景数：`{len(payload.get('plans', []))}`",
        f"- 编译错误数：`{len(payload.get('compile_errors', []))}`",
        "",
    ]
    if payload.get("compile_errors"):
        lines.extend(["## 编译错误", ""])
        for item in payload["compile_errors"]:
            lines.append(f"- {item}")
        lines.append("")
    lines.extend(["## 场景", ""])
    for index, plan in enumerate(payload.get("plans", []), start=1):
        lines.extend(
            [
                f"### {index}. {plan.get('scenario_name', '')}",
                "",
                f"- 场景 ID：`{plan.get('scenario_id', '')}`",
                f"- 命令数：`{len(plan.get('commands', []))}`",
                f"- 断言数：`{len(plan.get('assertions', []))}`",
                "",
                "#### 命令",
            ]
        )
        for command in plan.get("commands", []):
            lines.append(f"- `{command.get('name', '')}`：`{command.get('cmdline', '')}`")
        lines.extend(["", "#### Feature 步骤"])
        for step in plan.get("feature_steps", []):
            lines.append(f"- {step}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile Cucumber feature into an offline executable plan.")
    parser.add_argument("--feature", default=str(DEFAULT_FEATURE))
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--env-file", default="", help="本地环境配置 JSON，默认读取 polaris.local.json，兼容 config/polaris_env.json")
    parser.add_argument("--step-registry", default=str(DEFAULT_STEP_REGISTRY))
    parser.add_argument("--action-registry", default=str(DEFAULT_ACTION_REGISTRY))
    parser.add_argument("--contracts", default=str(DEFAULT_CONTRACTS))
    parser.add_argument("--debug-root", default=str(DEFAULT_DEBUG_ROOT))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--wake-word", default="")
    parser.add_argument("--command-text", default="")
    parser.add_argument("--command-file", default="")
    parser.add_argument("--command-limit", default=None)
    parser.add_argument("--device-key", default=None)
    parser.add_argument("--observe-ms", default="")
    parser.add_argument("--wifi-ssid", default="")
    parser.add_argument("--wifi-password", default="")
    parser.add_argument("--recognition-timeout-s", type=int, default=None)
    parser.add_argument("--half-duplex-timeout-s", type=int, default=None)
    parser.add_argument("--full-duplex-timeout-s", type=int, default=None)
    args = parser.parse_args()

    path, payload = compile_feature(args)
    print(path)
    if payload.get("compile_errors"):
        return 2 if args.strict else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
