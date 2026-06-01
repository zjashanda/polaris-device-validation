#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 Cucumber run_dir 做离线证据复核。

它不重新执行真机动作，只读取 run_dir 中已经落盘的原始/半原始证据：
- bdd_run_summary.json
- probe_summary.json
- doc case judge.json
- fa2_command_batch_summary.json

目标是回答：报告里的 PASS/FAIL/BLOCKED 是否有本地证据支撑。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

try:
    from tools.execution.polaris_doc_case_runner import (
        collect_dialog_behavior_metrics,
        read_clean_logs_from_execution,
    )
except Exception:  # pragma: no cover - validator still works with judge-only evidence.
    collect_dialog_behavior_metrics = None
    read_clean_logs_from_execution = None


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def resolve_evidence_path(run_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    direct = WORKSPACE_ROOT / path
    if direct.exists():
        return direct
    return run_dir / path


def int_metric(metrics: Dict[str, Any], key: str) -> int:
    try:
        return int(metrics.get(key, 0) or 0)
    except Exception:
        return 0


def count_expectation_pass(actual: int, expected: Any) -> Optional[bool]:
    text = str(expected)
    match = re.fullmatch(r"\s*(>=|<=|>|<|==?)\s*(-?\d+)\s*", text)
    if not match:
        return None
    op, raw_value = match.groups()
    value = int(raw_value)
    if op == ">=":
        return actual >= value
    if op == "<=":
        return actual <= value
    if op == ">":
        return actual > value
    if op == "<":
        return actual < value
    return actual == value


def recompute_doc_case_dialog(evidence_path: Path) -> Dict[str, Any]:
    if collect_dialog_behavior_metrics is None or read_clean_logs_from_execution is None:
        return {}
    logs = read_clean_logs_from_execution(evidence_path.parent)
    behavior = collect_dialog_behavior_metrics(logs)
    return {
        "successful_response_count": len(behavior.get("successful_response_records", [])),
        "audio_broadcast_count": len(behavior.get("audio_broadcast_records", [])),
        "asr_invalid_broadcast_count": len(behavior.get("asr_invalid_records", [])),
        "timeout_audio_count": len(behavior.get("timeout_audio_ids", [])),
    }


def validate_first_wake(evidence_path: Path, expected_result: str) -> Dict[str, Any]:
    payload = load_json(evidence_path)
    playback_codes: List[int] = []
    metrics: Dict[str, Any] = {
        "cp_wake_count": 0,
        "ap_wake_count": 0,
        "asr_wake_count": 0,
        "line_count": 0,
    }
    for step in payload.get("steps", []):
        playback = step.get("playback", {})
        if "returncode" in playback:
            try:
                playback_codes.append(int(playback.get("returncode")))
            except Exception:
                playback_codes.append(-1)
        step_metrics = step.get("metrics", {})
        metrics["cp_wake_count"] += int_metric(step_metrics, "cp_wake_count")
        metrics["ap_wake_count"] += int_metric(step_metrics, "ap_wake_count")
        metrics["asr_wake_count"] += int_metric(step_metrics, "wb_wake_count") + int_metric(step_metrics, "wb_online_wake_count")
        for value in step.get("window_summary", {}).get("line_counts", {}).values():
            try:
                metrics["line_count"] += int(value)
            except Exception:
                pass
    playback_ok = bool(playback_codes) and all(code == 0 for code in playback_codes)
    evidence_result = "PASS" if playback_ok and metrics["cp_wake_count"] >= 1 and metrics["ap_wake_count"] >= 1 and metrics["asr_wake_count"] >= 1 else "FAIL"
    return {
        "validator_result": "PASS" if evidence_result == expected_result else "FAIL",
        "evidence_result": evidence_result,
        "reason": f"playback={playback_codes}, CP={metrics['cp_wake_count']}, AP={metrics['ap_wake_count']}, ASR={metrics['asr_wake_count']}",
        "metrics": metrics,
    }


def validate_doc_case(evidence_path: Path, expected_result: str) -> Dict[str, Any]:
    judge = load_json(evidence_path)
    judge_result = str(judge.get("result", "UNKNOWN") or "UNKNOWN")
    checks = judge.get("checks", [])
    failed_checks = [item for item in checks if item.get("passed") is False]
    recomputed_dialog = recompute_doc_case_dialog(evidence_path)
    adjusted_failed_checks = failed_checks
    stale_parser_note = ""
    if recomputed_dialog:
        stale_names = set()
        for item in failed_checks:
            if item.get("name") != "successful_response_count":
                continue
            raw_count = int(recomputed_dialog.get("successful_response_count", 0) or 0)
            raw_pass = count_expectation_pass(raw_count, item.get("expected"))
            if raw_pass is True:
                stale_names.add(item.get("name"))
        if stale_names:
            adjusted_failed_checks = [item for item in failed_checks if item.get("name") not in stale_names]
            stale_parser_note = (
                "原 judge 的 successful_response_count 与原始日志重算不一致，"
                f"raw_successful_response_count={recomputed_dialog.get('successful_response_count')}；"
            )
    effective_judge_result = judge_result
    if judge_result == "FAIL" and failed_checks and not adjusted_failed_checks:
        effective_judge_result = "PASS"
    if judge_result == "PASS":
        evidence_ok = not failed_checks
        reason = "judge PASS 且 checks 全部通过。" if evidence_ok else f"judge PASS 但存在失败 checks：{[x.get('name') for x in failed_checks]}"
    elif judge_result in {"FAIL", "BLOCKED"}:
        evidence_ok = bool(judge.get("reason")) or bool(failed_checks)
        reason = stale_parser_note + (str(judge.get("reason", "")) or f"存在失败 checks：{[x.get('name') for x in failed_checks]}")
    else:
        evidence_ok = False
        reason = f"未知 judge result={judge_result}"
    return {
        "validator_result": "PASS" if evidence_ok and effective_judge_result == expected_result else "FAIL",
        "evidence_result": effective_judge_result,
        "judge_result": judge_result,
        "reason": reason,
        "failed_checks": adjusted_failed_checks,
        "judge_failed_checks": failed_checks,
        "recomputed_dialog": recomputed_dialog,
        "metrics": judge.get("metrics", {}),
    }


def validate_fa2_batch(evidence_path: Path, expected_result: str) -> Dict[str, Any]:
    summary = load_json(evidence_path)
    counts = summary.get("counts", {})
    total = int(summary.get("total", 0) or 0)
    playback = summary.get("playback_returncode")
    pass_count = int(counts.get("PASS", 0) or 0)
    fail_count = int(counts.get("FAIL", 0) or 0)
    blocked_count = int(counts.get("BLOCKED", 0) or 0)
    if playback != 0:
        evidence_result = "BLOCKED"
        reason = f"播放失败 returncode={playback}"
    elif total > 0 and pass_count == total and fail_count == 0 and blocked_count == 0:
        evidence_result = "PASS"
        reason = f"{total} 条命令均 PASS。"
    elif fail_count > 0:
        evidence_result = "FAIL"
        reason = f"存在失败命令：{counts}"
    else:
        evidence_result = "BLOCKED"
        reason = f"存在阻塞命令：{counts}"
    return {
        "validator_result": "PASS" if evidence_result == expected_result else "FAIL",
        "evidence_result": evidence_result,
        "reason": reason,
        "metrics": {
            "counts": counts,
            "total": total,
            "playback_returncode": playback,
        },
    }


def validate_scenario(run_dir: Path, item: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = str(item.get("scenario_id", ""))
    expected_result = str(item.get("result", "UNKNOWN") or "UNKNOWN")
    raw_evidence = str(item.get("evidence_path", ""))
    evidence_path = resolve_evidence_path(run_dir, raw_evidence)
    base = {
        "scenario_id": scenario_id,
        "scenario_name": item.get("scenario_name", item.get("mapping_title", "")),
        "reported_result": expected_result,
        "evidence_path": rel(evidence_path),
    }
    if not raw_evidence or not evidence_path.exists():
        base.update(
            {
                "validator_result": "FAIL",
                "evidence_result": "MISSING",
                "reason": f"证据文件不存在：{raw_evidence}",
            }
        )
        return base
    try:
        if evidence_path.name == "probe_summary.json" or scenario_id == "first_wake":
            detail = validate_first_wake(evidence_path, expected_result)
        elif evidence_path.name == "fa2_command_batch_summary.json" or scenario_id == "basic_command_recognition":
            detail = validate_fa2_batch(evidence_path, expected_result)
        elif evidence_path.name == "judge.json":
            detail = validate_doc_case(evidence_path, expected_result)
        else:
            detail = {
                "validator_result": "PASS",
                "evidence_result": expected_result,
                "reason": "证据文件存在，但没有专用 validator，仅做存在性校验。",
            }
    except Exception as exc:
        detail = {
            "validator_result": "FAIL",
            "evidence_result": "ERROR",
            "reason": f"解析证据失败：{exc}",
        }
    base.update(detail)
    return base


def render_report(payload: Dict[str, Any]) -> str:
    lines = [
        "# Cucumber 证据复核报告",
        "",
        f"- 运行目录：`{payload.get('run_dir', '')}`",
        f"- 生成时间：`{payload.get('generated_at', '')}`",
        f"- 汇总：`{json.dumps(payload.get('counts', {}), ensure_ascii=False)}`",
        "",
        "| 场景 | 报告结果 | 证据复核 | 证据推导结果 | 原因 | 证据 |",
        "|---|---|---|---|---|---|",
    ]
    for item in payload.get("scenarios", []):
        reason = str(item.get("reason", "")).replace("|", "\\|").replace("\n", " ")
        if len(reason) > 100:
            reason = reason[:97] + "..."
        lines.append(
            f"| {item.get('scenario_name') or item.get('scenario_id')} | `{item.get('reported_result')}` | `{item.get('validator_result')}` | `{item.get('evidence_result')}` | {reason} | `{item.get('evidence_path')}` |"
        )
    lines.extend(
        [
            "",
            "## 复核说明",
            "",
            "- `validator_result=PASS` 表示报告结果与本地证据文件可相互支撑。",
            "- `validator_result=FAIL` 表示证据缺失、解析失败，或报告结果与证据推导不一致。",
            "- 该工具不重新执行真机动作，只用于复核已落盘证据。",
        ]
    )
    return "\n".join(lines)


def validate_run(run_dir: Path) -> Dict[str, Any]:
    bdd_summary_path = run_dir / "bdd_run_summary.json"
    if not bdd_summary_path.exists():
        raise SystemExit(f"缺少 bdd_run_summary.json: {bdd_summary_path}")
    summary = load_json(bdd_summary_path)
    scenarios = [validate_scenario(run_dir, item) for item in summary.get("scenario_results", [])]
    counts: Dict[str, int] = {}
    for item in scenarios:
        key = str(item.get("validator_result", "UNKNOWN"))
        counts[key] = counts.get(key, 0) + 1
    payload = {
        "status": "DONE",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": rel(run_dir),
        "counts": counts,
        "scenarios": scenarios,
    }
    write_json(run_dir / "evidence_validation_summary.json", payload)
    (run_dir / "evidence_validation_report.md").write_text(render_report(payload), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate local evidence for a Polaris Cucumber run directory.")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    payload = validate_run(run_dir)
    print(run_dir / "evidence_validation_report.md")
    return 0 if payload.get("counts", {}).get("FAIL", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
