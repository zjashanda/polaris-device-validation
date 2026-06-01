#!/usr/bin/env python3
"""Monitor quiet environment for false wake events."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


BASE = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.core.polaris_config import add_canonical_log_aliases, configured_log_ports
from tools.core.polaris_runtime import current_session_dir, parse_prefixed_timestamp, read_lines_between


WAKE_RE = re.compile(r"WAKE\(1\)|wakeup_callback|offline_wakeup|online_wakeup", re.I)
BOOT_RE = re.compile(r"Boot Reason|boot\.action=boot_image|panic|assert|exception|watchdog|hardfault", re.I)


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_output_dir() -> Path:
    bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
    if bdd_run_dir:
        return Path(bdd_run_dir).resolve() / "false_wake_quiet"
    return BASE / "debug" / "false_wake_quiet" / datetime.now().strftime("%Y%m%d_%H%M%S")


def collect_logs(session_dir: Path, start: datetime, end: datetime, output_dir: Path) -> Dict[str, List[str]]:
    logs: Dict[str, List[str]] = {}
    log_dir = output_dir / "window_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    for port in configured_log_ports():
        lines = read_lines_between(port, start, end, session_dir=session_dir)
        logs[port] = lines
        (log_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    add_canonical_log_aliases(logs)
    return logs


def key_lines(logs: Dict[str, List[str]], regex: re.Pattern[str]) -> List[str]:
    lines: List[str] = []
    for port in ("COM12", "COM13", "COM14"):
        for line in logs.get(port, []):
            if regex.search(line):
                lines.append(line)
                if len(lines) >= 50:
                    return lines
    return lines


def line_counts(logs: Dict[str, List[str]]) -> Dict[str, int]:
    return {port: len(lines) for port, lines in logs.items() if port.upper().startswith("COM")}


def render_report(payload: Dict[str, Any]) -> str:
    lines = [
        "# 静默误唤醒基础监听报告",
        "",
        f"- 生成时间：`{payload.get('generated_at')}`",
        f"- 监听时长：`{payload.get('duration_s')}s`",
        f"- 结论：`{payload.get('result')}`",
        f"- 归因：`{payload.get('attribution')}`",
        f"- 原因：{payload.get('reason')}",
        "",
        "## 指标",
        "",
    ]
    for key, value in payload.get("metrics", {}).items():
        lines.append(f"- `{key}`：`{json.dumps(value, ensure_ascii=False)}`")
    lines.extend(["", "## 误唤醒关键日志", ""])
    for line in payload.get("wake_lines", [])[:20]:
        lines.append(f"- `{line}`")
    lines.extend(
        [
            "",
            "## 归因口径",
            "",
            "- 静默窗口内出现 wake marker：误唤醒 FAIL，需结合环境噪声和设备日志定位。",
            "- 串口全部无日志：logger/环境 BLOCKED，不判设备。",
            "- 出现 reboot/crash：设备稳定性 BLOCKED/FAIL，不能混入误唤醒率。",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor quiet false wake events.")
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--output-dir", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    session_dir = current_session_dir()
    start = datetime.now()
    time.sleep(max(0.0, float(args.duration_s)))
    end = datetime.now()
    logs = collect_logs(session_dir, start, end, output_dir)
    wakes = key_lines(logs, WAKE_RE)
    boots = key_lines(logs, BOOT_RE)
    counts = line_counts(logs)
    total_lines = sum(counts.values())
    if total_lines == 0:
        result = "BLOCKED"
        attribution = "serial_logger_or_ports"
        reason = "静默监听窗口内串口无日志，不能确认误唤醒。"
    elif boots:
        result = "BLOCKED"
        attribution = "device_reboot_or_crash_during_monitor"
        reason = "静默监听窗口内出现 reboot/crash 类日志，不能纳入误唤醒判定。"
    elif wakes:
        result = "FAIL"
        attribution = "false_wake_observed"
        reason = f"静默监听窗口内观察到 {len(wakes)} 条唤醒相关日志。"
    else:
        result = "PASS"
        attribution = "pass"
        reason = "静默监听窗口内未观察到唤醒 marker。"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "duration_s": float(args.duration_s),
        "session_dir": rel(session_dir),
        "started_at": start.isoformat(timespec="milliseconds"),
        "ended_at": end.isoformat(timespec="milliseconds"),
        "metrics": {
            "line_counts": counts,
            "total_lines": total_lines,
            "wake_line_count": len(wakes),
            "boot_or_crash_count": len(boots),
        },
        "wake_lines": wakes,
        "boot_or_crash_lines": boots,
    }
    write_json(output_dir / "false_wake_quiet_summary.json", payload)
    (output_dir / "false_wake_quiet_report.md").write_text(render_report(payload), encoding="utf-8")
    print(output_dir)
    print(json.dumps({"result": result, "wake_line_count": len(wakes)}, ensure_ascii=False))
    return 0 if result == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
