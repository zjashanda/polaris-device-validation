#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cucumber-style overnight stress runner for wake validation.

This runner is intentionally offline after it starts: it uses local feature-like
contracts, local serial/audio tools, and writes every decision to debug/stress.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from run_cucumber import (  # noqa: E402
    managed_session_payload,
    start_managed_session,
    stop_managed_session,
)
from polaris_env import load_default_env  # noqa: E402
from runtime.snapshots import (  # noqa: E402
    build_snapshot,
    diff_snapshots,
    write_coverage as write_snapshot_coverage,
    write_diff as write_snapshot_diff,
    write_snapshot,
)
from runtime.capabilities import infer_from_env_file  # noqa: E402
from tools.audio.polaris_audio_builder import build_sequence  # noqa: E402
from tools.core.polaris_adapter_bridge import run_adapter_action_capture  # noqa: E402
from tools.core.polaris_config import add_canonical_log_aliases, configured_log_ports  # noqa: E402
from tools.core.polaris_runtime import latest_heartbeat, parse_prefixed_timestamp, read_lines_between  # noqa: E402
from tools.execution.polaris_case_runner import default_playback_device_key, playback_device_label, run_playback, sanitize_logs, summarize_window  # noqa: E402
from tools.execution.polaris_doc_case_runner import collect_metrics  # noqa: E402
from tools.probe.polaris_phrase_probe import build_key_lines  # noqa: E402


AP_WAKE_RE = re.compile(r"wakeup_callback", re.I)
SESSION_TIMER_RE = re.compile(r"restart session timer with\s+(\d+)s", re.I)
TIMEOUT_REFRESH_RE = re.compile(r"(?:half|full)duplex timeout refresh to\s+(\d+)s", re.I)
SESSION_TIMEOUT_RE = re.compile(r"stop interactive by session timeout", re.I)

RESULT_FIELDS = [
    "round",
    "scenario_id",
    "scenario_name",
    "started_at",
    "ended_at",
    "result",
    "counted_in_rate",
    "attribution",
    "reason",
    "playback_returncode",
    "precondition_result",
    "timing_bucket",
    "target_delay_s",
    "timeout_s",
    "deadline_margin_ms",
    "cp_wake_count",
    "ap_wake_count",
    "asr_wake_count",
    "cp_command_count",
    "ap_cloud_tts_play_count",
    "ap_instruction_broadcast_count",
    "boot_marker_count",
    "crash_marker_count",
    "line_count",
    "evidence_dir",
]


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def parse_datetime(value: str) -> datetime:
    raw = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid datetime: {value}") from exc


def tomorrow_0830() -> datetime:
    now = datetime.now()
    target = now.replace(hour=8, minute=30, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def load_env() -> Dict[str, Any]:
    _path, payload = load_default_env(WORKSPACE_ROOT)
    return payload


def serial_context_from_env(env: Dict[str, Any]) -> Dict[str, str]:
    serial = env.get("serial", {}) if isinstance(env.get("serial"), dict) else {}
    ports = serial.get("ports", {}) if isinstance(serial.get("ports"), dict) else {}
    return {
        "ap_port": str(ports.get("ap") or "").strip(),
        "cp_port": str(ports.get("cp") or "").strip(),
        "asr_port": str(ports.get("asr") or ports.get("upper") or "").strip(),
        "baudrate": str(serial.get("baudrate") or env.get("baudrate") or "").strip(),
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


def jsonable_args(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, value in vars(args).items():
        if isinstance(value, datetime):
            payload[key] = value.isoformat(timespec="seconds")
        else:
            payload[key] = value
    return payload


def sum_line_count(raw_logs: Dict[str, List[str]]) -> int:
    return sum(len(lines) for lines in raw_logs.values())


def asr_wake_count(metrics: Dict[str, Any]) -> int:
    return int(metrics.get("wb_wake_count", 0) or 0) + int(metrics.get("wb_online_wake_count", 0) or 0)


def gather_logs(session_dir: Path, start_dt: datetime, end_dt: datetime) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], dict, dict, List[str]]:
    raw_logs: Dict[str, List[str]] = {}
    for port in configured_log_ports():
        raw_logs[port] = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
    add_canonical_log_aliases(raw_logs)
    clean_logs = sanitize_logs(raw_logs)
    window_summary = summarize_window(clean_logs)
    metrics = collect_metrics(clean_logs, window_summary)
    key_lines = build_key_lines(clean_logs)
    return raw_logs, clean_logs, window_summary, metrics, key_lines


def write_round_logs(round_dir: Path, raw_logs: Dict[str, List[str]], clean_logs: Dict[str, List[str]]) -> None:
    log_dir = round_dir / "window_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    for port, lines in raw_logs.items():
        (log_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    for port, lines in clean_logs.items():
        (log_dir / f"{port}.clean.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def should_store_full_logs(result: str, round_index: int, sample_every: int) -> bool:
    if result != "PASS":
        return True
    return sample_every > 0 and round_index % sample_every == 0


def classify_wake_round(playback_returncode: int, metrics: Dict[str, Any], line_count: int, cp_log_required: bool = True) -> Tuple[str, str, str, bool]:
    if playback_returncode != 0:
        return "BLOCKED", "audio/playback", f"播放失败 returncode={playback_returncode}", False
    if line_count <= 0:
        return "BLOCKED", "serial_logger_or_ports", "播放成功但串口窗口无日志。", False
    if int(metrics.get("crash_marker_count", 0) or 0) > 0 or int(metrics.get("boot_marker_count", 0) or 0) > 0:
        return "FAIL", "firmware_device_stability", "窗口内出现 reboot/crash 标记。", True
    cp_wake = int(metrics.get("cp_wake_count", 0) or 0)
    ap_wake = int(metrics.get("ap_wake_count", 0) or 0)
    asr_wake = asr_wake_count(metrics)
    if (not cp_log_required or cp_wake >= 1) and ap_wake >= 1 and asr_wake >= 1:
        source_label = "CP/AP/ASR" if cp_log_required else "AP/ASR（CP 可选）"
        return "PASS", "pass", f"{source_label} 均观察到目标唤醒闭环。", True
    missing = []
    if cp_log_required and cp_wake < 1:
        missing.append("CP_WAKE")
    if ap_wake < 1:
        missing.append("AP_WAKE")
    if asr_wake < 1:
        missing.append("ASR_WAKE")
    return "FAIL", "firmware_device_or_audio_path", "播放成功但目标唤醒闭环缺失：" + ",".join(missing), True


def find_ap_wake_time(clean_logs: Dict[str, List[str]]) -> Optional[datetime]:
    for line in clean_logs.get("COM14", []):
        if AP_WAKE_RE.search(line):
            ts = parse_prefixed_timestamp(line)
            if ts:
                return ts
    return None


def find_session_anchor(clean_logs: Dict[str, List[str]], fallback: Optional[datetime]) -> Tuple[Optional[datetime], int, str]:
    for line in clean_logs.get("COM14", []):
        ts = parse_prefixed_timestamp(line)
        if not ts:
            continue
        match = SESSION_TIMER_RE.search(line) or TIMEOUT_REFRESH_RE.search(line)
        if match:
            return ts, int(match.group(1)), line
    return fallback, 15, "fallback_to_ap_wake"


def session_refresh_observed(clean_logs: Dict[str, List[str]]) -> bool:
    for line in clean_logs.get("COM14", []):
        if SESSION_TIMER_RE.search(line) or TIMEOUT_REFRESH_RE.search(line):
            return True
    return False


def build_round_row(
    *,
    round_index: int,
    scenario_id: str,
    scenario_name: str,
    started_at: datetime,
    ended_at: datetime,
    result: str,
    counted: bool,
    attribution: str,
    reason: str,
    playback_returncode: Optional[int],
    metrics: Optional[Dict[str, Any]],
    line_count: int,
    evidence_dir: Path,
    precondition_result: str = "",
    timing_bucket: str = "",
    target_delay_s: Optional[float] = None,
    timeout_s: Optional[int] = None,
    deadline_margin_ms: Optional[int] = None,
) -> Dict[str, Any]:
    metrics = metrics or {}
    return {
        "round": round_index,
        "scenario_id": scenario_id,
        "scenario_name": scenario_name,
        "started_at": started_at.isoformat(timespec="milliseconds"),
        "ended_at": ended_at.isoformat(timespec="milliseconds"),
        "result": result,
        "counted_in_rate": bool(counted),
        "attribution": attribution,
        "reason": reason,
        "playback_returncode": "" if playback_returncode is None else playback_returncode,
        "precondition_result": precondition_result,
        "timing_bucket": timing_bucket,
        "target_delay_s": "" if target_delay_s is None else target_delay_s,
        "timeout_s": "" if timeout_s is None else timeout_s,
        "deadline_margin_ms": "" if deadline_margin_ms is None else deadline_margin_ms,
        "cp_wake_count": int(metrics.get("cp_wake_count", 0) or 0),
        "ap_wake_count": int(metrics.get("ap_wake_count", 0) or 0),
        "asr_wake_count": asr_wake_count(metrics),
        "cp_command_count": int(metrics.get("cp_command_count", 0) or 0),
        "ap_cloud_tts_play_count": int(metrics.get("ap_cloud_tts_play_count", 0) or 0),
        "ap_instruction_broadcast_count": int(metrics.get("ap_instruction_broadcast_count", 0) or 0),
        "boot_marker_count": int(metrics.get("boot_marker_count", 0) or 0),
        "crash_marker_count": int(metrics.get("crash_marker_count", 0) or 0),
        "line_count": int(line_count),
        "evidence_dir": rel(evidence_dir),
    }


class StressRun:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.env = load_env()
        self.serial_context = serial_context_from_env(self.env)
        self.capabilities = runtime_capabilities()
        self.cp_log_required = bool(self.capabilities.get("cp_log", True))
        device_cfg = self.env.get("device", {}) if isinstance(self.env.get("device"), dict) else {}
        self.wake_word = args.wake_word or str(self.env.get("current_wakeup_word") or device_cfg.get("wake_word") or "小美小美")
        self.device_key = str(args.device_key or default_playback_device_key(self.env)).strip()
        self.end_at = args.end_at or tomorrow_0830()
        self.run_dir = (Path(args.run_dir) if args.run_dir else BDD_ROOT / "debug" / "stress" / f"{stamp()}_wake_stress").resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir = self.run_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.rounds_dir = self.run_dir / "rounds"
        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        self.results_csv = self.run_dir / "round_results.csv"
        self.summary_path = self.run_dir / "stress_summary.json"
        self.report_path = self.run_dir / "stress_report.md"
        self.status_path = self.run_dir / "live_status.json"
        self.managed = None
        self.logger_proc = None
        self.rows: List[Dict[str, Any]] = []
        self.round_index = 0
        self.last_interaction_end: Optional[datetime] = None
        self.consecutive_blocked = 0
        self.consecutive_fail = 0
        self.recognition_intervals = [float(item) for item in args.recognition_intervals.split(",") if item.strip()]
        self.recognition_interval_index = 0

        self.audio_dir = self.run_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.wake_audio = self.audio_dir / "wake.wav"
        self.wake_manifest = build_sequence([{"type": "tts", "text": self.wake_word}], self.wake_audio)
        self.wake_audio_duration_ms = int(self.wake_manifest.get("duration_ms", 0) or 0)
        self.before_snapshot: Optional[Dict[str, Any]] = None
        self.snapshot_refs: Dict[str, Any] = {}
        self.snapshot_events: List[Dict[str, Any]] = []

    def snapshot_task(self) -> Dict[str, Any]:
        return {
            "schema": "polaris.wake-stress.task.v1",
            "task_id": "wake_stress",
            "expected_state": {"wake_evidence": "expected"},
        }

    def snapshot_env(self) -> Tuple[Path, Dict[str, Any]]:
        env_file = os.environ.get("POLARIS_ENV_FILE", "").strip()
        if env_file:
            env_path = Path(env_file)
            if not env_path.is_absolute():
                env_path = WORKSPACE_ROOT / env_path
            try:
                return env_path, json.loads(env_path.read_text(encoding="utf-8-sig"))
            except Exception:
                pass
        env_path, payload = load_default_env(WORKSPACE_ROOT)
        return env_path, payload

    def capture_snapshot(self, phase: str, *, file_name: str = "", extra_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        env_path, env_payload = self.snapshot_env()
        snapshot = build_snapshot(
            env_path=env_path,
            env_payload=env_payload,
            task=self.snapshot_task(),
            phase=phase,
            run_dir=self.run_dir,
            evidence_roots=[self.run_dir],
            scope={"runner": "run_wake_stress"},
            extra_state=extra_state,
        )
        path = write_snapshot(self.run_dir, snapshot, file_name=file_name or None)
        self.snapshot_events.append({"phase": phase, "path": rel(path), "created_at": datetime.now().isoformat(timespec="milliseconds")})
        return snapshot

    def capture_round_snapshot(self, row: Dict[str, Any], *, incident: bool = False) -> None:
        prefix = "incident" if incident else "checkpoint"
        result_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(row.get("result", "")).lower()).strip("_") or "round"
        phase = f"{prefix}_{int(row.get('round', 0) or 0):06d}_{result_label}"
        self.capture_snapshot(phase, file_name=f"snapshot_{phase}.json", extra_state={"round": row})

    def write_feature_plan(self) -> None:
        feature_text = f"""# Auto-generated by run_wake_stress.py
功能: Polaris 唤醒率压测
  背景:
    假如 使用本地串口配置和播放设备
    而且 调试产物写入 satellite/cucumber-agent-testing/debug/stress

  @first_wake_rate @stress
  场景: 首次唤醒率压测
    当 在待唤醒状态循环播放唤醒词 "{self.wake_word}"
    那么 统计 CP/AP/ASR 唤醒闭环成功率
    而且 播放失败、串口缺失、设备重启和崩溃需要单独归因

  @recognition_mode_wake_rate @stress
  场景: 识别模式下唤醒率压测
    当 每轮先进入识别模式再在安全窗口内播放目标唤醒词 "{self.wake_word}"
    那么 统计目标唤醒成功率
    而且 前置首次唤醒失败不计入目标唤醒率
    而且 临界超时灰区不计入主成功率
"""
        (self.run_dir / "cucumber_wake_stress.feature").write_text(feature_text, encoding="utf-8")
        plan = {
            "framework": "Polaris Cucumber Agent Testing",
            "mode": "stress-execute",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "end_at": self.end_at.isoformat(timespec="seconds"),
            "wake_word": self.wake_word,
            "device_key": self.device_key,
            "playback_device": playback_device_label(self.device_key),
            "scenarios": [
                {
                    "scenario_id": "first_wake_rate",
                    "source_test_item": "唤醒/首次唤醒",
                    "assertions": ["playback=0", "cp_wake>=1", "ap_wake>=1", "asr_wake>=1"],
                },
                {
                    "scenario_id": "recognition_mode_wake_rate",
                    "source_test_item": "唤醒/识别模式唤醒",
                    "assertions": [
                        "pre_first_wake_success=true",
                        "target_timing_bucket=SAFE",
                        "target_cp_wake>=1",
                        "target_ap_wake>=1",
                        "target_asr_wake>=1",
                    ],
                },
            ],
        }
        write_json(self.run_dir / "cucumber_stress_plan.json", plan)

    def append_row(self, row: Dict[str, Any]) -> None:
        self.rows.append(row)
        exists = self.results_csv.exists()
        with self.results_csv.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS)
            if not exists:
                writer.writeheader()
            writer.writerow({field: row.get(field, "") for field in RESULT_FIELDS})
        if row["result"] == "BLOCKED":
            self.consecutive_blocked += 1
        else:
            self.consecutive_blocked = 0
        if row["result"] == "FAIL":
            self.consecutive_fail += 1
        else:
            self.consecutive_fail = 0
        if row["result"] != "PASS":
            self.capture_round_snapshot(row, incident=True)
        elif self.round_index > 0 and self.round_index % 5 == 0:
            self.capture_round_snapshot(row, incident=False)
        self.write_summary()

    def aggregate(self) -> Dict[str, Any]:
        scenario_payloads = {}
        for scenario_id in ("first_wake_rate", "recognition_mode_wake_rate"):
            items = [row for row in self.rows if row["scenario_id"] == scenario_id]
            counted = [row for row in items if row.get("counted_in_rate") is True]
            pass_count = sum(1 for row in counted if row["result"] == "PASS")
            fail_count = sum(1 for row in counted if row["result"] == "FAIL")
            counts: Dict[str, int] = {}
            for row in items:
                counts[row["result"]] = counts.get(row["result"], 0) + 1
            scenario_payloads[scenario_id] = {
                "total_rounds": len(items),
                "counted_rounds": len(counted),
                "pass": pass_count,
                "fail": fail_count,
                "rate": None if not counted else round(pass_count / len(counted), 6),
                "counts": counts,
            }
        return {
            "status": "RUNNING" if datetime.now() < self.end_at else "DONE",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "run_dir": rel(self.run_dir),
            "end_at": self.end_at.isoformat(timespec="seconds"),
            "wake_word": self.wake_word,
            "device_key": self.device_key,
            "playback_device": playback_device_label(self.device_key),
            "wake_audio_duration_ms": self.wake_audio_duration_ms,
            "cp_log_required": self.cp_log_required,
            "capabilities": self.capabilities,
            "round_total": len(self.rows),
            "scenarios": scenario_payloads,
            "managed_session": managed_session_payload(self.managed),
            "snapshots": self.snapshot_refs,
            "snapshot_events": self.snapshot_events[-50:],
            "latest_rounds": self.rows[-10:],
        }

    def write_summary(self) -> None:
        payload = self.aggregate()
        write_json(self.summary_path, payload)
        write_json(self.status_path, payload)
        self.report_path.write_text(self.render_report(payload), encoding="utf-8")

    def render_report(self, payload: Dict[str, Any]) -> str:
        lines = [
            "# Polaris Cucumber 唤醒压测报告",
            "",
            f"- 状态：`{payload['status']}`",
            f"- 运行目录：`{payload['run_dir']}`",
            f"- 截止时间：`{payload['end_at']}`",
            f"- 唤醒词：`{payload['wake_word']}`",
            f"- 声卡：`{payload.get('playback_device') or payload['device_key']}`",
            f"- 唤醒音频时长：`{payload['wake_audio_duration_ms']}ms`",
            f"- 总轮次：`{payload['round_total']}`",
            f"- 快照 diff：`{(payload.get('snapshots') or {}).get('diff', '')}`",
            f"- 快照 coverage：`{(payload.get('snapshots') or {}).get('coverage', '')}`",
            "",
            "| 场景 | 总轮次 | 计入分母 | PASS | FAIL | 成功率 | 结果分布 |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
        for scenario_id, item in payload["scenarios"].items():
            rate = "" if item["rate"] is None else f"{item['rate'] * 100:.3f}%"
            lines.append(
                f"| {scenario_id} | {item['total_rounds']} | {item['counted_rounds']} | {item['pass']} | {item['fail']} | {rate} | `{json.dumps(item['counts'], ensure_ascii=False)}` |"
            )
        lines.extend(
            [
                "",
                "## 统计口径",
                "",
                "- `PASS`：目标唤醒在有效窗口内完成 CP/AP/ASR 闭环。",
                "- `FAIL`：播放和串口正常，但目标唤醒闭环缺失或出现 reboot/crash。",
                "- `BLOCKED`：播放、串口、前置首次唤醒、联网/环境导致无法验证。",
                "- `TIMING_AMBIGUOUS`：识别模式目标唤醒落入临界超时灰区，不计入主成功率。",
                "- `OUT_OF_WINDOW`：目标唤醒明确越过识别模式窗口，不计入识别模式唤醒率。",
                "",
                "## 最近 10 轮",
                "",
                "| 轮次 | 场景 | 结果 | 计入 | 原因 | 证据 |",
                "|---:|---|---|---|---|---|",
            ]
        )
        for row in payload.get("latest_rounds", []):
            reason = str(row.get("reason", "")).replace("|", "\\|")
            if len(reason) > 100:
                reason = reason[:97] + "..."
            lines.append(
                f"| {row.get('round')} | {row.get('scenario_id')} | `{row.get('result')}` | `{row.get('counted_in_rate')}` | {reason} | `{row.get('evidence_dir')}` |"
            )
        return "\n".join(lines) + "\n"

    def ensure_idle(self) -> None:
        if self.last_interaction_end is None:
            return
        wait_s = float(self.args.idle_wait_s)
        elapsed = (datetime.now() - self.last_interaction_end).total_seconds()
        remaining = wait_s - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def run_network_recovery(self, label: str) -> Dict[str, Any]:
        log_path = self.logs_dir / f"{label}_{datetime.now().strftime('%H%M%S')}.log"
        started = datetime.now()
        result = run_adapter_action_capture(
            adapter_id="network.local",
            action="ensure_online",
            params={"ssid": str(self.env.get("network", {}).get("wifi_ssid", "") if isinstance(self.env.get("network"), dict) else ""), "pwd": str(self.env.get("network", {}).get("wifi_password", "") if isinstance(self.env.get("network"), dict) else "")},
            timeout_s=180,
            execute=True,
            allow_side_effects=True,
            log_path=log_path,
        )
        return {
            "label": label,
            "started_at": started.isoformat(timespec="seconds"),
            "ended_at": datetime.now().isoformat(timespec="seconds"),
            "returncode": result.returncode,
            "log_path": rel(log_path),
            "adapter": result.to_dict(),
        }

    def maybe_recover(self) -> None:
        if self.consecutive_blocked >= int(self.args.recover_after_blocked):
            event = self.run_network_recovery("wake_stress_recover_blocked")
            events_path = self.run_dir / "recovery_events.jsonl"
            with events_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.consecutive_blocked = 0

    def check_logger(self) -> None:
        if self.managed is None or self.logger_proc is None:
            return
        if self.logger_proc.poll() is not None:
            raise RuntimeError(f"serial logger exited: pid={self.managed.logger_pid} rc={self.logger_proc.returncode}")
        heartbeat = latest_heartbeat(self.managed.session_dir)
        ports = heartbeat.get("ports", {})
        if ports and not all(bool(item.get("is_open")) for item in ports.values()):
            raise RuntimeError(f"serial logger port not open: {ports}")

    def run_first_wake_round(self) -> None:
        started = datetime.now()
        self.ensure_idle()
        self.round_index += 1
        round_dir = self.rounds_dir / f"{self.round_index:06d}_first_wake"
        round_dir.mkdir(parents=True, exist_ok=True)
        playback = run_playback(self.wake_audio, self.device_key, round_dir, skip_probe=True, log_prefix="wake")
        time.sleep(float(self.args.first_observe_s))
        ended = datetime.now()
        assert self.managed is not None
        raw, clean, window_summary, metrics, key_lines = gather_logs(self.managed.session_dir, started, ended)
        line_count = sum_line_count(raw)
        result, attribution, reason, counted = classify_wake_round(playback.returncode, metrics, line_count, self.cp_log_required)
        if should_store_full_logs(result, self.round_index, int(self.args.sample_pass_logs_every)):
            write_round_logs(round_dir, raw, clean)
        row = build_round_row(
            round_index=self.round_index,
            scenario_id="first_wake_rate",
            scenario_name="首次唤醒率压测",
            started_at=started,
            ended_at=ended,
            result=result,
            counted=counted,
            attribution=attribution,
            reason=reason,
            playback_returncode=playback.returncode,
            metrics=metrics,
            line_count=line_count,
            evidence_dir=round_dir,
        )
        write_json(
            round_dir / "round.json",
            {
                "row": row,
                "window_summary": window_summary,
                "metrics": metrics,
                "key_lines": key_lines[:120],
                "wake_manifest": self.wake_manifest,
            },
        )
        self.last_interaction_end = ended
        self.append_row(row)

    def next_recognition_interval(self) -> float:
        value = self.recognition_intervals[self.recognition_interval_index % len(self.recognition_intervals)]
        self.recognition_interval_index += 1
        return value

    def wait_for_precondition(self, start_dt: datetime, timeout_s: float) -> Tuple[str, Dict[str, Any], Dict[str, List[str]], Dict[str, List[str]], dict, List[str], Optional[datetime], int, str]:
        assert self.managed is not None
        deadline = time.time() + timeout_s
        last_payload = ({}, {}, {}, {}, [], None, 15, "")
        while time.time() < deadline:
            now = datetime.now()
            raw, clean, window_summary, metrics, key_lines = gather_logs(self.managed.session_dir, start_dt, now)
            ap_wake_ts = find_ap_wake_time(clean)
            anchor_ts, timeout_value, anchor_line = find_session_anchor(clean, ap_wake_ts)
            last_payload = (raw, clean, window_summary, metrics, key_lines, anchor_ts, timeout_value, anchor_line)
            if (
                (not self.cp_log_required or int(metrics.get("cp_wake_count", 0) or 0) >= 1)
                and int(metrics.get("ap_wake_count", 0) or 0) >= 1
                and asr_wake_count(metrics) >= 1
                and anchor_ts is not None
            ):
                return ("PASS", *last_payload)
            time.sleep(0.25)
        return ("PRECONDITION_FAIL", *last_payload)

    def classify_timing(self, anchor_ts: datetime, target_start: datetime, timeout_s: int) -> Tuple[str, int]:
        deadline = anchor_ts + timedelta(seconds=timeout_s)
        decision_ts = target_start + timedelta(milliseconds=self.wake_audio_duration_ms)
        margin_ms = int((deadline - decision_ts).total_seconds() * 1000)
        guard_ms = int(self.args.timing_guard_ms)
        if target_start >= deadline:
            return "OUT_OF_WINDOW", margin_ms
        if margin_ms < 0:
            return "TIMING_AMBIGUOUS", margin_ms
        if margin_ms < guard_ms:
            return "TIMING_AMBIGUOUS", margin_ms
        return "SAFE", margin_ms

    def run_recognition_wake_round(self) -> None:
        started = datetime.now()
        self.ensure_idle()
        self.round_index += 1
        round_dir = self.rounds_dir / f"{self.round_index:06d}_recognition_mode_wake"
        round_dir.mkdir(parents=True, exist_ok=True)

        pre_playback = run_playback(self.wake_audio, self.device_key, round_dir, skip_probe=True, log_prefix="pre_wake")
        pre_status, pre_raw, pre_clean, pre_window, pre_metrics, pre_key_lines, anchor_ts, timeout_s, anchor_line = self.wait_for_precondition(
            started, float(self.args.precondition_observe_s)
        )
        if pre_playback.returncode != 0:
            pre_status = "PRECONDITION_BLOCKED"
        if pre_status != "PASS" or anchor_ts is None:
            ended = datetime.now()
            result = "BLOCKED" if pre_playback.returncode != 0 or sum_line_count(pre_raw) == 0 else "PRECONDITION_FAIL"
            reason = (
                f"前置首次唤醒未成立，pre_status={pre_status}, playback={pre_playback.returncode}, "
                f"cp={pre_metrics.get('cp_wake_count', 0)}, ap={pre_metrics.get('ap_wake_count', 0)}, asr={asr_wake_count(pre_metrics)}"
            )
            write_round_logs(round_dir, pre_raw, pre_clean)
            row = build_round_row(
                round_index=self.round_index,
                scenario_id="recognition_mode_wake_rate",
                scenario_name="识别模式下唤醒率压测",
                started_at=started,
                ended_at=ended,
                result=result,
                counted=False,
                attribution="precondition_first_wake",
                reason=reason,
                playback_returncode=pre_playback.returncode,
                metrics=pre_metrics,
                line_count=sum_line_count(pre_raw),
                evidence_dir=round_dir,
                precondition_result=pre_status,
            )
            write_json(
                round_dir / "round.json",
                {
                    "row": row,
                    "precondition": {
                        "window_summary": pre_window,
                        "metrics": pre_metrics,
                        "key_lines": pre_key_lines[:120],
                    },
                },
            )
            self.last_interaction_end = ended
            self.append_row(row)
            return

        target_delay = self.next_recognition_interval()
        target_due = anchor_ts + timedelta(seconds=target_delay)
        sleep_s = (target_due - datetime.now()).total_seconds()
        if sleep_s > 0:
            time.sleep(sleep_s)

        target_start = datetime.now()
        timing_bucket, margin_ms = self.classify_timing(anchor_ts, target_start, timeout_s)
        target_playback = run_playback(self.wake_audio, self.device_key, round_dir, skip_probe=True, log_prefix="target_wake")
        time.sleep(float(self.args.target_observe_s))
        ended = datetime.now()
        assert self.managed is not None
        raw, clean, window_summary, metrics, key_lines = gather_logs(self.managed.session_dir, target_start, ended)
        line_count = sum_line_count(raw)

        if timing_bucket == "OUT_OF_WINDOW":
            result, attribution, reason, counted = (
                "OUT_OF_WINDOW",
                "test_timing",
                f"目标唤醒开始已超过识别超时窗口，deadline_margin_ms={margin_ms}。",
                False,
            )
        elif timing_bucket == "TIMING_AMBIGUOUS":
            result, attribution, reason, counted = (
                "TIMING_AMBIGUOUS",
                "test_timing_guard",
                f"目标唤醒判决点落入临界超时灰区，deadline_margin_ms={margin_ms}。",
                False,
            )
        else:
            result, attribution, reason, counted = classify_wake_round(target_playback.returncode, metrics, line_count, self.cp_log_required)
            if result == "PASS" and not session_refresh_observed(clean):
                source_label = "CP/AP/ASR" if self.cp_log_required else "AP/ASR"
                reason += f"；本轮未观察到 session timer refresh，仅按 {source_label} 闭环计 PASS。"

        if should_store_full_logs(result, self.round_index, int(self.args.sample_pass_logs_every)):
            write_round_logs(round_dir, raw, clean)
            (round_dir / "precondition_key_lines.txt").write_text("\n".join(pre_key_lines[:120]), encoding="utf-8")
        row = build_round_row(
            round_index=self.round_index,
            scenario_id="recognition_mode_wake_rate",
            scenario_name="识别模式下唤醒率压测",
            started_at=started,
            ended_at=ended,
            result=result,
            counted=counted,
            attribution=attribution,
            reason=reason,
            playback_returncode=target_playback.returncode,
            metrics=metrics,
            line_count=line_count,
            evidence_dir=round_dir,
            precondition_result=pre_status,
            timing_bucket=timing_bucket,
            target_delay_s=target_delay,
            timeout_s=timeout_s,
            deadline_margin_ms=margin_ms,
        )
        write_json(
            round_dir / "round.json",
            {
                "row": row,
                "anchor": {
                    "anchor_ts": anchor_ts.isoformat(timespec="milliseconds"),
                    "anchor_line": anchor_line,
                    "timeout_s": timeout_s,
                    "target_due": target_due.isoformat(timespec="milliseconds"),
                    "target_start": target_start.isoformat(timespec="milliseconds"),
                    "deadline_margin_ms": margin_ms,
                    "timing_bucket": timing_bucket,
                },
                "precondition": {
                    "window_summary": pre_window,
                    "metrics": pre_metrics,
                    "key_lines": pre_key_lines[:120],
                },
                "target": {
                    "window_summary": window_summary,
                    "metrics": metrics,
                    "key_lines": key_lines[:120],
                },
            },
        )
        self.last_interaction_end = ended
        self.append_row(row)

    def choose_task(self, started_at: datetime) -> str:
        first_items = [row for row in self.rows if row["scenario_id"] == "first_wake_rate"]
        recog_items = [row for row in self.rows if row["scenario_id"] == "recognition_mode_wake_rate"]
        if self.args.max_first_rounds and len(first_items) >= self.args.max_first_rounds:
            return "recognition"
        if self.args.max_recognition_rounds and len(recog_items) >= self.args.max_recognition_rounds:
            return "first"
        if not first_items:
            return "first"
        if not recog_items:
            return "recognition"
        elapsed = max(1.0, (datetime.now() - started_at).total_seconds())
        first_time = sum(
            max(0.0, (datetime.fromisoformat(row["ended_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds())
            for row in first_items
        )
        first_share_actual = first_time / elapsed
        return "first" if first_share_actual < float(self.args.first_time_share) else "recognition"

    def reached_round_limits(self) -> bool:
        if not self.args.max_first_rounds and not self.args.max_recognition_rounds:
            return False
        first_count = sum(1 for row in self.rows if row["scenario_id"] == "first_wake_rate")
        recog_count = sum(1 for row in self.rows if row["scenario_id"] == "recognition_mode_wake_rate")
        first_done = not self.args.max_first_rounds or first_count >= self.args.max_first_rounds
        recog_done = not self.args.max_recognition_rounds or recog_count >= self.args.max_recognition_rounds
        return first_done and recog_done

    def run(self) -> int:
        self.write_feature_plan()
        started_at = datetime.now()
        write_json(
            self.run_dir / "stress_start.json",
            {
                "started_at": started_at.isoformat(timespec="seconds"),
                "end_at": self.end_at.isoformat(timespec="seconds"),
                "args": jsonable_args(self.args),
                "wake_manifest": self.wake_manifest,
            },
        )
        self.before_snapshot = self.capture_snapshot("before", file_name="snapshot_before.json", extra_state={"end_at": self.end_at.isoformat(timespec="seconds")})
        try:
            self.managed, self.logger_proc = start_managed_session(self.run_dir, self.serial_context)
            self.write_summary()
            self.run_network_recovery("wake_stress_start_ensure_online")
            while datetime.now() < self.end_at and not self.reached_round_limits():
                self.check_logger()
                task = self.choose_task(started_at)
                if task == "first":
                    self.run_first_wake_round()
                else:
                    self.run_recognition_wake_round()
                self.maybe_recover()
            self.write_summary()
            return 0
        except KeyboardInterrupt:
            self.write_summary()
            return 130
        except Exception as exc:
            error_payload = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "error": repr(exc),
            }
            write_json(self.run_dir / "fatal_error.json", error_payload)
            self.write_summary()
            return 2
        finally:
            if self.managed is not None and self.logger_proc is not None:
                stop_managed_session(self.managed, self.logger_proc)
            payload = self.aggregate()
            payload["status"] = "DONE" if datetime.now() >= self.end_at or self.reached_round_limits() else payload["status"]
            after_snapshot = self.capture_snapshot("after", file_name="snapshot_after.json", extra_state={"summary": payload})
            self.capture_snapshot("final", file_name="snapshot_final.json", extra_state={"summary": payload})
            if self.before_snapshot:
                diff = diff_snapshots(self.before_snapshot, after_snapshot)
                diff_path = write_snapshot_diff(self.run_dir, diff)
                coverage_path = write_snapshot_coverage(self.run_dir, diff.get("coverage", {}))
                self.snapshot_refs = {
                    "before": rel(self.run_dir / "snapshot_before.json"),
                    "after": rel(self.run_dir / "snapshot_after.json"),
                    "final": rel(self.run_dir / "snapshot_final.json"),
                    "diff": rel(diff_path),
                    "coverage": rel(coverage_path),
                    "coverage_summary": diff.get("coverage", {}),
                    "diff_summary": diff.get("summary", {}),
                }
                payload["snapshots"] = self.snapshot_refs
                payload["snapshot_events"] = self.snapshot_events
            write_json(self.summary_path, payload)
            self.report_path.write_text(self.render_report(payload), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Cucumber-style first/recognition-mode wake stress tests.")
    parser.add_argument("--end-at", type=parse_datetime, default=None, help="Local end time, e.g. 2026-05-14 08:30:00")
    parser.add_argument("--wake-word", default="")
    parser.add_argument("--device-key", default="")
    parser.add_argument("--first-time-share", type=float, default=0.40)
    parser.add_argument("--recognition-intervals", default="2,6,10,12")
    parser.add_argument("--first-observe-s", type=float, default=8.0)
    parser.add_argument("--precondition-observe-s", type=float, default=8.0)
    parser.add_argument("--target-observe-s", type=float, default=8.0)
    parser.add_argument("--idle-wait-s", type=float, default=20.0)
    parser.add_argument("--timing-guard-ms", type=int, default=1500)
    parser.add_argument("--sample-pass-logs-every", type=int, default=50)
    parser.add_argument("--recover-after-blocked", type=int, default=5)
    parser.add_argument("--max-first-rounds", type=int, default=0)
    parser.add_argument("--max-recognition-rounds", type=int, default=0)
    parser.add_argument("--run-dir", default="", help="Optional explicit output directory.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    runner = StressRun(args)
    print(runner.run_dir)
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
