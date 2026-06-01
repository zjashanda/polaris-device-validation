#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the FA2 command actuator/beep expectation table.

This table is rule-based. It does not claim that a physical buzzer was heard;
it only tells the runner whether actuator/beep evidence is expected, optional,
or unknown for each command class.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_command_control_diagnosis import classify_command_kind  # noqa: E402


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def default_command_file() -> Path:
    docs_dir = WORKSPACE_ROOT / "docs"
    matches = sorted(docs_dir.glob("fa2*.txt"))
    if not matches:
        raise SystemExit("FA2 command file not found under docs/fa2*.txt")
    return matches[0]


def static_beep_expectation(command: str, kind: str) -> Dict[str, str]:
    if kind in {"query", "network_query_or_setup", "online_or_general"}:
        return {
            "beep_expectation": "not_required",
            "noop_expectation": "not_required",
            "assertion_policy": "do_not_require_actuator_or_beep",
            "notes": "查询/联网/媒体/通用在线类命令重点看识别和回复，不要求蜂鸣器。",
        }
    if kind == "unknown":
        return {
            "beep_expectation": "unknown_need_project_rule",
            "noop_expectation": "unknown_need_project_rule",
            "assertion_policy": "require_project_rule_before_fail",
            "notes": "命令类型无法静态归类，不能因为缺少蜂鸣器直接判失败。",
        }
    return {
        "beep_expectation": "expected_if_state_changes",
        "noop_expectation": "not_expected_if_noop",
        "assertion_policy": "split_recognition_control_tts_actuator",
        "notes": "控制类命令只有发生状态变化时才期望执行/蜂鸣反馈；no-op、拒绝、不支持时不强制蜂鸣。",
    }


def load_commands(path: Path) -> List[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def build_rows(command_file: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, command in enumerate(load_commands(command_file), start=1):
        kind = classify_command_kind(command)
        expectation = static_beep_expectation(command, kind)
        rows.append(
            {
                "index": index,
                "case_id": f"F{index:03d}",
                "command": command,
                "command_kind": kind,
                **expectation,
            }
        )
    return rows


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["index", "case_id", "command", "command_kind", "beep_expectation", "noop_expectation", "assertion_policy", "notes"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_md(payload: Dict[str, Any]) -> str:
    counts = payload.get("counts", {})
    lines = [
        "# FA2 命令蜂鸣器期望表",
        "",
        "本文件由 `build_fa2_beep_expectation_table.py` 生成，是规则期望表，不是物理蜂鸣器实测事实表。",
        "录音/声学回采未落地时，缺少蜂鸣器证据只能归为 `UNKNOWN` 或 `evidence_gap`，不能伪造成 PASS。",
        "",
        f"- 命令总数：`{payload.get('command_count')}`",
        f"- 来源：`{payload.get('source_file')}`",
        f"- 蜂鸣器期望分布：`{json.dumps(counts.get('beep_expectation', {}), ensure_ascii=False)}`",
        f"- 命令类型分布：`{json.dumps(counts.get('command_kind', {}), ensure_ascii=False)}`",
        "",
        "## 断言口径",
        "",
        "- `expected_if_state_changes`：控制类命令只有发生状态变化时才期望执行/蜂鸣反馈。",
        "- `not_expected_if_noop`：重复打开/重复关闭、已是当前模式等 no-op 场景不强制蜂鸣器。",
        "- `not_required`：查询、联网状态、媒体/问答等不要求执行机构或蜂鸣器。",
        "- `unknown_need_project_rule`：需要项目私有规则或更多日志 marker 后再提升为强断言。",
        "",
        "## 产物",
        "",
        f"- JSON：`{payload.get('json_output')}`",
        f"- CSV：`{payload.get('csv_output')}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build FA2 actuator/beep expectation table")
    parser.add_argument("--command-file", default="", help="Defaults to docs/fa2*.txt")
    parser.add_argument("--json-output", default="docs/wiki/voice-validation/fa2-command-beep-expectation.json")
    parser.add_argument("--csv-output", default="docs/wiki/voice-validation/fa2-command-beep-expectation.csv")
    parser.add_argument("--md-output", default="docs/wiki/voice-validation/fa2-command-beep-expectation.md")
    args = parser.parse_args()

    command_file = resolve_path(args.command_file) if args.command_file else default_command_file()
    rows = build_rows(command_file)
    counts = {
        "command_kind": dict(Counter(row["command_kind"] for row in rows)),
        "beep_expectation": dict(Counter(row["beep_expectation"] for row in rows)),
        "noop_expectation": dict(Counter(row["noop_expectation"] for row in rows)),
    }
    json_output = resolve_path(args.json_output)
    csv_output = resolve_path(args.csv_output)
    md_output = resolve_path(args.md_output)
    payload = {
        "schema": "polaris.fa2_command_beep_expectation.v1",
        "source_file": str(command_file),
        "command_count": len(rows),
        "counts": counts,
        "rows": rows,
        "json_output": str(json_output),
        "csv_output": str(csv_output),
    }
    write_json(json_output, payload)
    write_csv(csv_output, rows)
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_md(payload), encoding="utf-8")
    print(json_output)
    print(csv_output)
    print(md_output)
    print(f"command_count={len(rows)} counts={counts['beep_expectation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
