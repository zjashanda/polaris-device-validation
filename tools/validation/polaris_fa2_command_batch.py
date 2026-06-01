#!/usr/bin/env python3
from pathlib import Path
import sys

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

import argparse
import csv
import json
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from tools.audio.polaris_audio_builder import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH, ensure_tts_pcm, read_pcm, silence_pcm
from tools.core.polaris_adapter_bridge import run_audio_playback_adapter
from tools.core.polaris_config import add_canonical_log_aliases, configured_log_ports, read_env_config
from tools.core.polaris_runtime import current_session_dir, new_artifact_dir, parse_prefixed_timestamp, read_lines_between
from tools.execution.polaris_case_runner import default_playback_device_key, playback_device_label, sanitize_logs, summarize_window
from tools.execution.polaris_doc_case_runner import collect_metrics


DEFAULT_COMMAND_FILE = Path("docs") / "fa2命令词.txt"
DEFAULT_DEVICE_KEY = ""
DEFAULT_WAKE_WORD = "小美小美"
KEY_MARKERS = (
    "WAKE(",
    "wakeup_callback",
    "offline_wakeup",
    "online_wakeup",
    "offline_asr_callbak",
    "online_asr_callbak",
    "audioBroadcast",
    "stream_tts url id",
    "TTS recv",
    "TTS playing",
    "play next tone",
    "PLAYING",
    "PLAYBACK_COMPLETE",
    "asrInvalid",
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def read_commands(path: Path) -> List[str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    commands: List[str] = []
    seen = set()
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        commands.append(line)
    return commands


def pcm_duration_ms(pcm: bytes) -> int:
    return int(len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS) * 1000)


def append_tts(combined: bytearray, text: str) -> dict:
    pcm_path = ensure_tts_pcm(text)
    pcm = read_pcm(pcm_path)
    combined.extend(pcm)
    return {
        "type": "tts",
        "text": text,
        "pcm_path": str(pcm_path),
        "bytes": len(pcm),
        "duration_ms": pcm_duration_ms(pcm),
    }


def append_silence(combined: bytearray, duration_ms: int) -> dict:
    pcm = silence_pcm(duration_ms)
    combined.extend(pcm)
    return {"type": "silence", "duration_ms": duration_ms, "bytes": len(pcm)}


def build_batch_audio(
    commands: List[str],
    output_wav: Path,
    wake_word: str,
    wake_gap_ms: int,
    post_command_gap_ms: int,
) -> dict:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    combined = bytearray()
    entries: List[dict] = []
    for index, command in enumerate(commands, start=1):
        segment_start_ms = pcm_duration_ms(combined)
        steps: List[dict] = []
        wake_start_ms = pcm_duration_ms(combined)
        wake_step = append_tts(combined, wake_word)
        wake_end_ms = pcm_duration_ms(combined)
        steps.append(wake_step)
        steps.append(append_silence(combined, wake_gap_ms))
        command_start_ms = pcm_duration_ms(combined)
        command_step = append_tts(combined, command)
        command_end_ms = pcm_duration_ms(combined)
        steps.append(command_step)
        steps.append(append_silence(combined, post_command_gap_ms))
        segment_end_ms = pcm_duration_ms(combined)
        entries.append(
            {
                "index": index,
                "command": command,
                "segment_start_ms": segment_start_ms,
                "wake_start_ms": wake_start_ms,
                "wake_end_ms": wake_end_ms,
                "command_start_ms": command_start_ms,
                "command_end_ms": command_end_ms,
                "segment_end_ms": segment_end_ms,
                "steps": steps,
            }
        )

    with wave.open(str(output_wav), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(bytes(combined))

    manifest = {
        "output_wav": str(output_wav),
        "wake_word": wake_word,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "sample_width": SAMPLE_WIDTH,
        "wake_gap_ms": wake_gap_ms,
        "post_command_gap_ms": post_command_gap_ms,
        "duration_ms": pcm_duration_ms(combined),
        "command_count": len(commands),
        "entries": entries,
    }
    output_wav.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def run_playback(audio_file: Path, device_key: str, output_dir: Path, timeout_s: int) -> dict:
    device_key = str(device_key or "").strip()
    started_at = datetime.now()
    playback_started_at: Optional[datetime] = None
    lines: List[str] = []
    capture = run_audio_playback_adapter(
        audio_file,
        device_key,
        timeout_s=timeout_s,
        stream_log_path=output_dir / "play_combined.log",
    )
    lines = capture.stdout_lines
    playback_started_at = capture.playback_started_at
    returncode = capture.completed.returncode
    finished_at = datetime.now()
    payload = {
        "cmd": list(capture.completed.args),
        "returncode": returncode,
        "device_key": device_key,
        "playback_device": playback_device_label(device_key),
        "process_started_at": started_at.isoformat(timespec="milliseconds"),
        "playback_started_at": (playback_started_at or started_at).isoformat(timespec="milliseconds"),
        "finished_at": finished_at.isoformat(timespec="milliseconds"),
        "stdout_lines": lines,
        "log_path": str(output_dir / "play_combined.log"),
        "adapter_executor": capture.action_result.to_dict(),
    }
    (output_dir / "playback.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def run_command_batch(
    *,
    command_file: Path = DEFAULT_COMMAND_FILE,
    device_key: str = DEFAULT_DEVICE_KEY,
    wake_word: str = DEFAULT_WAKE_WORD,
    wake_gap_ms: int = 900,
    post_command_gap_ms: int = 6500,
    window_pad_before_ms: int = 500,
    window_pad_after_ms: int = 1500,
    label: str = "fa2_full",
    limit: int = 0,
    start_index: int = 1,
) -> dict:
    device_key = str(device_key or default_playback_device_key(read_env_config())).strip()
    session_dir = current_session_dir()
    commands = read_commands(command_file)
    if start_index > 1:
        commands = commands[start_index - 1 :]
    if limit:
        commands = commands[:limit]
    if not commands:
        raise ValueError("no commands to validate")

    output_dir = new_artifact_dir(f"fa2_command_batch_{label}", session_dir=session_dir)
    audio_file = output_dir / "audio" / "fa2_wake_before_each_command.wav"
    manifest = build_batch_audio(
        commands,
        audio_file,
        wake_word=wake_word,
        wake_gap_ms=wake_gap_ms,
        post_command_gap_ms=post_command_gap_ms,
    )
    timeout_s = max(120, int(manifest["duration_ms"] / 1000) + 600)
    playback = run_playback(audio_file, device_key, output_dir, timeout_s)
    playback_started_at = datetime.fromisoformat(str(playback["playback_started_at"]))
    finished_at = datetime.fromisoformat(str(playback["finished_at"]))
    full_logs = collect_full_logs(session_dir, playback_started_at - timedelta(seconds=2), finished_at + timedelta(seconds=2), output_dir)
    rows = analyze_entries(
        manifest,
        full_logs,
        playback_started_at,
        int(playback["returncode"]),
        output_dir,
        window_pad_before_ms,
        window_pad_after_ms,
    )
    metadata = {
        "generated_at": now_iso(),
        "session_dir": str(session_dir),
        "output_dir": str(output_dir),
        "command_file": str(command_file),
        "device_key": device_key,
        "playback_device": playback_device_label(device_key),
        "audio_file": str(audio_file),
        "wake_word": wake_word,
        "playback_returncode": playback["returncode"],
        "playback_started_at": playback["playback_started_at"],
        "playback_finished_at": playback["finished_at"],
        "audio_duration_ms": manifest["duration_ms"],
        "configured_log_ports": configured_log_ports(),
    }
    write_reports(rows, output_dir, metadata)
    counts = {k: sum(1 for row in rows if row["result"] == k) for k in ("PASS", "FAIL", "BLOCKED")}
    return {
        "returncode": 0,
        "output_dir": output_dir,
        "summary_path": output_dir / "fa2_command_batch_summary.json",
        "total": len(rows),
        "counts": counts,
    }


def filter_lines(lines: List[str], start_dt: datetime, end_dt: datetime) -> List[str]:
    selected: List[str] = []
    for line in lines:
        ts = parse_prefixed_timestamp(line)
        if ts is None:
            continue
        if start_dt <= ts <= end_dt:
            selected.append(line)
    return selected


def collect_full_logs(session_dir: Path, start_dt: datetime, end_dt: datetime, output_dir: Path) -> Dict[str, List[str]]:
    logs: Dict[str, List[str]] = {}
    logs_dir = output_dir / "full_window_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for port in configured_log_ports():
        lines = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
        logs[port] = lines
        (logs_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    add_canonical_log_aliases(logs)
    return logs


def key_lines(clean_logs: Dict[str, List[str]], limit: int = 80) -> List[str]:
    found: List[str] = []
    for port in ("COM12", "COM13", "COM14"):
        for line in clean_logs.get(port, []):
            if any(marker in line for marker in KEY_MARKERS):
                found.append(line)
                if len(found) >= limit:
                    return found
    return found


def judge_command(command: str, metrics: dict, line_count: int, playback_returncode: int) -> dict:
    wake_count = metrics.get("cp_wake_count", 0) + metrics.get("ap_wake_count", 0) + metrics.get("wb_wake_count", 0) + metrics.get("wb_online_wake_count", 0)
    asr_count = metrics.get("ap_asr_count", 0) + metrics.get("wb_asr_count", 0)
    command_evidence = (
        asr_count
        + metrics.get("cp_command_count", 0)
        + metrics.get("unique_command_keyword_count", 0)
        + len(metrics.get("ap_online_asr_texts", []))
        + len(metrics.get("tone_ids", []))
        + metrics.get("ap_instruction_broadcast_count", 0)
        + metrics.get("ap_cloud_tts_recv_count", 0)
        + metrics.get("wb_playback_end_count", 0)
    )
    if playback_returncode != 0:
        return {
            "result": "BLOCKED",
            "failure_type": "BLOCKED_AUDIO_ROUTE",
            "reason": "播放工具返回非 0，未进入可靠日志判定。",
        }
    if line_count <= 0:
        return {
            "result": "BLOCKED",
            "failure_type": "BLOCKED_LOG_CAPTURE",
            "reason": "该命令窗口未采集到 CP/ASR/AP 日志。",
        }
    if wake_count <= 0:
        return {
            "result": "FAIL",
            "failure_type": "WAKE_NOT_OBSERVED",
            "reason": "已先播放唤醒词，但该窗口未观察到唤醒证据。",
        }
    if command_evidence <= 0:
        return {
            "result": "FAIL",
            "failure_type": "COMMAND_NOT_OBSERVED",
            "reason": "有唤醒证据，但未观察到命令词 ASR/keyword/TTS/tone/云控闭环证据。",
        }
    matched_texts = [str(item) for item in metrics.get("ap_online_asr_texts", []) + metrics.get("recognized_command_keywords", [])]
    strict_match = any(command in text or text in command for text in matched_texts if text)
    return {
        "result": "PASS" if strict_match or command_evidence > 0 else "FAIL",
        "failure_type": "" if command_evidence > 0 else "COMMAND_NOT_OBSERVED",
        "reason": "唤醒和命令链路均有证据；strict_text_match=%s。" % strict_match,
    }


def analyze_entries(
    manifest: dict,
    full_logs: Dict[str, List[str]],
    playback_started_at: datetime,
    playback_returncode: int,
    output_dir: Path,
    window_pad_before_ms: int,
    window_pad_after_ms: int,
) -> List[dict]:
    rows: List[dict] = []
    detail_dir = output_dir / "per_command"
    detail_dir.mkdir(parents=True, exist_ok=True)
    for entry in manifest["entries"]:
        index = int(entry["index"])
        start_dt = playback_started_at + timedelta(milliseconds=max(0, int(entry["segment_start_ms"]) - window_pad_before_ms))
        end_dt = playback_started_at + timedelta(milliseconds=int(entry["segment_end_ms"]) + window_pad_after_ms)
        raw_logs = {
            port: filter_lines(lines, start_dt, end_dt)
            for port, lines in full_logs.items()
            if port in {"COM12", "COM13", "COM14"} or port.upper().startswith("COM")
        }
        add_canonical_log_aliases(raw_logs)
        clean_logs = sanitize_logs(raw_logs)
        window_summary = summarize_window(clean_logs)
        metrics = collect_metrics(clean_logs, window_summary)
        line_count = sum(len(lines) for lines in clean_logs.values())
        diagnosis = judge_command(entry["command"], metrics, line_count, playback_returncode)
        key = key_lines(clean_logs)
        payload = {
            "index": index,
            "command": entry["command"],
            "window_start": start_dt.isoformat(timespec="milliseconds"),
            "window_end": end_dt.isoformat(timespec="milliseconds"),
            "line_count": line_count,
            "metrics": metrics,
            "diagnosis": diagnosis,
            "key_lines": key,
        }
        detail_path = detail_dir / f"{index:03d}.json"
        detail_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(
            {
                "index": index,
                "command": entry["command"],
                "result": diagnosis["result"],
                "failure_type": diagnosis["failure_type"],
                "reason": diagnosis["reason"],
                "line_count": line_count,
                "cp_wake_count": metrics.get("cp_wake_count", 0),
                "ap_wake_count": metrics.get("ap_wake_count", 0),
                "asr_total": metrics.get("asr_total", 0),
                "cp_command_count": metrics.get("cp_command_count", 0),
                "unique_command_keyword_count": metrics.get("unique_command_keyword_count", 0),
                "ap_online_asr_texts": "|".join(metrics.get("ap_online_asr_texts", [])),
                "recognized_command_keywords": "|".join(metrics.get("recognized_command_keywords", [])),
                "tone_ids": "|".join(str(item) for item in metrics.get("tone_ids", [])),
                "detail_path": str(detail_path),
            }
        )
    return rows


def write_reports(rows: List[dict], output_dir: Path, metadata: dict) -> None:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    summary = {
        **metadata,
        "counts": counts,
        "total": len(rows),
        "rows": rows,
    }
    (output_dir / "fa2_command_batch_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "fa2_command_batch_results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["index", "command", "result"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    lines = [
        "# fa2 命令词批量验证结果",
        "",
        f"- 生成时间：`{metadata['generated_at']}`",
        f"- 命令词总数：`{len(rows)}`",
        f"- 结果统计：`{counts}`",
        f"- 声卡：`{metadata['device_key']}`",
        f"- 音频：`{metadata['audio_file']}`",
        "",
        "| # | 命令词 | 结果 | 原因 | ASR 文本 | keyword |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['index']} | {row['command']} | {row['result']} | {row['reason']} | {row['ap_online_asr_texts']} | {row['recognized_command_keywords']} |"
        )
    (output_dir / "fa2_command_batch_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch-validate fa2 command words by playing wake word before every command.")
    parser.add_argument("--command-file", type=Path, default=DEFAULT_COMMAND_FILE)
    parser.add_argument("--device-key", default=DEFAULT_DEVICE_KEY)
    parser.add_argument("--wake-word", default=DEFAULT_WAKE_WORD)
    parser.add_argument("--wake-gap-ms", type=int, default=900)
    parser.add_argument("--post-command-gap-ms", type=int, default=6500)
    parser.add_argument("--window-pad-before-ms", type=int, default=500)
    parser.add_argument("--window-pad-after-ms", type=int, default=1500)
    parser.add_argument("--label", default="fa2_full")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start-index", type=int, default=1)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_command_batch(
            command_file=args.command_file,
            device_key=args.device_key,
            wake_word=args.wake_word,
            wake_gap_ms=args.wake_gap_ms,
            post_command_gap_ms=args.post_command_gap_ms,
            window_pad_before_ms=args.window_pad_before_ms,
            window_pad_after_ms=args.window_pad_after_ms,
            label=args.label,
            limit=args.limit,
            start_index=args.start_index,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(result["output_dir"])
    print(json.dumps({"total": result["total"], "counts": result["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
