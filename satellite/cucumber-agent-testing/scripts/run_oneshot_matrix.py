#!/usr/bin/env python3
"""Run a wake+command one-shot interval matrix.

Each interval is executed as an independent FA2 wake+command batch so the
evidence remains easy to attribute. This is deliberately small by default; a
formal stress matrix can increase intervals/repeats later without changing the
Cucumber step contract.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.validation.polaris_fa2_command_batch import run_command_batch  # noqa: E402


DEFAULT_DEVICE_KEY = ""
DEFAULT_WAKE_WORD = "小美小美"
DEFAULT_COMMAND = "打开空调"


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_output_dir() -> Path:
    bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
    if bdd_run_dir:
        return Path(bdd_run_dir).resolve() / "oneshot_matrix"
    return BASE / "debug" / "oneshot_matrix" / datetime.now().strftime("%Y%m%d_%H%M%S")


def parse_intervals(text: str) -> List[int]:
    values: List[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if value < 0:
            raise ValueError(f"interval must be >=0: {value}")
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("no intervals")
    return values


def quote_cmd(cmd: List[str]) -> str:
    quoted: List[str] = []
    for arg in cmd:
        if not arg:
            quoted.append('""')
        elif any(ch.isspace() for ch in arg) or any(ch in arg for ch in ['"', "'", "&"]):
            quoted.append('"' + arg.replace('"', '\\"') + '"')
        else:
            quoted.append(arg)
    return " ".join(quoted)


def run_interval(
    interval_ms: int,
    output_dir: Path,
    *,
    command_file: Path,
    wake_word: str,
    device_key: str,
    post_command_gap_ms: int,
) -> Dict[str, Any]:
    label = f"bdd_oneshot_{interval_ms}ms"
    log_path = output_dir / f"interval_{interval_ms}ms.log"
    started_at = datetime.now()
    with log_path.open("w", encoding="utf-8", newline="") as log:
        log.write(
            "adapter-only fa2 batch: "
            f"label={label} wake_gap_ms={interval_ms} post_command_gap_ms={post_command_gap_ms}\n"
        )
        log.flush()
        batch = run_command_batch(
            command_file=command_file,
            wake_word=wake_word,
            device_key=device_key,
            wake_gap_ms=interval_ms,
            post_command_gap_ms=post_command_gap_ms,
            limit=1,
            label=label,
        )
        log.write(str(batch.get("output_dir", "")) + "\n")
        log.write(json.dumps({"total": batch.get("total"), "counts": batch.get("counts")}, ensure_ascii=False) + "\n")
        returncode = int(batch.get("returncode", 0) or 0)
    summary_path: Optional[Path] = batch.get("summary_path") if isinstance(batch.get("summary_path"), Path) else None
    if summary_path is None:
        roots = list((Path(os.environ.get("POLARIS_BDD_RUN_DIR", "")) / "session" / "artifacts" / "misc" / "fa2").glob(f"*{label}*/fa2_command_batch_summary.json")) if os.environ.get("POLARIS_BDD_RUN_DIR") else []
        if roots:
            summary_path = max(roots, key=lambda path: path.stat().st_mtime)
    summary = load_json(summary_path) if summary_path and summary_path.exists() else {}
    counts = dict(summary.get("counts", {}))
    row = (summary.get("rows") or [{}])[0] if summary.get("rows") else {}
    if returncode != 0:
        result = "BLOCKED"
        attribution = "runner_or_playback_failed"
        reason = f"interval {interval_ms}ms runner returncode={returncode}。"
    elif int(counts.get("PASS", 0) or 0) == 1:
        result = "PASS"
        attribution = "pass"
        reason = f"interval {interval_ms}ms 唤醒+命令闭环通过。"
    elif int(counts.get("BLOCKED", 0) or 0) > 0:
        result = "BLOCKED"
        attribution = "audio_or_serial_precondition"
        reason = f"interval {interval_ms}ms 阻塞：{counts}。"
    else:
        result = "FAIL"
        attribution = "firmware_asr_or_interval_policy"
        reason = f"interval {interval_ms}ms 未形成闭环：{counts}。"
    return {
        "interval_ms": interval_ms,
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "returncode": returncode,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "log_path": rel(log_path),
        "summary_path": rel(summary_path) if summary_path else "",
        "counts": counts,
        "row": row,
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = ["interval_ms", "result", "attribution", "reason", "summary_path", "log_path"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def render_report(payload: Dict[str, Any]) -> str:
    lines = [
        "# One-shot 间隔矩阵报告",
        "",
        f"- 生成时间：`{payload.get('generated_at')}`",
        f"- 命令：`{payload.get('command_text')}`",
        f"- 结论：`{payload.get('result')}`",
        f"- 归因：`{payload.get('attribution')}`",
        f"- 原因：{payload.get('reason')}",
        "",
        "| interval(ms) | result | attribution | reason |",
        "|---:|---|---|---|",
    ]
    for row in payload.get("rows", []):
        lines.append(f"| {row.get('interval_ms')} | `{row.get('result')}` | `{row.get('attribution')}` | {row.get('reason')} |")
    lines.extend(
        [
            "",
            "## 归因口径",
            "",
            "- 单个间隔播放/串口异常为 BLOCKED，不归固件。",
            "- 间隔已稳定命中且唤醒成功但命令无证据，归因到固件/ASR/间隔策略。",
            "- 正式阈值未确认时，小样本矩阵用于能力探测；后续可扩展 repeats 和阈值。",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one-shot wake+command interval matrix.")
    parser.add_argument("--intervals", default="500,800,1000")
    parser.add_argument("--command-text", default=DEFAULT_COMMAND)
    parser.add_argument("--wake-word", default=DEFAULT_WAKE_WORD)
    parser.add_argument("--device-key", default=DEFAULT_DEVICE_KEY)
    parser.add_argument("--post-command-gap-ms", type=int, default=6500)
    parser.add_argument("--output-dir", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    command_file = output_dir / "oneshot_command.txt"
    command_file.write_text(args.command_text.strip() + "\n", encoding="utf-8")
    rows = [
        run_interval(
            interval,
            output_dir,
            command_file=command_file,
            wake_word=args.wake_word,
            device_key=args.device_key,
            post_command_gap_ms=args.post_command_gap_ms,
        )
        for interval in parse_intervals(args.intervals)
    ]
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    if counts.get("BLOCKED"):
        result = "BLOCKED"
        attribution = "partial_audio_or_serial_precondition"
        reason = f"矩阵存在阻塞间隔：{counts}。"
    elif counts.get("FAIL"):
        result = "FAIL"
        attribution = "firmware_asr_or_interval_policy"
        reason = f"矩阵存在失败间隔：{counts}。"
    else:
        result = "PASS"
        attribution = "pass"
        reason = f"{len(rows)} 个 one-shot 间隔均通过。"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "command_text": args.command_text,
        "wake_word": args.wake_word,
        "intervals": [row["interval_ms"] for row in rows],
        "counts": counts,
        "rows": rows,
    }
    write_json(output_dir / "oneshot_matrix_summary.json", payload)
    write_csv(output_dir / "oneshot_matrix_results.csv", rows)
    (output_dir / "oneshot_matrix_report.md").write_text(render_report(payload), encoding="utf-8")
    print(output_dir)
    print(json.dumps({"result": result, "counts": counts}, ensure_ascii=False))
    return 0 if result == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

