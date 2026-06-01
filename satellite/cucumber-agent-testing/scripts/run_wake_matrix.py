#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run small Cucumber-style wake matrices for latency/continuous/random items.

The script intentionally keeps formal thresholds optional. When a requirement
threshold is not provided, latency and stability numbers are reported while
PASS/FAIL is based on observable wake evidence and environment health.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_cucumber import start_managed_session, stop_managed_session  # noqa: E402
from polaris_env import load_default_env  # noqa: E402
from runtime.snapshots import (  # noqa: E402
    build_snapshot,
    diff_snapshots,
    write_coverage as write_snapshot_coverage,
    write_diff as write_snapshot_diff,
    write_snapshot,
)
from run_wake_stress import (  # noqa: E402
    asr_wake_count,
    classify_wake_round,
    gather_logs,
    runtime_capabilities,
    sum_line_count,
    write_round_logs,
)
from tools.audio.polaris_audio_builder import build_sequence  # noqa: E402
from tools.core.polaris_runtime import parse_prefixed_timestamp  # noqa: E402
from tools.execution.polaris_case_runner import run_playback  # noqa: E402


DEFAULT_DEVICE_KEY = ""
DEFAULT_WAKE_WORD = "小美小美"
WAKE_PATTERNS = {
    "cp": re.compile(r"\bWAKE\(1\)", re.I),
    "ap": re.compile(r"wakeup_callback", re.I),
    "asr": re.compile(r"\bonline_wakeup\b|wakeup", re.I),
}


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_output_dir(scenario: str) -> Path:
    bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
    if bdd_run_dir:
        return Path(bdd_run_dir).resolve() / "wake_matrix"
    return BDD_ROOT / "debug" / "wake_matrix" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{scenario}"


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


def snapshot_env() -> Tuple[Path, Dict[str, Any]]:
    return load_default_env(WORKSPACE_ROOT)


def matrix_snapshot_task(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "schema": "polaris.wake-matrix.task.v1",
        "task_id": f"wake_matrix_{args.scenario}",
        "expected_state": {"wake_evidence": "expected"},
        "execution": {"rounds": args.rounds, "scenario": args.scenario},
    }


def capture_matrix_snapshot(
    output_dir: Path,
    args: argparse.Namespace,
    phase: str,
    *,
    file_name: str = "",
    extra_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    env_path, env_payload = snapshot_env()
    snapshot = build_snapshot(
        env_path=env_path,
        env_payload=env_payload,
        task=matrix_snapshot_task(args),
        phase=phase,
        run_dir=output_dir,
        evidence_roots=[output_dir],
        scope={"runner": "run_wake_matrix", "scenario": args.scenario},
        extra_state=extra_state,
    )
    write_snapshot(output_dir, snapshot, file_name=file_name or None)
    return snapshot


def build_wake_audio(output_dir: Path, wake_word: str) -> Tuple[Path, Dict[str, Any]]:
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "wake.wav"
    manifest = build_sequence([{"type": "tts", "text": wake_word}], audio_path)
    return audio_path, manifest


def find_first_marker_time(clean_logs: Dict[str, List[str]], role: str) -> Optional[datetime]:
    ports = {"cp": ["COM12", "cskcp"], "ap": ["COM14", "cskap"], "asr": ["COM13", "asr"]}.get(role, [])
    pattern = WAKE_PATTERNS[role]
    found: List[datetime] = []
    for port in ports:
        for line in clean_logs.get(port, []):
            if not pattern.search(line):
                continue
            ts = parse_prefixed_timestamp(line)
            if ts:
                found.append(ts)
    return min(found) if found else None


def latency_payload(started_at: datetime, clean_logs: Dict[str, List[str]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    values: List[int] = []
    for role in ("cp", "ap", "asr"):
        ts = find_first_marker_time(clean_logs, role)
        if ts is None:
            payload[f"{role}_wake_latency_ms"] = None
            continue
        value = int((ts - started_at).total_seconds() * 1000)
        payload[f"{role}_wake_latency_ms"] = value
        if value >= 0:
            values.append(value)
    payload["first_wake_marker_latency_ms"] = min(values) if values else None
    return payload


def add_nominal_audio_end_latency(row: Dict[str, Any], wake_manifest: Dict[str, Any]) -> None:
    first = row.get("first_wake_marker_latency_ms")
    duration_ms = int(wake_manifest.get("duration_ms", 0) or 0)
    if first is None:
        row["nominal_after_wake_audio_end_ms"] = None
    else:
        row["nominal_after_wake_audio_end_ms"] = int(first) - duration_ms


def should_store_full_logs(result: str, round_index: int, sample_every: int) -> bool:
    if result != "PASS":
        return True
    return sample_every > 0 and round_index % sample_every == 0


def cp_log_required_from_env() -> bool:
    return bool(runtime_capabilities().get("cp_log", True))


def wake_observed_min(metrics: Dict[str, Any], cp_log_required: bool) -> int:
    values = [int(metrics.get("ap_wake_count", 0) or 0), asr_wake_count(metrics)]
    if cp_log_required:
        values.insert(0, int(metrics.get("cp_wake_count", 0) or 0))
    return min(values)


def run_single_wake_round(
    *,
    round_index: int,
    scenario: str,
    output_dir: Path,
    session_dir: Path,
    wake_audio: Path,
    wake_manifest: Dict[str, Any],
    device_key: str,
    observe_s: float,
    sample_every: int,
    cp_log_required: bool,
    planned_interval_s: Optional[float] = None,
) -> Dict[str, Any]:
    round_dir = output_dir / "rounds" / f"{round_index:04d}_{scenario}"
    round_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now()
    playback = run_playback(wake_audio, device_key, round_dir, skip_probe=True, log_prefix="wake")
    time.sleep(observe_s)
    ended_at = datetime.now()
    raw, clean, window_summary, metrics, key_lines = gather_logs(session_dir, started_at, ended_at)
    line_count = sum_line_count(raw)
    result, attribution, reason, counted = classify_wake_round(playback.returncode, metrics, line_count, cp_log_required)
    latency = latency_payload(started_at, clean)
    if should_store_full_logs(result, round_index, sample_every):
        write_round_logs(round_dir, raw, clean)
    row = {
        "round": round_index,
        "scenario": scenario,
        "started_at": started_at.isoformat(timespec="milliseconds"),
        "ended_at": ended_at.isoformat(timespec="milliseconds"),
        "planned_interval_s": planned_interval_s,
        "result": result,
        "counted": counted,
        "attribution": attribution,
        "reason": reason,
        "playback_returncode": playback.returncode,
        "line_count": line_count,
        "cp_wake_count": int(metrics.get("cp_wake_count", 0) or 0),
        "ap_wake_count": int(metrics.get("ap_wake_count", 0) or 0),
        "asr_wake_count": asr_wake_count(metrics),
        "boot_marker_count": int(metrics.get("boot_marker_count", 0) or 0),
        "crash_marker_count": int(metrics.get("crash_marker_count", 0) or 0),
        "cp_log_required": cp_log_required,
        "evidence_dir": rel(round_dir),
        **latency,
    }
    add_nominal_audio_end_latency(row, wake_manifest)
    write_json(
        round_dir / "round.json",
        {
            "row": row,
            "wake_manifest": wake_manifest,
            "window_summary": window_summary,
            "metrics": metrics,
            "key_lines": key_lines[:120],
        },
    )
    return row


def build_continuous_audio(output_dir: Path, wake_word: str, rounds: int, gap_ms: int) -> Tuple[Path, Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    for index in range(rounds):
        if index > 0 and gap_ms > 0:
            steps.append({"type": "silence", "duration_ms": gap_ms})
        steps.append({"type": "tts", "text": wake_word})
    audio_path = output_dir / "audio" / "continuous_wake.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    return audio_path, build_sequence(steps, audio_path)


def run_continuous(
    *,
    output_dir: Path,
    session_dir: Path,
    wake_word: str,
    device_key: str,
    rounds: int,
    gap_ms: int,
    observe_s: float,
    min_expected_wakes: int,
    cp_log_required: bool,
) -> List[Dict[str, Any]]:
    audio_path, manifest = build_continuous_audio(output_dir, wake_word, rounds, gap_ms)
    round_dir = output_dir / "rounds" / "0001_continuous"
    round_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now()
    playback = run_playback(audio_path, device_key, round_dir, skip_probe=True, log_prefix="continuous_wake")
    time.sleep(observe_s)
    ended_at = datetime.now()
    raw, clean, window_summary, metrics, key_lines = gather_logs(session_dir, started_at, ended_at)
    line_count = sum_line_count(raw)
    if playback.returncode != 0:
        result = "BLOCKED"
        attribution = "audio/playback"
        reason = f"连续唤醒音频播放失败 returncode={playback.returncode}。"
        counted = False
    elif line_count <= 0:
        result = "BLOCKED"
        attribution = "serial_logger_or_ports"
        reason = "连续唤醒播放成功但串口窗口无日志。"
        counted = False
    elif int(metrics.get("boot_marker_count", 0) or 0) > 0 or int(metrics.get("crash_marker_count", 0) or 0) > 0:
        result = "FAIL"
        attribution = "firmware_device_stability"
        reason = "连续唤醒窗口出现 reboot/crash 标记。"
        counted = True
    else:
        observed = wake_observed_min(metrics, cp_log_required)
        if observed >= min_expected_wakes:
            result = "PASS"
            attribution = "pass"
            source_label = "三端" if cp_log_required else "AP/ASR"
            reason = f"连续唤醒稳定性 smoke 通过，最小{source_label}唤醒数 {observed}/{rounds}。"
            counted = True
        elif observed > 0:
            result = "FAIL"
            attribution = "continuous_wake_evidence_below_expected"
            source_label = "三端" if cp_log_required else "AP/ASR"
            reason = f"连续唤醒有部分证据但低于 smoke 期望，最小{source_label}唤醒数 {observed}/{rounds}，期望 >= {min_expected_wakes}。"
            counted = True
        else:
            result = "FAIL"
            attribution = "firmware_device_or_audio_path"
            reason = "连续唤醒播放成功但未观察到完整三端唤醒证据。"
            counted = True
    write_round_logs(round_dir, raw, clean)
    row = {
        "round": 1,
        "scenario": "continuous",
        "started_at": started_at.isoformat(timespec="milliseconds"),
        "ended_at": ended_at.isoformat(timespec="milliseconds"),
        "planned_interval_s": gap_ms / 1000.0,
        "result": result,
        "counted": counted,
        "attribution": attribution,
        "reason": reason,
        "playback_returncode": playback.returncode,
        "line_count": line_count,
        "cp_wake_count": int(metrics.get("cp_wake_count", 0) or 0),
        "ap_wake_count": int(metrics.get("ap_wake_count", 0) or 0),
        "asr_wake_count": asr_wake_count(metrics),
        "boot_marker_count": int(metrics.get("boot_marker_count", 0) or 0),
        "crash_marker_count": int(metrics.get("crash_marker_count", 0) or 0),
        "cp_log_required": cp_log_required,
        "continuous_segments": rounds,
        "min_expected_wakes": min_expected_wakes,
        "continuous_gap_ms": gap_ms,
        "evidence_dir": rel(round_dir),
        **latency_payload(started_at, clean),
    }
    add_nominal_audio_end_latency(row, manifest)
    write_json(
        round_dir / "round.json",
        {
            "row": row,
            "wake_manifest": manifest,
            "window_summary": window_summary,
            "metrics": metrics,
            "key_lines": key_lines[:160],
        },
    )
    return [row]


def write_rows_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "round",
        "scenario",
        "planned_interval_s",
        "result",
        "counted",
        "attribution",
        "reason",
        "playback_returncode",
        "line_count",
        "cp_wake_count",
        "ap_wake_count",
        "asr_wake_count",
        "first_wake_marker_latency_ms",
        "cp_wake_latency_ms",
        "ap_wake_latency_ms",
        "asr_wake_latency_ms",
        "nominal_after_wake_audio_end_ms",
        "boot_marker_count",
        "crash_marker_count",
        "cp_log_required",
        "evidence_dir",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def aggregate_rows(args: argparse.Namespace, output_dir: Path, rows: List[Dict[str, Any]], wake_manifest: Dict[str, Any]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    counted = [row for row in rows if row.get("counted") is True]
    pass_count = sum(1 for row in counted if row.get("result") == "PASS")
    fail_count = sum(1 for row in counted if row.get("result") == "FAIL")
    latencies = [
        int(row["first_wake_marker_latency_ms"])
        for row in rows
        if row.get("result") == "PASS" and row.get("first_wake_marker_latency_ms") is not None
    ]
    over_threshold = []
    if args.max_latency_ms > 0:
        over_threshold = [row for row in rows if row.get("first_wake_marker_latency_ms") is not None and int(row["first_wake_marker_latency_ms"]) > args.max_latency_ms]
    if any(row.get("result") == "FAIL" for row in rows):
        result = "FAIL"
        attribution = next((row.get("attribution") for row in rows if row.get("result") == "FAIL"), "wake_matrix_fail")
        reason = "存在 FAIL 轮次，详见 wake_matrix_rows.csv。"
    elif any(row.get("result") == "BLOCKED" for row in rows):
        result = "BLOCKED"
        attribution = next((row.get("attribution") for row in rows if row.get("result") == "BLOCKED"), "wake_matrix_blocked")
        reason = "存在 BLOCKED 轮次，不能完成该测试项验证。"
    elif over_threshold:
        result = "FAIL"
        attribution = "wake_latency_threshold"
        reason = f"{len(over_threshold)} 个成功样本超过 max_latency_ms={args.max_latency_ms}。"
    else:
        result = "PASS"
        attribution = "pass"
        reason = "所有有效轮次均通过；未配置阈值的耗时指标仅统计不判失败。"
    latency_stats = {
        "sample_count": len(latencies),
        "avg_ms": round(mean(latencies), 3) if latencies else None,
        "min_ms": min(latencies) if latencies else None,
        "max_ms": max(latencies) if latencies else None,
        "max_latency_ms": args.max_latency_ms or None,
        "over_threshold_count": len(over_threshold),
        "reference": "host_command_start_to_first_serial_wake_marker",
        "limitation": "当前小样本没有音频回采/播放起点硬件 marker，不能等同于需求中的唤醒词最后音节到提示音耗时。",
    }
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scenario": args.scenario,
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "run_dir": rel(output_dir),
        "wake_word": args.wake_word,
        "device_key": args.device_key,
        "wake_audio_duration_ms": int(wake_manifest.get("duration_ms", 0) or 0),
        "cp_log_required": bool(rows[0].get("cp_log_required")) if rows else True,
        "rounds_requested": args.rounds,
        "counts": counts,
        "counted_rounds": len(counted),
        "pass": pass_count,
        "fail": fail_count,
        "rate": None if not counted else round(pass_count / len(counted), 6),
        "latency": latency_stats,
        "rows": rows,
    }


def render_report(payload: Dict[str, Any]) -> str:
    lines = [
        "# Wake Matrix 报告",
        "",
        f"- 场景：`{payload.get('scenario')}`",
        f"- 结论：`{payload.get('result')}`",
        f"- 归因：`{payload.get('attribution')}`",
        f"- 原因：{payload.get('reason')}",
        f"- 唤醒词：`{payload.get('wake_word')}`",
        f"- 唤醒音频时长：`{payload.get('wake_audio_duration_ms')}ms`",
        f"- 结果分布：`{json.dumps(payload.get('counts', {}), ensure_ascii=False)}`",
        f"- 成功率：`{'' if payload.get('rate') is None else payload.get('rate')}`",
        f"- 快照 diff：`{(payload.get('snapshots') or {}).get('diff', '')}`",
        f"- 快照 coverage：`{(payload.get('snapshots') or {}).get('coverage', '')}`",
        "",
        "## 响应时间统计",
        "",
        f"- 统计参考点：`{payload.get('latency', {}).get('reference')}`",
        f"- 口径限制：{payload.get('latency', {}).get('limitation')}",
        f"- 样本数：`{payload.get('latency', {}).get('sample_count')}`",
        f"- 平均：`{payload.get('latency', {}).get('avg_ms')}` ms",
        f"- 最小：`{payload.get('latency', {}).get('min_ms')}` ms",
        f"- 最大：`{payload.get('latency', {}).get('max_ms')}` ms",
        f"- 超阈值：`{payload.get('latency', {}).get('over_threshold_count')}`",
        "",
        "## 轮次",
        "",
        "| round | result | interval(s) | cp/ap/asr | first marker(ms) | nominal after audio end(ms) | attribution | reason |",
        "|---:|---|---:|---|---:|---:|---|---|",
    ]
    for row in payload.get("rows", []):
        reason = str(row.get("reason", "")).replace("|", "\\|")
        if len(reason) > 120:
            reason = reason[:117] + "..."
        interval = "" if row.get("planned_interval_s") is None else row.get("planned_interval_s")
        lines.append(
            f"| {row.get('round')} | `{row.get('result')}` | {interval} | "
            f"{row.get('cp_wake_count')}/{row.get('ap_wake_count')}/{row.get('asr_wake_count')} | "
            f"{row.get('first_wake_marker_latency_ms')} | {row.get('nominal_after_wake_audio_end_ms')} | `{row.get('attribution')}` | {reason} |"
        )
    lines.extend(
        [
            "",
            "## 归因口径",
            "",
            "- 播放失败或串口无日志为 BLOCKED，不归固件。",
            "- reboot/crash/log stop 归设备稳定性问题。",
            "- 未配置响应时间阈值时，只统计 avg/min/max/超阈值候选，不把慢样本直接判 FAIL。",
            "- 精确唤醒响应时间需要音频回采或播放起点硬件 marker；当前 smoke 用主机启动播放命令到首个串口唤醒 marker 的粗略 proxy。",
            "- 连续/随机间隔属于稳定性 smoke，正式压力次数和阈值可由 Cucumber 参数扩展。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    output_dir = (Path(args.output_dir) if args.output_dir else default_output_dir(args.scenario)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    before_snapshot = capture_matrix_snapshot(output_dir, args, "before", file_name="snapshot_before.json")
    wake_audio, wake_manifest = build_wake_audio(output_dir, args.wake_word)
    bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
    managed = None
    logger_proc = None
    if bdd_run_dir:
        session_dir = Path(bdd_run_dir).resolve() / "session"
    else:
        managed, logger_proc = start_managed_session(output_dir)
        session_dir = managed.session_dir
        time.sleep(1.0)
    cp_log_required = cp_log_required_from_env()
    rows: List[Dict[str, Any]] = []
    try:
        write_json(
            output_dir / "wake_matrix_start.json",
            {
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "args": vars(args),
                "session_dir": rel(session_dir),
                "wake_manifest": wake_manifest,
            },
        )
        if args.scenario == "continuous":
            rows = run_continuous(
                output_dir=output_dir,
                session_dir=session_dir,
                wake_word=args.wake_word,
                device_key=args.device_key,
                rounds=args.rounds,
                gap_ms=args.continuous_gap_ms,
                observe_s=args.observe_s,
                min_expected_wakes=args.min_expected_wakes,
                cp_log_required=cp_log_required,
            )
            if rows:
                capture_matrix_snapshot(
                    output_dir,
                    args,
                    "checkpoint_0001_continuous",
                    file_name="snapshot_checkpoint_0001_continuous.json",
                    extra_state={"latest_row": rows[-1], "row_count": len(rows)},
                )
        else:
            rng = random.Random(args.seed)
            for round_index in range(1, args.rounds + 1):
                planned_interval: Optional[float] = None
                if args.scenario == "latency":
                    if round_index > 1:
                        time.sleep(args.idle_wait_s)
                    planned_interval = args.idle_wait_s if round_index > 1 else None
                elif args.scenario == "random":
                    if round_index > 1:
                        planned_interval = round(rng.uniform(args.random_min_s, args.random_max_s), 3)
                        time.sleep(planned_interval)
                    else:
                        planned_interval = None
                rows.append(
                    run_single_wake_round(
                        round_index=round_index,
                        scenario=args.scenario,
                        output_dir=output_dir,
                        session_dir=session_dir,
                        wake_audio=wake_audio,
                        wake_manifest=wake_manifest,
                        device_key=args.device_key,
                        observe_s=args.observe_s,
                        sample_every=args.sample_pass_logs_every,
                        cp_log_required=cp_log_required,
                        planned_interval_s=planned_interval,
                    )
                )
                capture_matrix_snapshot(
                    output_dir,
                    args,
                    f"checkpoint_{round_index:04d}_{args.scenario}",
                    file_name=f"snapshot_checkpoint_{round_index:04d}_{args.scenario}.json",
                    extra_state={"latest_row": rows[-1], "row_count": len(rows)},
                )
        write_rows_csv(output_dir / "wake_matrix_rows.csv", rows)
        payload = aggregate_rows(args, output_dir, rows, wake_manifest)
        after_snapshot = capture_matrix_snapshot(output_dir, args, "after", file_name="snapshot_after.json", extra_state={"summary": payload})
        capture_matrix_snapshot(output_dir, args, "final", file_name="snapshot_final.json", extra_state={"summary": payload})
        diff = diff_snapshots(before_snapshot, after_snapshot)
        diff_path = write_snapshot_diff(output_dir, diff)
        coverage_path = write_snapshot_coverage(output_dir, diff.get("coverage", {}))
        payload["snapshots"] = {
            "before": rel(output_dir / "snapshot_before.json"),
            "after": rel(output_dir / "snapshot_after.json"),
            "final": rel(output_dir / "snapshot_final.json"),
            "diff": rel(diff_path),
            "coverage": rel(coverage_path),
            "coverage_summary": diff.get("coverage", {}),
            "diff_summary": diff.get("summary", {}),
        }
        write_json(output_dir / "wake_matrix_summary.json", payload)
        (output_dir / "wake_matrix_report.md").write_text(render_report(payload), encoding="utf-8")
        print(output_dir)
        print(json.dumps({"result": payload["result"], "attribution": payload["attribution"]}, ensure_ascii=False))
        return 0 if payload["result"] in {"PASS", "BLOCKED"} else 1
    finally:
        if managed is not None and logger_proc is not None:
            stop_managed_session(managed, logger_proc)


def build_parser() -> argparse.ArgumentParser:
    env_defaults = load_env_defaults()
    parser = argparse.ArgumentParser(description="Run wake latency/continuous/random smoke matrices.")
    parser.add_argument("--scenario", choices=["latency", "continuous", "random"], required=True)
    parser.add_argument("--wake-word", default=env_defaults.get("wake_word", DEFAULT_WAKE_WORD))
    parser.add_argument("--device-key", default=env_defaults.get("device_key", DEFAULT_DEVICE_KEY))
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--observe-s", type=float, default=6.0)
    parser.add_argument("--idle-wait-s", type=float, default=18.0)
    parser.add_argument("--sample-pass-logs-every", type=int, default=10)
    parser.add_argument("--max-latency-ms", type=int, default=0, help="0 means report-only")
    parser.add_argument("--continuous-gap-ms", type=int, default=0)
    parser.add_argument("--min-expected-wakes", type=int, default=1)
    parser.add_argument("--random-min-s", type=float, default=1.0)
    parser.add_argument("--random-max-s", type=float, default=6.0)
    parser.add_argument("--seed", type=int, default=20260514)
    parser.add_argument("--output-dir", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.rounds <= 0:
        raise SystemExit("--rounds must be > 0")
    if args.random_min_s < 0 or args.random_max_s < args.random_min_s:
        raise SystemExit("invalid random interval range")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

