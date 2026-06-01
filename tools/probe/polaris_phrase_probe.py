#!/usr/bin/env python3
from pathlib import Path
import sys

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from tools.audio.polaris_audio_builder import build_sequence
from tools.core.polaris_config import add_canonical_log_aliases, configured_log_ports, read_env_config
from tools.execution.polaris_case_runner import default_playback_device_key, playback_device_label, run_playback, sanitize_logs, summarize_window
from tools.execution.polaris_doc_case_runner import collect_metrics
from tools.core.polaris_runtime import current_session_dir, new_artifact_dir, read_lines_between, workspace_root


KEY_MARKERS = (
    "WAKE(",
    "wakeup_callback",
    "offline_wakeup",
    "offline_asr_callbak",
    "cloud.instructions.audioBroadcast",
    "TTS recv with",
    "TTS playing with",
    "ttsplayer play:",
    'ttsplayer report state: play',
    'ttsplayer report state: stop',
    'player":{"status":"play"',
    'player":{"status":"stop"',
    "play audio http",
    "play next tone",
    "play complete",
    "tone player evt",
    "local player status 2 PLAYING",
    "local player status 6 PLAYBACK_COMPLETE",
    "soundplayer status:",
    "offline_tts_callbak",
    "tts ",
    "player reset by \"user\"",
)


def build_key_lines(clean_logs: Dict[str, List[str]]) -> List[str]:
    key_lines: List[str] = []
    for port in ["COM12", "COM13", "COM14"]:
        for line in clean_logs.get(port, []):
            if any(marker in line for marker in KEY_MARKERS):
                key_lines.append(line)
    return key_lines


def write_logs(step_dir: Path, raw_logs: Dict[str, List[str]], clean_logs: Dict[str, List[str]]) -> None:
    logs_dir = step_dir / "window_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for port, lines in raw_logs.items():
        (logs_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    for port, lines in clean_logs.items():
        (logs_dir / f"{port}.clean.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_step_summary(step_payload: dict) -> str:
    metrics = step_payload["metrics"]
    lines = [
        f"# {step_payload['step_id']}",
        "",
        f"- Text: `{step_payload['text']}`",
        f"- Observe window: `{step_payload['started_at']}` ~ `{step_payload['ended_at']}`",
        f"- Playback returncode: `{step_payload['playback']['returncode']}`",
        f"- CP wake/command: `{metrics['cp_wake_count']}` / `{metrics['cp_command_count']}`",
        f"- AP wake/asr: `{metrics['ap_wake_count']}` / `{metrics['ap_asr_count']}`",
        f"- ASR wake/asr: `{metrics['wb_wake_count']}` / `{metrics['wb_asr_count']}`",
        f"- Recognized keywords: `{metrics['recognized_command_keywords']}`",
        f"- Tone ids: `{metrics['tone_ids']}`",
        f"- ASR playback start/end: `{metrics['wb_playback_start_count']}` / `{metrics['wb_playback_end_count']}`",
        f"- ASR TTS callback ids: `{metrics['wb_tts_callback_ids']}`",
        f"- AP TTS fail ids: `{metrics['ap_tts_fail_ids']}`",
        "",
        "## Key lines",
        "",
    ]
    if step_payload["key_lines"]:
        for line in step_payload["key_lines"][:80]:
            lines.append(f"- `{line}`")
    else:
        lines.append("- <none>")
    return "\n".join(lines) + "\n"


def run_probe(texts: List[str], device_key: str, observe_ms: int, label: str) -> Path:
    session_dir = current_session_dir()
    device_key = str(device_key or default_playback_device_key(read_env_config())).strip()

    lock_path = session_dir / ".case_runner.lock"
    lock_fd = None
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode("ascii", errors="ignore"))
    except FileExistsError as exc:
        raise RuntimeError(f"another runner is already active: {lock_path}") from exc

    try:
        execution_dir = new_artifact_dir(f"phrase_probe_{label}", session_dir)
        audio_dir = execution_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        step_results: List[dict] = []
        for index, text in enumerate(texts, start=1):
            step_id = f"step{index:02d}"
            step_dir = execution_dir / step_id
            step_dir.mkdir(parents=True, exist_ok=True)
            audio_file = audio_dir / f"{step_id}.wav"
            audio_manifest = build_sequence([{"type": "tts", "text": text}], audio_file)

            start_dt = datetime.now()
            playback = run_playback(audio_file, device_key, step_dir, log_prefix=f"{step_id}_play")
            time.sleep(observe_ms / 1000.0)
            end_dt = datetime.now()

            raw_logs: Dict[str, List[str]] = {}
            for port in configured_log_ports():
                raw_logs[port] = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
            add_canonical_log_aliases(raw_logs)
            clean_logs = sanitize_logs(raw_logs)
            write_logs(step_dir, raw_logs, clean_logs)

            window_summary = summarize_window(clean_logs)
            metrics = collect_metrics(clean_logs, window_summary)
            key_lines = build_key_lines(clean_logs)

            step_payload = {
                "step_id": step_id,
                "text": text,
                "started_at": start_dt.isoformat(timespec="milliseconds"),
                "ended_at": end_dt.isoformat(timespec="milliseconds"),
                "playback": {
                    "audio_file": str(audio_file),
                    "manifest": audio_manifest,
                    "returncode": playback.returncode,
                },
                "window_summary": window_summary,
                "metrics": metrics,
                "key_lines": key_lines,
            }
            (step_dir / "probe_step.json").write_text(json.dumps(step_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            (step_dir / "probe_step.md").write_text(build_step_summary(step_payload), encoding="utf-8")
            step_results.append(step_payload)

            time.sleep(1.0)

        summary = {
            "label": label,
            "device_key": device_key,
            "playback_device": playback_device_label(device_key),
            "observe_ms": observe_ms,
            "configured_log_ports": configured_log_ports(),
            "execution_dir": str(execution_dir),
            "steps": step_results,
        }
        summary_path = execution_dir / "probe_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary_path
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if lock_path.exists():
            lock_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe phrase playback against Polaris serial logs")
    parser.add_argument("--text", action="append", required=True, help="TTS text to play; repeat the flag for multiple steps")
    parser.add_argument("--device-key", default="")
    parser.add_argument("--observe-ms", type=int, default=12000)
    parser.add_argument("--label", default="manual")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result_path = run_probe(
        texts=args.text,
        device_key=args.device_key,
        observe_ms=args.observe_ms,
        label=args.label,
    )
    print(result_path)


if __name__ == "__main__":
    main()
