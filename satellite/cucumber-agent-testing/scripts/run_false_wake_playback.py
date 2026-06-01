#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run synthetic playback-based false wake smoke tests."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import struct
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_cucumber import start_managed_session, stop_managed_session  # noqa: E402
from run_wake_stress import asr_wake_count, gather_logs, sum_line_count, write_round_logs  # noqa: E402
from tools.audio.polaris_audio_builder import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH, build_sequence  # noqa: E402
from tools.execution.polaris_case_runner import run_playback  # noqa: E402


DEFAULT_DEVICE_KEY = ""
HUMAN_SENTENCES = [
    "今天我们讨论一下空调节能和室内舒适度。",
    "这是一段普通人声干扰内容，不包含目标唤醒词。",
    "会议将在下午三点开始，请提前准备材料。",
    "窗外的风有点大，但是房间里很安静。",
]


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_output_dir(kind: str) -> Path:
    bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
    if bdd_run_dir:
        return Path(bdd_run_dir).resolve() / "false_wake_playback"
    return BDD_ROOT / "debug" / "false_wake_playback" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{kind}"


def write_noise_wav(path: Path, duration_s: float, amplitude: float, seed: int) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    frames = int(SAMPLE_RATE * duration_s)
    amp = max(0.0, min(1.0, amplitude)) * 32767
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        data = bytearray()
        for _ in range(frames):
            sample = int(rng.uniform(-amp, amp))
            data.extend(struct.pack("<h", sample))
        handle.writeframes(bytes(data))
    manifest = {
        "output_wav": str(path),
        "type": "white_noise",
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "sample_width": SAMPLE_WIDTH,
        "duration_ms": int(duration_s * 1000),
        "amplitude": amplitude,
        "seed": seed,
    }
    path.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build_audio(output_dir: Path, kind: str, duration_s: float, amplitude: float, seed: int) -> tuple[Path, Dict[str, Any]]:
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    if kind == "human_speech":
        steps: List[Dict[str, Any]] = []
        for sentence in HUMAN_SENTENCES:
            steps.append({"type": "tts", "text": sentence})
            steps.append({"type": "silence", "duration_ms": 500})
        return audio_dir / "human_speech.wav", build_sequence(steps, audio_dir / "human_speech.wav")
    return audio_dir / "white_noise.wav", write_noise_wav(audio_dir / "white_noise.wav", duration_s, amplitude, seed)


def run(args: argparse.Namespace) -> int:
    output_dir = (Path(args.output_dir) if args.output_dir else default_output_dir(args.kind)).resolve()
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
        audio_path, manifest = build_audio(output_dir, args.kind, args.duration_s, args.amplitude, args.seed)
        case_dir = output_dir / args.kind
        case_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now()
        playback = run_playback(audio_path, args.device_key, case_dir, skip_probe=True, log_prefix=args.kind)
        time.sleep(args.observe_s)
        ended_at = datetime.now()
        raw, clean, window_summary, metrics, key_lines = gather_logs(session_dir, started_at, ended_at)
        write_round_logs(case_dir, raw, clean)
        line_count = sum_line_count(raw)
        wake_count = int(metrics.get("cp_wake_count", 0) or 0) + int(metrics.get("ap_wake_count", 0) or 0) + asr_wake_count(metrics)
        boot_count = int(metrics.get("boot_marker_count", 0) or 0)
        crash_count = int(metrics.get("crash_marker_count", 0) or 0)
        if playback.returncode != 0:
            result = "BLOCKED"
            attribution = "audio_playback_or_device_key"
            reason = f"干扰音频播放失败 returncode={playback.returncode}。"
        elif line_count <= 0:
            result = "BLOCKED"
            attribution = "serial_logger_or_ports"
            reason = "播放成功但串口窗口无日志。"
        elif boot_count > 0 or crash_count > 0:
            result = "FAIL"
            attribution = "device_reboot_or_crash_during_noise"
            reason = "干扰播放窗口出现 reboot/crash 标记。"
        elif wake_count > 0:
            result = "FAIL"
            attribution = "false_wake_observed"
            reason = f"干扰播放窗口观察到唤醒 marker，总数={wake_count}。"
        else:
            result = "PASS"
            attribution = "pass"
            reason = "干扰播放窗口未观察到唤醒 marker，且无 reboot/crash。"
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "kind": args.kind,
            "result": result,
            "attribution": attribution,
            "reason": reason,
            "run_dir": rel(output_dir),
            "metrics": {
                "line_count": line_count,
                "cp_wake_count": int(metrics.get("cp_wake_count", 0) or 0),
                "ap_wake_count": int(metrics.get("ap_wake_count", 0) or 0),
                "asr_wake_count": asr_wake_count(metrics),
                "wake_marker_total": wake_count,
                "boot_marker_count": boot_count,
                "crash_marker_count": crash_count,
            },
            "audio_manifest": manifest,
            "window_summary": window_summary,
            "key_lines": key_lines[:120],
            "evidence_dir": rel(case_dir),
        }
        write_json(output_dir / "false_wake_playback_summary.json", payload)
        (output_dir / "false_wake_playback_report.md").write_text(render_report(payload), encoding="utf-8")
        write_csv(output_dir / "false_wake_playback_rows.csv", [payload])
        print(output_dir)
        print(json.dumps({"result": result, "attribution": attribution}, ensure_ascii=False))
        return 0 if result in {"PASS", "BLOCKED"} else 1
    finally:
        if managed is not None and logger_proc is not None:
            stop_managed_session(managed, logger_proc)


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = ["kind", "result", "attribution", "reason", "evidence_dir"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def render_report(payload: Dict[str, Any]) -> str:
    metrics = payload.get("metrics", {})
    return "\n".join(
        [
            "# False Wake Playback 报告",
            "",
            f"- 类型：`{payload.get('kind')}`",
            f"- 结论：`{payload.get('result')}`",
            f"- 归因：`{payload.get('attribution')}`",
            f"- 原因：{payload.get('reason')}",
            f"- 唤醒 marker：`{metrics.get('wake_marker_total')}`",
            f"- CP/AP/ASR：`{metrics.get('cp_wake_count')}/{metrics.get('ap_wake_count')}/{metrics.get('asr_wake_count')}`",
            f"- reboot/crash：`{metrics.get('boot_marker_count')}/{metrics.get('crash_marker_count')}`",
            "",
            "## 归因口径",
            "",
            "- 播放失败或串口无日志为 BLOCKED。",
            "- 干扰播放窗口出现唤醒 marker 判为误唤醒 FAIL。",
            "- 合成 TTS/白噪声只能覆盖基础 smoke；正式人声噪、非人声噪、多点噪仍需要标准噪声素材、声压和声场。",
            "",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run synthetic playback-based false wake smoke tests.")
    parser.add_argument("--kind", choices=["human_speech", "white_noise"], required=True)
    parser.add_argument("--device-key", default=DEFAULT_DEVICE_KEY)
    parser.add_argument("--duration-s", type=float, default=20.0)
    parser.add_argument("--amplitude", type=float, default=0.08)
    parser.add_argument("--seed", type=int, default=20260514)
    parser.add_argument("--observe-s", type=float, default=5.0)
    parser.add_argument("--output-dir", default="")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

