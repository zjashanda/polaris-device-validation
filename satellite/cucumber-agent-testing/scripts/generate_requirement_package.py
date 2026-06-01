#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a deterministic requirement package from Polaris validation packs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
WIKI_ROOT = WORKSPACE_ROOT / "docs" / "wiki" / "voice-validation"
PACK_ROOT = WIKI_ROOT / "packs"
PACK_INDEX = WIKI_ROOT / "validation-pack-index.json"
DEFAULT_OUT_ROOT = BDD_ROOT / "debug" / "requirement_packages"

KEYWORDS = {
    "first_wake": ["首次唤醒", "首唤", "冷启动唤醒", "wake"],
    "recognition_mode_wake": ["识别模式唤醒", "二次唤醒", "识别态唤醒", "识别窗口"],
    "half_duplex_recognition": ["半双工", "half duplex", "播报中不识别"],
    "full_duplex_recognition": ["全双工", "full duplex", "在线全双工", "连续对话", "播报中识别"],
    "basic_command_recognition": ["命令词", "基础命令", "指令识别", "离线命令"],
    "online_mixed_stress": ["在线压测", "混合压测", "音乐", "相声", "新闻", "百科", "炒菜", "问答"],
    "wake_interrupt": ["唤醒打断", "自播中唤醒", "播报中唤醒"],
    "command_interrupt": ["识别打断", "命令打断", "自播中识别", "播报中命令"],
    "network_recovery_basic": ["联网恢复", "断网", "热点", "重新联网"],
    "offline_oneshot_matrix": ["离线 one-shot", "离线oneshot", "离线一口气"],
    "online_oneshot_matrix": ["在线 one-shot", "在线oneshot", "在线一口气"],
    "online_vad_special_smoke": ["在线 vad", "VAD", "停顿", "截断"],
    "false_wake": ["误唤醒", "误识别", "反集", "静默", "白噪声", "人声干扰"],
}

PACK_FILES = {
    "first_wake": "first-wake.md",
    "recognition_mode_wake": "recognition-mode-wake.md",
    "half_duplex_recognition": "half-duplex.md",
    "full_duplex_recognition": "online-full-duplex.md",
    "basic_command_recognition": "basic-command.md",
    "online_mixed_stress": "online-mixed-stress.md",
    "wake_interrupt": "half-duplex.md",
    "command_interrupt": "half-duplex.md",
    "network_recovery_basic": "online-mixed-stress.md",
    "offline_oneshot_matrix": "basic-command.md",
    "online_oneshot_matrix": "online-mixed-stress.md",
    "wake_latency_smoke": "first-wake.md",
    "continuous_wake_smoke": "first-wake.md",
    "random_interval_wake_smoke": "first-wake.md",
    "online_vad_special_smoke": "online-mixed-stress.md",
    "false_wake": "false-wake.md",
    "false_wake_quiet_basic": "false-wake.md",
    "false_wake_human_speech_smoke": "false-wake.md",
    "false_wake_white_noise_smoke": "false-wake.md",
}

TASK_BY_ENTRY = {
    "first_wake": ["satellite/cucumber-agent-testing/tasks/examples/first_wake.example.json"],
    "recognition_mode_wake": ["satellite/cucumber-agent-testing/tasks/examples/recognition_mode_wake.example.json"],
    "half_duplex_recognition": ["satellite/cucumber-agent-testing/tasks/examples/half_duplex.example.json"],
    "full_duplex_recognition": [
        "satellite/cucumber-agent-testing/tasks/examples/online_full_duplex.example.json",
        "satellite/cucumber-agent-testing/tasks/examples/online_full_duplex.continuous.example.json",
        "satellite/cucumber-agent-testing/tasks/examples/online_full_duplex.media_interrupt.example.json",
        "satellite/cucumber-agent-testing/tasks/examples/online_full_duplex.timeout_boundary.example.json",
        "satellite/cucumber-agent-testing/tasks/examples/online_full_duplex.exception_matrix.example.json",
        "satellite/cucumber-agent-testing/tasks/examples/online_full_duplex.random_stress.example.json",
    ],
    "basic_command_recognition": ["satellite/cucumber-agent-testing/tasks/examples/basic_command.example.json"],
    "online_mixed_stress": ["satellite/cucumber-agent-testing/tasks/examples/online_mixed_stress.example.json"],
    "wake_interrupt": ["satellite/cucumber-agent-testing/tasks/examples/wake_interrupt.example.json"],
    "command_interrupt": ["satellite/cucumber-agent-testing/tasks/examples/command_interrupt.example.json"],
    "network_recovery_basic": ["satellite/cucumber-agent-testing/tasks/examples/network_recovery_basic.example.json"],
    "offline_oneshot_matrix": ["satellite/cucumber-agent-testing/tasks/examples/offline_oneshot_matrix.example.json"],
    "online_oneshot_matrix": ["satellite/cucumber-agent-testing/tasks/examples/online_oneshot_matrix.example.json"],
    "wake_latency_smoke": ["satellite/cucumber-agent-testing/tasks/examples/wake_latency.example.json"],
    "continuous_wake_smoke": ["satellite/cucumber-agent-testing/tasks/examples/continuous_wake.example.json"],
    "random_interval_wake_smoke": ["satellite/cucumber-agent-testing/tasks/examples/random_interval_wake.example.json"],
    "online_vad_special_smoke": ["satellite/cucumber-agent-testing/tasks/examples/online_vad.example.json"],
    "false_wake": ["satellite/cucumber-agent-testing/tasks/examples/false_wake_quiet.example.json", "satellite/cucumber-agent-testing/tasks/examples/false_wake_human_speech.example.json", "satellite/cucumber-agent-testing/tasks/examples/false_wake_white_noise.example.json"],
    "false_wake_quiet_basic": ["satellite/cucumber-agent-testing/tasks/examples/false_wake_quiet.example.json"],
    "false_wake_human_speech_smoke": ["satellite/cucumber-agent-testing/tasks/examples/false_wake_human_speech.example.json"],
    "false_wake_white_noise_smoke": ["satellite/cucumber-agent-testing/tasks/examples/false_wake_white_noise.example.json"],
    "attribution_validator_smoke": ["satellite/cucumber-agent-testing/tasks/examples/attribution_validator.example.json"],
}

SCENE_BY_ENTRY = {
    "full_duplex_recognition": ["satellite/cucumber-agent-testing/references/scenes/online_full_duplex_fd002_fd012.scene.example.json"],
    "recognition_mode_wake": ["satellite/cucumber-agent-testing/references/scenes/l1_voice_core_supported_smoke.scene.example.json"],
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace") if path.exists() else ""


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def entry_alias(entry: str) -> str:
    text = str(entry or "").split("/")[0].split("+")[0].strip()
    aliases = {"command latency profile": "wake_latency_smoke"}
    return aliases.get(text, text)


def collect_entries() -> List[Dict[str, Any]]:
    payload = read_json(PACK_INDEX, {})
    entries: List[Dict[str, Any]] = []
    for category, items in (payload.get("categories", {}) if isinstance(payload, dict) else {}).items():
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                entry = entry_alias(str(item.get("current_entry", "") or ""))
                entries.append({"category": category, **item, "entry_id": entry})
    entries.extend([
        {"category": "在线识别", "name": "在线全双工 FD-002~FD-012", "support_level": "L1", "entry_id": "full_duplex_recognition", "gap": "已有全量 scene；真机 execute 需允许云控/媒体副作用。"},
        {"category": "稳定性", "name": "在线混合交互压测", "support_level": "L1", "entry_id": "online_mixed_stress", "gap": "已有随机策略；长时间压测需保留设备和日志。"},
        {"category": "误唤醒", "name": "误唤醒/误识别基础验证", "support_level": "L1", "entry_id": "false_wake", "gap": "静默、人声、白噪声 smoke 可执行；正式声学场景需语料和阈值。"},
        {"category": "在线识别", "name": "在线 VAD 专项", "support_level": "L1", "entry_id": "online_vad_special_smoke", "gap": "可探索性验证；正式截断容差需需求阈值。"},
    ])
    return entries


def score_entry(requirement: str, entry: Dict[str, Any]) -> int:
    req = normalize(requirement)
    entry_id = str(entry.get("entry_id", "") or "")
    hay = normalize(" ".join(str(entry.get(key, "")) for key in ["name", "entry_id", "gap", "category", "type", "current_entry"]))
    score = 0
    name = normalize(entry.get("name", ""))
    if name and name in req:
        score += 120
    if entry_id and normalize(entry_id) in req:
        score += 80
    for key, words in KEYWORDS.items():
        if entry_id == key or key in hay:
            score += sum(35 for word in words if normalize(word) in req)
    for token in re.split(r"[\s,，。；;、/]+", req):
        if len(token) >= 2 and token in hay:
            score += 6
    return score


def choose_packs(requirement: str, max_packs: int) -> List[Dict[str, Any]]:
    scored = [(score_entry(requirement, item), item) for item in collect_entries()]
    scored = [(score, item) for score, item in scored if score > 0]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    selected: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for score, item in scored:
        entry = entry_alias(str(item.get("entry_id") or item.get("current_entry") or ""))
        if not entry or entry in seen:
            continue
        selected.append({"score": score, **item, "entry_id": entry})
        seen.add(entry)
        if len(selected) >= max_packs:
            break
    return selected


def extract_case_table(pack_text: str) -> List[str]:
    lines = pack_text.splitlines()
    table: List[str] = []
    in_table = False
    for line in lines:
        if re.search(r"用例矩阵|测试矩阵|场景矩阵|Case Matrix", line, re.I):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if in_table and line.strip().startswith("|"):
            table.append(line)
    return table


def task_commands(tasks: List[str], env_file: str) -> List[Dict[str, str]]:
    return [{
        "task": task,
        "dry_run": f"python satellite/cucumber-agent-testing/scripts/run_optimized_task.py --task {task} --mode dry-run --env-file {env_file}",
        "execute": f"python satellite/cucumber-agent-testing/scripts/run_optimized_task.py --task {task} --mode execute --env-file {env_file} --allow-side-effects --manage-session --runtime-strict",
    } for task in tasks]


def build_run_plan(selected: List[Dict[str, Any]], env_file: str, project: str, mode: str) -> Dict[str, Any]:
    items = []
    for item in selected:
        entry = str(item.get("entry_id", ""))
        tasks = TASK_BY_ENTRY.get(entry, [])
        scenes = SCENE_BY_ENTRY.get(entry, [])
        items.append({
            "entry_id": entry,
            "name": item.get("name", ""),
            "support_level": item.get("support_level", ""),
            "tasks": tasks,
            "scenes": scenes,
            "commands": task_commands(tasks, env_file),
            "scene_commands": [{
                "scene": scene,
                "dry_run": f"python satellite/cucumber-agent-testing/scripts/run_kernel_scene.py --scene {scene} --env-file {env_file} --mode dry-run --execute-runner --emit-ir-bundle",
                "execute": f"python satellite/cucumber-agent-testing/scripts/run_kernel_scene.py --scene {scene} --env-file {env_file} --mode execute --execute-runner --allow-side-effects --manage-session --runtime-strict --emit-ir-bundle",
            } for scene in scenes],
        })
    return {"schema": "polaris.requirement_run_plan.v1", "generated_at": now_iso(), "project": project, "env_file": env_file, "default_mode": mode, "items": items}


def render_plan(requirement: str, selected: List[Dict[str, Any]], run_plan: Dict[str, Any]) -> str:
    lines = ["# Polaris 需求验证方案草案", "", f"- 生成时间：`{now_iso()}`", f"- 需求：`{requirement}`", f"- 项目：`{run_plan.get('project') or '按 env_file active_project'}`", f"- 环境文件：`{run_plan.get('env_file')}`", "", "## 命中的验证包", "", "| 验证包/测试项 | 等级 | 入口/tag | 匹配分 | 当前缺口 |", "| --- | --- | --- | ---: | --- |"]
    for item in selected:
        lines.append(f"| {item.get('name','')} | {item.get('support_level','')} | `{item.get('entry_id','')}` | {item.get('score','')} | {item.get('gap','')} |")
    lines += ["", "## 执行原则", "", "- 执行前确认 active_project、串口、声卡、Wi-Fi 和云环境。", "- 已有验证包优先复用，执行阶段不依赖大模型临时生成脚本。", "- 真机执行必须显式增加 `--allow-side-effects` 并保留 debug 证据。", "- 结论按 `PASS/FAIL/BLOCKED/TIMING_AMBIGUOUS/REQUIREMENT_REVIEW` 归因。", "", "## 推荐入口", ""]
    for item in run_plan.get("items", []):
        lines.append(f"### {item.get('name')} (`{item.get('entry_id')}`)")
        for command in item.get("commands", []):
            lines.append(f"- dry-run：`{command['dry_run']}`")
            lines.append(f"- execute：`{command['execute']}`")
        for command in item.get("scene_commands", []):
            lines.append(f"- scene dry-run：`{command['dry_run']}`")
            lines.append(f"- scene execute：`{command['execute']}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_cases(selected: List[Dict[str, Any]]) -> str:
    lines = ["# Polaris 用例矩阵草案", ""]
    for item in selected:
        entry = str(item.get("entry_id", ""))
        pack_text = read_text(PACK_ROOT / PACK_FILES.get(entry, "")) if PACK_FILES.get(entry) else ""
        table = extract_case_table(pack_text)
        lines += [f"## {item.get('name','')} (`{entry}`)", ""]
        if table:
            lines += table + [""]
        else:
            status = "已有关联 task" if TASK_BY_ENTRY.get(entry) else "需补 task"
            lines += ["| ID | 类型 | 目标 | 前置 | 核心断言 | 自动化状态 |", "| --- | --- | --- | --- | --- | --- |", f"| AUTO-001 | smoke | {item.get('name','')} | 按项目配置完成前置 | 参考 assertion/runtime/media oracle | {status} |", ""]
    return "\n".join(lines)


def render_gaps(selected: List[Dict[str, Any]]) -> str:
    lines = ["# Polaris 缺口清单草案", "", "| 测试项 | 等级 | 缺口 | 建议处理 |", "| --- | --- | --- | --- |"]
    for item in selected:
        level = str(item.get("support_level", ""))
        if level.startswith("L0"):
            action = "可直接 precheck/dry-run/execute；按需补正式阈值。"
        elif level.startswith("L1"):
            action = "可 smoke/探索执行；正式结论需轮次、阈值或 oracle。"
        elif level.startswith("L2"):
            action = "先补语料、阈值、API 或配置入口，再进入正式执行。"
        else:
            action = "需要物理 rig、标准数据集或人工场景。"
        lines.append(f"| {item.get('name','')} | {level} | {item.get('gap','')} | {action} |")
    return "\n".join(lines) + "\n"


def render_confirmation(selected: List[Dict[str, Any]], run_plan: Dict[str, Any]) -> str:
    lines = ["# Polaris 执行确认单", "", "执行前请确认：", "", f"- 使用设备/项目：`{run_plan.get('project') or '按 env_file active_project'}`。", f"- 环境文件：`{run_plan.get('env_file')}`。", "- 是否允许真机副作用：串口占用、声卡播放、云控设置、上下电、断网/联网。", "- 是否全量执行，还是先执行 smoke。", "- 是否允许修改设备状态：半/全双工、音量、夜间模式、网络、PA。", "", "## 推荐任务入口", ""]
    paths: List[str] = []
    for item in selected:
        paths.extend(TASK_BY_ENTRY.get(str(item.get("entry_id", "")), []))
    lines += [f"- `{task}`" for task in sorted(set(paths))] if paths else ["- 暂无直接 task；需先补 registry/task/runtime 后执行。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Polaris requirement package from local validation packs.")
    parser.add_argument("--requirement", default="")
    parser.add_argument("--requirement-file", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--max-packs", type=int, default=5)
    parser.add_argument("--project", default="")
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--mode", choices=["plan-only", "dry-run", "execute"], default="dry-run")
    args = parser.parse_args()
    requirement = args.requirement.strip()
    if args.requirement_file:
        requirement = (requirement + "\n" + read_text(resolve_path(args.requirement_file))).strip()
    if not requirement:
        raise SystemExit("请提供 --requirement 或 --requirement-file")
    selected = choose_packs(requirement, max(1, args.max_packs))
    run_plan = build_run_plan(selected, args.env_file, args.project, args.mode)
    out_dir = resolve_path(args.out_dir) if args.out_dir else DEFAULT_OUT_ROOT / stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "requirement_package.json", {"schema": "polaris.requirement_package.v1", "requirement": requirement, "selected": selected, "run_plan": run_plan})
    write_json(out_dir / "run_plan.json", run_plan)
    (out_dir / "test_plan.md").write_text(render_plan(requirement, selected, run_plan), encoding="utf-8")
    (out_dir / "case_matrix.md").write_text(render_cases(selected), encoding="utf-8")
    (out_dir / "gap_list.md").write_text(render_gaps(selected), encoding="utf-8")
    (out_dir / "confirmation.md").write_text(render_confirmation(selected, run_plan), encoding="utf-8")
    print(out_dir)
    print(f"matched_packs={len(selected)}")
    for item in selected:
        print(f"- {item.get('name')} -> {item.get('entry_id')} score={item.get('score')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
