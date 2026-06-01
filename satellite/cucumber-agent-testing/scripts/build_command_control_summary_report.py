#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a compact report for command-control diagnosis and FA2 aggregates."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_OUT_ROOT = BDD_ROOT / "debug" / "reports"


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_input(path: Path) -> Dict[str, Any]:
    if path.is_dir():
        path = path / "summary.json"
    payload = load_json(path)
    if payload.get("schema") == "polaris.command-control-diagnosis.summary.v1":
        results = payload.get("results", []) if isinstance(payload.get("results"), list) else []
        result_counts = Counter(str(item.get("result") or "UNKNOWN") for item in results)
        attr_counts = Counter(str(item.get("attribution") or "") for item in results)
        tts_chain = sum(1 for item in results if str(item.get("attribution")) == "tts_response_chain")
        return {
            "kind": "diagnosis_run",
            "input": rel(path),
            "project_id": payload.get("project_id", ""),
            "run_dir": payload.get("run_dir", ""),
            "result": payload.get("result", ""),
            "result_counts": dict(result_counts),
            "attribution_counts": dict(attr_counts),
            "serial_coverage": payload.get("serial_coverage", {}),
            "tts_response_chain_count": tts_chain,
            "total_cases": len(results),
        }
    # FA2 final aggregate shape: top-level project ids.
    projects = []
    for project_id, item in payload.items():
        if not isinstance(item, dict):
            continue
        projects.append(
            {
                "project_id": project_id,
                "baseline_run": item.get("baseline_run", ""),
                "baseline_counts": item.get("baseline_counts", {}),
                "baseline_attr_counts": item.get("baseline_attr_counts", {}),
                "baseline_fail_rechecked": item.get("baseline_fail_rechecked", 0),
                "final_stable_fail_count": item.get("final_stable_fail_count", 0),
                "recovered_after_recheck_or_oracle_update": item.get("recovered_after_recheck_or_oracle_update", 0),
                "final_stable_failures": item.get("final_stable_failures", []),
            }
        )
    return {"kind": "fa2_aggregate", "input": rel(path), "projects": projects}


def build_report(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema": "polaris.command_control_summary_report.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_inputs": len(items),
        "items": items,
    }


def short(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "/")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render_md(report: Dict[str, Any]) -> str:
    lines = [
        "# 命令控制链路总报告",
        "",
        f"- 生成时间：`{report.get('generated_at')}`",
        f"- 输入数量：`{report.get('total_inputs')}`",
        "",
    ]
    for item in report.get("items", []):
        if item.get("kind") == "diagnosis_run":
            coverage = item.get("serial_coverage", {}) if isinstance(item.get("serial_coverage"), dict) else {}
            lines += [
                f"## Run: {item.get('project_id') or 'unknown'}",
                "",
                f"- 输入：`{item.get('input')}`",
                f"- Run dir：`{item.get('run_dir')}`",
                f"- 总结果：`{item.get('result')}`，用例数：`{item.get('total_cases')}`",
                f"- 结果分布：`{json.dumps(item.get('result_counts', {}), ensure_ascii=False)}`",
                f"- 归因分布：`{json.dumps(item.get('attribution_counts', {}), ensure_ascii=False)}`",
                f"- 串口覆盖：`{coverage.get('status', '')}` - {short(coverage.get('reason', ''))}",
                f"- TTS 空 URL/播报链路告警数：`{item.get('tts_response_chain_count')}`",
                "",
            ]
        elif item.get("kind") == "fa2_aggregate":
            lines += ["## FA2 聚合", "", f"- 输入：`{item.get('input')}`", "", "| 项目 | baseline | 复验数 | 稳定 FAIL | 恢复数 |", "| --- | --- | ---: | ---: | ---: |"]
            for project in item.get("projects", []):
                lines.append(
                    f"| `{project.get('project_id')}` | `{json.dumps(project.get('baseline_counts', {}), ensure_ascii=False)}` | {project.get('baseline_fail_rechecked')} | {project.get('final_stable_fail_count')} | {project.get('recovered_after_recheck_or_oracle_update')} |"
                )
            for project in item.get("projects", []):
                failures = project.get("final_stable_failures", []) if isinstance(project.get("final_stable_failures"), list) else []
                if failures:
                    lines += ["", f"### {project.get('project_id')} 稳定失败", ""]
                    for failure in failures:
                        lines.append(f"- `{failure.get('case_id')}` `{failure.get('command')}`：{short(failure.get('final_reason'))}")
            lines.append("")
    lines += [
        "## 判定提醒",
        "",
        "- `COVERAGE_DEGRADED` 说明有配置串口未打开，只能给降级覆盖结论。",
        "- `tts_response_chain` 说明识别/控制可能已有证据，但 TTS/媒体播报未闭环，需要单独分析。",
        "- 没有明确 beep/buzzer/蜂鸣器 marker 或人工/声学证据时，不声称物理蜂鸣器已响。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build command-control summary report")
    parser.add_argument("--input", action="append", default=[], help="summary.json, run dir, or FA2 aggregate json")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()
    if not args.input:
        raise SystemExit("请至少提供一个 --input")
    items = [normalize_input(resolve_path(value)) for value in args.input]
    report = build_report(items)
    out_dir = resolve_path(args.out_dir) if args.out_dir else DEFAULT_OUT_ROOT / f"command_control_{stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "command_control_summary_report.json", report)
    (out_dir / "command_control_summary_report.md").write_text(render_md(report), encoding="utf-8")
    print(out_dir)
    print(f"total_inputs={report['total_inputs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
