#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate candidate regression cases from failed Polaris runs."""

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
DEFAULT_OUT_ROOT = BDD_ROOT / "debug" / "failure_cases"
NON_PASS = {"FAIL", "BLOCKED", "TIMING_AMBIGUOUS", "REQUIREMENT_REVIEW", "UNKNOWN", "ERROR"}


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_id(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_\-]+", "_", value or "case").strip("_")
    return text.lower() or "case"


def normalize_input_run(path: Path) -> Dict[str, Any]:
    record = load_json(path / "execution_record.json")
    attempts = record.get("attempts", []) if isinstance(record.get("attempts"), list) else []
    last_attempt = attempts[-1] if attempts else {}
    run_dir = resolve_path(str(last_attempt.get("run_dir", ""))) if last_attempt.get("run_dir") else path
    return {"input": rel(path), "optimized": bool(record), "execution_record": record, "run_dir": run_dir, "bdd_summary": load_json(run_dir / "bdd_run_summary.json"), "execution_plan": load_json(run_dir / "execution_plan.json")}


def scenario_plan_by_id(execution_plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in execution_plan.get("plans", []) if isinstance(execution_plan.get("plans"), list) else []:
        if isinstance(item, dict) and item.get("scenario_id"):
            result[str(item["scenario_id"])] = item
    return result


def failed_scenarios(run: Dict[str, Any], scenario_filter: str = "") -> List[Dict[str, Any]]:
    bdd = run.get("bdd_summary", {}) if isinstance(run.get("bdd_summary"), dict) else {}
    scenarios = bdd.get("scenario_results", []) if isinstance(bdd.get("scenario_results"), list) else []
    failed: List[Dict[str, Any]] = []
    for item in scenarios:
        if not isinstance(item, dict):
            continue
        result = str(item.get("result", "UNKNOWN") or "UNKNOWN").upper()
        sid = str(item.get("scenario_id", "") or "")
        if scenario_filter and sid != scenario_filter:
            continue
        if result in NON_PASS:
            failed.append(item)
    if failed or scenarios:
        return failed
    record = run.get("execution_record", {}) if isinstance(run.get("execution_record"), dict) else {}
    record_result = str(record.get("result", "UNKNOWN") or "UNKNOWN").upper()
    if record_result in NON_PASS:
        failed.append({"scenario_id": record.get("task_id", "preflight_or_task"), "scenario_name": record.get("task_id", "preflight/task"), "mapping_title": record.get("task_id", "preflight/task"), "result": record_result, "attribution": record.get("stability", ""), "reason": json.dumps(record.get("preflight", {}), ensure_ascii=False)[:1000], "metrics": {}})
    return failed


def infer_candidate_type(item: Dict[str, Any]) -> str:
    result = str(item.get("result", "") or "").upper()
    text = (str(item.get("attribution", "")) + " " + str(item.get("reason", ""))).lower()
    if result == "BLOCKED" or any(token in text for token in ["serial", "audio", "device_key", "precondition", "online", "cloud", "port"]):
        return "environment_precondition_guard"
    if result == "TIMING_AMBIGUOUS" or "timing" in text or "临界" in text:
        return "timing_boundary_regression"
    if any(token in text for token in ["media", "tts", "player"]):
        return "media_response_regression"
    if any(token in text for token in ["reboot", "crash", "watchdog"]):
        return "stability_regression"
    return "functional_regression"


def render_gherkin(case_id: str, scenario_id: str, title: str, item: Dict[str, Any], original_steps: List[Any]) -> str:
    lines = ["# language: zh-CN", f"@polaris @generated_regression @{safe_id(scenario_id)} @{safe_id(str(item.get('result', 'unknown')))}", "功能: Polaris 失败回归候选用例", f"  场景: {case_id} {title}", "    假如 使用当前 Polaris 本地串口配置", "    而且 使用默认播放声卡"]
    for step in original_steps:
        text = str(step).strip()
        if text:
            lines.append(f"    而且 原始步骤包含 \"{text}\"")
    lines += [f"    当 重新执行 `{scenario_id}` 对应的固定 task 或 scene", f"    那么 结果不应再次出现 `{item.get('result', 'UNKNOWN')}` 且归因为 `{item.get('attribution', '')}`", "    而且 若再次失败，应保留串口、声卡、云控、runtime replay 和 media oracle 证据"]
    return "\n".join(lines) + "\n"


def suggest_assertions(candidate_type: str) -> List[Dict[str, str]]:
    result = [{"target": "result_policy", "suggestion": "保留 PASS/FAIL/BLOCKED/TIMING_AMBIGUOUS 区分，不允许把前置缺失直接判固件 FAIL。"}]
    mapping = {
        "environment_precondition_guard": ("preflight", "把对应端口/声卡/联网/API 前置加入 constraint_engine 或 task preconditions。"),
        "timing_boundary_regression": ("timing_guard", "增加 timing_guard_ms 和注入窗口证据，临界窗口输出 TIMING_AMBIGUOUS。"),
        "media_response_regression": ("media_oracle", "要求 TTSStarted/MediaStarted/MediaCompleted 或明确 media_error marker。"),
        "stability_regression": ("state_coverage", "把 RebootDetected/CrashDetected/watchdog marker 加入 forbidden_event_types。"),
        "functional_regression": ("business_assertion", "补充该功能的关键 marker 或需求 oracle，避免只按 returncode 判定。"),
    }
    target, suggestion = mapping.get(candidate_type, mapping["functional_regression"])
    result.append({"target": target, "suggestion": suggestion})
    return result


def suggest_rules(candidate_type: str) -> List[Dict[str, str]]:
    mapping = {
        "media_response_regression": ("event_graph_rules", "新增或启用 ASR/Command -> TTS/Media 的项目私有因果边。"),
        "stability_regression": ("event_graph_rules", "新增 activity -> Reboot/Crash 的项目私有边，报告 boot reason/watchdog/crash marker。"),
        "environment_precondition_guard": ("constraint_engine", "新增环境 BLOCKED 指纹，不进入固件 FAIL。"),
    }
    target, suggestion = mapping.get(candidate_type, ("registry", "保持候选，不自动修改 registry；先复测确认。"))
    return [{"target": target, "suggestion": suggestion}]


def retest_checklist(candidate_type: str) -> List[str]:
    items = ["确认 polaris.local.json active_project、串口、声卡、Wi-Fi、云环境正确。", "执行前关闭 Xshell/串口助手/旧 logger，保证端口未占用。", "用 dry-run/plan-only 确认命令和 adapter flow 正确。", "真机 execute 时增加 --allow-side-effects --manage-session --runtime-strict。", "执行后运行 build_validation_summary_report.py 汇总证据。"]
    if candidate_type == "media_response_regression":
        items.append("额外运行 analyze_media_response_oracle.py，检查云端响应、播放器启动、完成和错误 marker。")
    if candidate_type == "timing_boundary_regression":
        items.append("复测时扩大 timing_guard_ms，避开唤醒播报和识别超时临界重叠。")
    return items


def build_candidate(run: Dict[str, Any], item: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    sid = str(item.get("scenario_id", "") or plan.get("scenario_id", "generated"))
    ctype = infer_candidate_type(item)
    title = str(item.get("mapping_title") or item.get("scenario_name") or plan.get("scenario_name") or sid)
    case_id = f"AUTO-{stamp()}-{safe_id(sid)}"
    steps = plan.get("feature_steps", []) if isinstance(plan.get("feature_steps"), list) else []
    return {"id": case_id, "scenario_id": sid, "title": title, "candidate_type": ctype, "source_run": run.get("input"), "run_dir": rel(run.get("run_dir")), "result": item.get("result", "UNKNOWN"), "attribution": item.get("attribution", ""), "reason": item.get("reason", ""), "metrics": item.get("metrics", {}), "original_steps": steps, "original_assertions": plan.get("assertions", []), "original_commands": plan.get("commands", []), "gherkin": render_gherkin(case_id, sid, title, item, steps), "suggested_assertion_updates": suggest_assertions(ctype), "suggested_rule_updates": suggest_rules(ctype), "retest_checklist": retest_checklist(ctype)}


def render_cases(candidates: List[Dict[str, Any]]) -> str:
    lines = ["# Polaris Failure-to-Test-Case 候选用例", ""]
    if not candidates:
        lines.append("- 未发现需要生成回归候选的失败场景。")
        return "\n".join(lines) + "\n"
    for cand in candidates:
        lines += [f"## {cand['id']} {cand['title']}", "", f"- source_run：`{cand['source_run']}`", f"- run_dir：`{cand['run_dir']}`", f"- result：`{cand['result']}`", f"- attribution：`{cand['attribution']}`", f"- candidate_type：`{cand['candidate_type']}`", f"- reason：{cand['reason']}", "", "```gherkin", cand["gherkin"].rstrip(), "```", ""]
    return "\n".join(lines) + "\n"


def render_suggestions(candidates: List[Dict[str, Any]]) -> str:
    lines = ["# Polaris 断言/规则补强建议", ""]
    for cand in candidates:
        lines += [f"## {cand['id']} {cand['title']}", "", "### Assertion 建议"]
        for item in cand.get("suggested_assertion_updates", []):
            lines.append(f"- `{item.get('target')}`：{item.get('suggestion')}")
        lines.append("\n### Rule 建议")
        for item in cand.get("suggested_rule_updates", []):
            lines.append(f"- `{item.get('target')}`：{item.get('suggestion')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_checklist(candidates: List[Dict[str, Any]]) -> str:
    lines = ["# Polaris 失败回归复测清单", ""]
    for cand in candidates:
        lines.append(f"## {cand['id']} {cand['title']}")
        for item in cand.get("retest_checklist", []):
            lines.append(f"- [ ] {item}")
        lines.append("")
    if not candidates:
        lines.append("- [ ] 无失败候选；无需生成专项复测。")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate candidate regression cases from a failed Polaris run.")
    parser.add_argument("--run", action="append", required=True)
    parser.add_argument("--scenario-id", default="")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()
    runs = [normalize_input_run(resolve_path(value)) for value in args.run]
    candidates: List[Dict[str, Any]] = []
    for run in runs:
        plans = scenario_plan_by_id(run.get("execution_plan", {}))
        for item in failed_scenarios(run, args.scenario_id):
            candidates.append(build_candidate(run, item, plans.get(str(item.get("scenario_id", "")), {})))
    out_dir = resolve_path(args.out_dir) if args.out_dir else DEFAULT_OUT_ROOT / stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    package = {"schema": "polaris.failure_to_test_case_package.v1", "generated_at": now_iso(), "runs": [run.get("input") for run in runs], "candidate_count": len(candidates), "candidates": candidates}
    write_json(out_dir / "failure_case_package.json", package)
    (out_dir / "candidate_cases.md").write_text(render_cases(candidates), encoding="utf-8")
    (out_dir / "suggested_registry_updates.md").write_text(render_suggestions(candidates), encoding="utf-8")
    (out_dir / "retest_checklist.md").write_text(render_checklist(candidates), encoding="utf-8")
    print(out_dir)
    print(f"candidate_count={len(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
