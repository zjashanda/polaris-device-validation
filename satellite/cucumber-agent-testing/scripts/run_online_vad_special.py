#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run exploratory online VAD special cases.

The script builds wake + online utterance audio locally. Some candidates include
intentional pauses so VAD truncation/timeout evidence can be separated from
generic online ASR failures.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from run_cucumber import start_managed_session, stop_managed_session  # noqa: E402
from polaris_env import load_default_env  # noqa: E402
from run_wake_stress import asr_wake_count, gather_logs, sum_line_count, write_round_logs  # noqa: E402
from runtime.capabilities import infer_from_env_file  # noqa: E402
from tools.audio.polaris_audio_builder import build_sequence  # noqa: E402
from tools.execution.polaris_case_runner import run_playback  # noqa: E402


DEFAULT_DEVICE_KEY = ""
DEFAULT_WAKE_WORD = "小美小美"
VAD_END_RE = re.compile(r"cloud\.instructions\.vadEnd|topic out is cloud\.instructions\.vadEnd|vadEnd", re.I)


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
        return Path(bdd_run_dir).resolve() / "online_vad_special"
    return BDD_ROOT / "debug" / "online_vad_special" / datetime.now().strftime("%Y%m%d_%H%M%S")


def load_env_defaults() -> Dict[str, str]:
    _env_path, payload = load_default_env(WORKSPACE_ROOT)
    return {
        "wake_word": str(payload.get("current_wakeup_word") or payload.get("device", {}).get("wake_word") or DEFAULT_WAKE_WORD),
        "device_key": str(
            payload.get("default_playback_device_key")
            or payload.get("audio", {}).get("default_playback_device_key")
            or DEFAULT_DEVICE_KEY
        ),
    }


def runtime_capabilities() -> Dict[str, Any]:
    env_file = os.environ.get("POLARIS_ENV_FILE", "").strip()
    if not env_file:
        return {"cp_log": True, "asr_log": True}
    path = Path(env_file)
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    try:
        _project, caps = infer_from_env_file(path)
        return caps
    except Exception:
        return {"cp_log": True, "asr_log": True}


def default_candidates() -> List[Dict[str, Any]]:
    return [
        {
            "id": "short_weather",
            "category": "short",
            "expected_text": "天气",
            "steps": [{"type": "tts", "text": "天气"}],
        },
        {
            "id": "normal_weather",
            "category": "normal",
            "expected_text": "今天天气怎么样",
            "steps": [{"type": "tts", "text": "今天天气怎么样"}],
        },
        {
            "id": "pause_weather_900ms",
            "category": "pause",
            "expected_text": "今天天气怎么样",
            "steps": [
                {"type": "tts", "text": "今天"},
                {"type": "silence", "duration_ms": 900},
                {"type": "tts", "text": "天气怎么样"},
            ],
        },
        {
            "id": "pause_weather_1500ms",
            "category": "long_pause",
            "expected_text": "今天天气怎么样",
            "steps": [
                {"type": "tts", "text": "今天"},
                {"type": "silence", "duration_ms": 1500},
                {"type": "tts", "text": "天气怎么样"},
            ],
        },
    ]


def normalize_text(text: str) -> str:
    return re.sub(r"[\s，,。！？!?.、：:；;]+", "", text.strip().lower())


def text_coverage(expected: str, observed_texts: List[str]) -> Dict[str, Any]:
    expected_norm = normalize_text(expected)
    observed_norms = [normalize_text(item) for item in observed_texts if item]
    if not expected_norm:
        return {"expected": expected, "observed": observed_texts, "coverage": None, "missing_chars": []}
    joined = "".join(observed_norms)
    missing = [char for char in expected_norm if char not in joined]
    coverage = round((len(expected_norm) - len(missing)) / len(expected_norm), 6)
    return {"expected": expected, "observed": observed_texts, "coverage": coverage, "missing_chars": missing}


def count_vad_end(clean_logs: Dict[str, List[str]]) -> int:
    return sum(1 for lines in clean_logs.values() for line in lines if VAD_END_RE.search(line))


def build_candidate_audio(output_dir: Path, wake_word: str, candidate: Dict[str, Any], wake_gap_ms: int, post_gap_ms: int) -> tuple[Path, Dict[str, Any]]:
    steps: List[Dict[str, Any]] = [{"type": "tts", "text": wake_word}, {"type": "silence", "duration_ms": wake_gap_ms}]
    steps.extend(candidate["steps"])
    steps.append({"type": "silence", "duration_ms": post_gap_ms})
    audio_path = output_dir / "audio" / f"{candidate['id']}.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    return audio_path, build_sequence(steps, audio_path)


def run_candidate(
    *,
    output_dir: Path,
    session_dir: Path,
    wake_word: str,
    device_key: str,
    candidate: Dict[str, Any],
    wake_gap_ms: int,
    post_gap_ms: int,
    observe_s: float,
    cp_log_required: bool,
) -> Dict[str, Any]:
    case_dir = output_dir / "cases" / candidate["id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    audio_path, manifest = build_candidate_audio(output_dir, wake_word, candidate, wake_gap_ms, post_gap_ms)
    started_at = datetime.now()
    playback = run_playback(audio_path, device_key, case_dir, skip_probe=True, log_prefix="online_vad")
    time.sleep(observe_s)
    ended_at = datetime.now()
    raw, clean, window_summary, metrics, key_lines = gather_logs(session_dir, started_at, ended_at)
    write_round_logs(case_dir, raw, clean)
    line_count = sum_line_count(raw)
    cp_wake = int(metrics.get("cp_wake_count", 0) or 0)
    ap_wake = int(metrics.get("ap_wake_count", 0) or 0)
    asr_wake = asr_wake_count(metrics)
    online_texts = list(metrics.get("ap_online_asr_texts", []) or [])
    vad_end_count = count_vad_end(clean)
    cloud_tts_count = int(metrics.get("ap_cloud_tts_play_count", 0) or 0) + int(metrics.get("ap_instruction_broadcast_count", 0) or 0)
    coverage = text_coverage(str(candidate.get("expected_text", "")), online_texts)
    if playback.returncode != 0:
        result = "BLOCKED"
        attribution = "audio_playback_or_device_key"
        reason = f"播放失败 returncode={playback.returncode}。"
    elif line_count <= 0:
        result = "BLOCKED"
        attribution = "serial_logger_or_ports"
        reason = "播放成功但串口窗口无日志。"
    elif (cp_log_required and cp_wake < 1) or ap_wake < 1 or asr_wake < 1:
        result = "FAIL"
        attribution = "wake_precondition_for_online_vad"
        reason = f"在线 VAD 前置唤醒证据不足，cp/ap/asr={cp_wake}/{ap_wake}/{asr_wake}。"
    elif not online_texts and cloud_tts_count <= 0 and vad_end_count <= 0:
        result = "FAIL"
        attribution = "online_vad_or_cloud_path"
        reason = "唤醒成功后未观察到在线 ASR、VAD end 或云端播报证据。"
    else:
        result = "PASS"
        attribution = "pass"
        if online_texts:
            reason = f"观察到在线 ASR 文本 {online_texts}，coverage={coverage.get('coverage')}。"
        elif vad_end_count > 0:
            reason = f"观察到 VAD end={vad_end_count}，但未取得在线 ASR 文本，标记探索性待复核。"
        else:
            reason = "观察到云端播报/指令证据，标记探索性通过。"
    row = {
        "candidate_id": candidate["id"],
        "category": candidate["category"],
        "expected_text": candidate.get("expected_text", ""),
        "started_at": started_at.isoformat(timespec="milliseconds"),
        "ended_at": ended_at.isoformat(timespec="milliseconds"),
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "playback_returncode": playback.returncode,
        "line_count": line_count,
        "cp_wake_count": cp_wake,
        "ap_wake_count": ap_wake,
        "asr_wake_count": asr_wake,
        "cp_log_required": cp_log_required,
        "online_asr_texts": online_texts,
        "vad_end_count": vad_end_count,
        "cloud_tts_or_instruction_count": cloud_tts_count,
        "coverage": coverage,
        "evidence_dir": rel(case_dir),
    }
    write_json(
        case_dir / "online_vad_case.json",
        {
            "row": row,
            "candidate": candidate,
            "audio_manifest": manifest,
            "window_summary": window_summary,
            "metrics": metrics,
            "key_lines": key_lines[:160],
        },
    )
    return row


def write_rows_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "candidate_id",
        "category",
        "expected_text",
        "result",
        "attribution",
        "reason",
        "cp_wake_count",
        "ap_wake_count",
        "asr_wake_count",
        "cp_log_required",
        "online_asr_texts",
        "vad_end_count",
        "cloud_tts_or_instruction_count",
        "evidence_dir",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def summarize(output_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    needs_review = [
        row for row in rows
        if row.get("result") == "PASS"
        and (not row.get("online_asr_texts") or (row.get("coverage") or {}).get("coverage") not in (None, 1.0))
    ]
    if any(row["result"] == "FAIL" for row in rows):
        result = "FAIL"
        attribution = next(row["attribution"] for row in rows if row["result"] == "FAIL")
        reason = "存在在线 VAD FAIL 候选，详见 online_vad_special_rows.csv。"
    elif any(row["result"] == "BLOCKED" for row in rows):
        result = "BLOCKED"
        attribution = next(row["attribution"] for row in rows if row["result"] == "BLOCKED")
        reason = "存在 BLOCKED 候选，不能完成在线 VAD 验证。"
    else:
        result = "PASS"
        attribution = "pass_with_exploratory_review" if needs_review else "pass"
        reason = "所有在线 VAD 小样本均有在线链路证据；自由文本覆盖差异作为探索性待复核项输出。"
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "run_dir": rel(output_dir),
        "counts": counts,
        "candidate_count": len(rows),
        "needs_review_count": len(needs_review),
        "rows": rows,
    }


def render_report(payload: Dict[str, Any]) -> str:
    lines = [
        "# Online VAD Special 报告",
        "",
        f"- 结论：`{payload.get('result')}`",
        f"- 归因：`{payload.get('attribution')}`",
        f"- 原因：{payload.get('reason')}",
        f"- 候选数：`{payload.get('candidate_count')}`",
        f"- 待复核：`{payload.get('needs_review_count')}`",
        f"- 结果分布：`{json.dumps(payload.get('counts', {}), ensure_ascii=False)}`",
        "",
        "| candidate | category | result | cp/ap/asr | vadEnd | online text | attribution | reason |",
        "|---|---|---|---|---:|---|---|---|",
    ]
    for row in payload.get("rows", []):
        text = json.dumps(row.get("online_asr_texts", []), ensure_ascii=False).replace("|", "\\|")
        reason = str(row.get("reason", "")).replace("|", "\\|")
        if len(reason) > 120:
            reason = reason[:117] + "..."
        lines.append(
            f"| `{row.get('candidate_id')}` | `{row.get('category')}` | `{row.get('result')}` | "
            f"{row.get('cp_wake_count')}/{row.get('ap_wake_count')}/{row.get('asr_wake_count')} | "
            f"{row.get('vad_end_count')} | {text} | `{row.get('attribution')}` | {reason} |"
        )
    lines.extend(
        [
            "",
            "## 归因口径",
            "",
            "- 播放失败、串口无日志、联网前置失败为 BLOCKED。",
            "- 唤醒前置已成立但无在线 ASR/VAD end/云端播报证据，归在线 VAD/云端链路问题。",
            "- 在线文本覆盖不完全先作为探索性待复核，不直接判固件 FAIL；正式 VAD 截断率需要用户确认容差和标注集。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    output_dir = (Path(args.output_dir) if args.output_dir else default_output_dir()).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
    managed = None
    logger_proc = None
    if bdd_run_dir:
        session_dir = Path(bdd_run_dir).resolve() / "session"
    else:
        managed, logger_proc = start_managed_session(output_dir)
        session_dir = managed.session_dir
        time.sleep(1.0)
    try:
        candidates = default_candidates()[: args.limit]
        caps = runtime_capabilities()
        cp_log_required = bool(caps.get("cp_log", True))
        rows: List[Dict[str, Any]] = []
        for idx, candidate in enumerate(candidates, start=1):
            if idx > 1 and args.between_case_wait_s > 0:
                time.sleep(args.between_case_wait_s)
            rows.append(
                run_candidate(
                    output_dir=output_dir,
                    session_dir=session_dir,
                    wake_word=args.wake_word,
                    device_key=args.device_key,
                    candidate=candidate,
                    wake_gap_ms=args.wake_gap_ms,
                    post_gap_ms=args.post_gap_ms,
                    observe_s=args.observe_s,
                    cp_log_required=cp_log_required,
                )
            )
        write_rows_csv(output_dir / "online_vad_special_rows.csv", rows)
        payload = summarize(output_dir, rows)
        write_json(output_dir / "online_vad_special_summary.json", payload)
        (output_dir / "online_vad_special_report.md").write_text(render_report(payload), encoding="utf-8")
        print(output_dir)
        print(json.dumps({"result": payload["result"], "attribution": payload["attribution"]}, ensure_ascii=False))
        return 0 if payload["result"] in {"PASS", "BLOCKED"} else 1
    finally:
        if managed is not None and logger_proc is not None:
            stop_managed_session(managed, logger_proc)


def build_parser() -> argparse.ArgumentParser:
    env_defaults = load_env_defaults()
    parser = argparse.ArgumentParser(description="Run exploratory online VAD special cases.")
    parser.add_argument("--wake-word", default=env_defaults.get("wake_word", DEFAULT_WAKE_WORD))
    parser.add_argument("--device-key", default=env_defaults.get("device_key", DEFAULT_DEVICE_KEY))
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--wake-gap-ms", type=int, default=1000)
    parser.add_argument("--post-gap-ms", type=int, default=9000)
    parser.add_argument("--observe-s", type=float, default=12.0)
    parser.add_argument("--between-case-wait-s", type=float, default=3.0)
    parser.add_argument("--output-dir", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit must be > 0")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

