#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate BDD summaries against underlying evidence summaries.

This validator is intentionally evidence-only: it never replays audio and never
turns a missing oracle into a firmware failure. It catches script/reporting
inconsistencies such as a module summary being PASS while the BDD layer reports
test_artifact_missing/BLOCKED.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


BASE = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
ASR_WAKE_MARKER_RE = re.compile(r"(online_wakeup|offline[_ ]wakeup|line_wakeup)", re.I)


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def default_output_dir() -> Path:
    bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
    if bdd_run_dir:
        return Path(bdd_run_dir).resolve() / "attribution_validator"
    return BASE / "debug" / "attribution_validator" / datetime.now().strftime("%Y%m%d_%H%M%S")


def find_latest_path(run_dir: Path, names: Iterable[str]) -> Optional[Path]:
    candidates: List[Path] = []
    for name in names:
        candidates.extend(run_dir.glob(f"**/{name}"))
    existing = [item for item in candidates if item.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def module_summary_for(run_dir: Path, scenario_id: str) -> Optional[Path]:
    mapping = {
        "online_oneshot_matrix": ["oneshot_matrix_summary.json"],
        "offline_oneshot_matrix": ["oneshot_matrix_summary.json"],
        "wake_latency_smoke": ["wake_matrix_summary.json"],
        "continuous_wake_smoke": ["wake_matrix_summary.json"],
        "random_interval_wake_smoke": ["wake_matrix_summary.json"],
        "network_recovery_basic": ["network_recovery_summary.json"],
        "false_wake_quiet_basic": ["false_wake_quiet_summary.json"],
        "wake_interrupt": ["interrupt_injection_result.json"],
        "command_interrupt": ["interrupt_injection_result.json"],
        "interrupt_prerequisite_measurement": ["interrupt_prerequisite_measurement.json"],
    }
    names = mapping.get(scenario_id, [])
    return find_latest_path(run_dir, names) if names else None


def discover_run_dirs(scan_root: Path, latest_per_scenario: bool, limit: int) -> List[Path]:
    summaries = sorted(scan_root.glob("*/bdd_run_summary.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if latest_per_scenario:
        selected: Dict[str, Path] = {}
        for summary_path in summaries:
            payload = read_json(summary_path)
            for item in payload.get("scenario_results", []):
                scenario_id = str(item.get("scenario_id", ""))
                if scenario_id and scenario_id not in selected:
                    selected[scenario_id] = summary_path.parent
        dirs = list(selected.values())
    else:
        dirs = [path.parent for path in summaries]
    if limit > 0:
        return dirs[:limit]
    return dirs


def scan_asr_marker_false_fail(run_dir: Path, scenario_id: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for round_json in run_dir.glob("**/round.json"):
        payload = read_json(round_json)
        row = payload.get("row", {})
        reason = str(row.get("reason", ""))
        if row.get("result") != "FAIL" or "ASR_WAKE" not in reason:
            continue
        evidence_dir = Path(str(row.get("evidence_dir", "")))
        if not evidence_dir.is_absolute():
            evidence_dir = WORKSPACE_ROOT / evidence_dir
        lines = []
        for log_name in ("COM13.clean.log", "COM13.log"):
            path = evidence_dir / "window_logs" / log_name
            if path.exists():
                lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
        marker_lines = [line for line in lines if ASR_WAKE_MARKER_RE.search(line)]
        if marker_lines:
            findings.append(
                {
                    "severity": "ERROR",
                    "type": "SCRIPT_FALSE_FAIL_ASR_MARKER",
                    "scenario_id": scenario_id,
                    "run_dir": rel(run_dir),
                    "evidence": rel(round_json),
                    "reason": "轮次被判 ASR_WAKE 缺失，但 COM13 原始日志存在 ASR wake marker。",
                    "marker_excerpt": marker_lines[:3],
                }
            )
    return findings


def validate_run(run_dir: Path) -> Dict[str, Any]:
    bdd_path = run_dir / "bdd_run_summary.json"
    payload = read_json(bdd_path)
    findings: List[Dict[str, Any]] = []
    scenario_results = payload.get("scenario_results", [])
    if not scenario_results:
        findings.append(
            {
                "severity": "WARN",
                "type": "BDD_SUMMARY_EMPTY",
                "run_dir": rel(run_dir),
                "reason": "bdd_run_summary.json 无 scenario_results，无法复核。",
            }
        )
    for item in scenario_results:
        scenario_id = str(item.get("scenario_id", ""))
        bdd_result = str(item.get("result", ""))
        attribution = str(item.get("attribution", ""))
        module_path = module_summary_for(run_dir, scenario_id)
        if module_path:
            module = read_json(module_path)
            module_result = str(module.get("result", ""))
            if module_result and bdd_result and module_result != bdd_result:
                findings.append(
                    {
                        "severity": "ERROR",
                        "type": "BDD_MODULE_RESULT_MISMATCH",
                        "scenario_id": scenario_id,
                        "run_dir": rel(run_dir),
                        "bdd_result": bdd_result,
                        "module_result": module_result,
                        "bdd_evidence": rel(bdd_path),
                        "module_evidence": rel(module_path),
                        "reason": "BDD 汇总结果与模块 summary 不一致。",
                    }
                )
        elif attribution == "test_artifact_missing":
            findings.append(
                {
                    "severity": "ERROR",
                    "type": "MISSING_EXPECTED_ARTIFACT",
                    "scenario_id": scenario_id,
                    "run_dir": rel(run_dir),
                    "bdd_result": bdd_result,
                    "reason": "BDD 报 test_artifact_missing，且未找到对应模块 summary。",
                }
            )
        findings.extend(scan_asr_marker_false_fail(run_dir, scenario_id))
    return {"run_dir": rel(run_dir), "scenario_count": len(scenario_results), "findings": findings}


def write_findings_csv(path: Path, findings: List[Dict[str, Any]]) -> None:
    fields = ["severity", "type", "scenario_id", "run_dir", "bdd_result", "module_result", "reason", "evidence", "module_evidence"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for item in findings:
            writer.writerow(item)


def render_report(payload: Dict[str, Any]) -> str:
    lines = [
        "# Attribution Validator 报告",
        "",
        f"- 结论：`{payload.get('result')}`",
        f"- 归因：`{payload.get('attribution')}`",
        f"- 原因：{payload.get('reason')}",
        f"- 扫描 run 数：`{payload.get('run_count')}`",
        f"- findings：`{payload.get('finding_count')}`",
        "",
        "| severity | type | scenario | run | reason |",
        "|---|---|---|---|---|",
    ]
    for item in payload.get("findings", [])[:80]:
        reason = str(item.get("reason", "")).replace("|", "\\|")
        lines.append(f"| `{item.get('severity')}` | `{item.get('type')}` | `{item.get('scenario_id','')}` | `{item.get('run_dir')}` | {reason} |")
    if not payload.get("findings"):
        lines.append("| - | - | - | - | 未发现归因不一致 |")
    lines.extend(
        [
            "",
            "## 判定口径",
            "",
            "- BDD summary 与模块 summary 不一致，判为脚本/汇总问题。",
            "- BDD 报 test_artifact_missing 且对应模块 summary 缺失，判为测试产物缺失。",
            "- 原始 COM13 日志存在 `line_wakeup/offline wakeup/online_wakeup`，但轮次因 ASR_WAKE 缺失而 FAIL，判为脚本 marker 覆盖不足。",
            "- 需求/oracle 不明确时只标记 NEEDS_REVIEW，不直接归固件。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    scan_root = Path(args.scan_root).resolve()
    output_dir = (Path(args.output_dir) if args.output_dir else default_output_dir()).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dirs = discover_run_dirs(scan_root, latest_per_scenario=not args.include_history, limit=args.limit)
    results = [validate_run(path) for path in run_dirs]
    findings = [finding for item in results for finding in item["findings"]]
    error_count = sum(1 for item in findings if item.get("severity") == "ERROR")
    warn_count = sum(1 for item in findings if item.get("severity") == "WARN")
    if not run_dirs:
        result = "BLOCKED"
        attribution = "no_bdd_runs"
        reason = "未找到可复核的 bdd_run_summary.json。"
    elif error_count > 0:
        result = "FAIL"
        attribution = "attribution_inconsistency"
        reason = f"发现 {error_count} 个 ERROR 级归因/汇总不一致。"
    else:
        result = "PASS"
        attribution = "pass" if warn_count == 0 else "pass_with_warnings"
        reason = "未发现 ERROR 级归因不一致。" if warn_count == 0 else f"未发现 ERROR，存在 {warn_count} 个 WARN。"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "scan_root": rel(scan_root),
        "run_count": len(run_dirs),
        "finding_count": len(findings),
        "error_count": error_count,
        "warn_count": warn_count,
        "runs": results,
        "findings": findings,
    }
    write_json(output_dir / "attribution_validator_summary.json", payload)
    write_findings_csv(output_dir / "attribution_validator_findings.csv", findings)
    (output_dir / "attribution_validator_report.md").write_text(render_report(payload), encoding="utf-8")
    print(output_dir)
    print(json.dumps({"result": result, "attribution": attribution, "finding_count": len(findings)}, ensure_ascii=False))
    return 0 if result in {"PASS", "BLOCKED"} else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate BDD attribution against evidence summaries.")
    parser.add_argument("--scan-root", default=str(BASE / "debug" / "runs"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--include-history", action="store_true", help="Scan latest N runs, not only latest per scenario")
    parser.add_argument("--output-dir", default="")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
