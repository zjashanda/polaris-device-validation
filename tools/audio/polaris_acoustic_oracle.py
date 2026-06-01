#!/usr/bin/env python3
"""Acoustic loopback/capture oracle for Polaris media validation."""
from __future__ import annotations

from pathlib import Path
import argparse
import array
import json
import math
import platform
import shutil
import subprocess
import sys
import time
import wave
from datetime import datetime
from typing import Any

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
else:
    ROOT = Path(__file__).resolve().parents[2]

from tools.core.polaris_config import deep_merge, normalize_env_payload, read_json


DEFAULT_THRESHOLDS = {
    "active_threshold_dbfs": -50.0,
    "min_rms_dbfs": -45.0,
    "min_peak_dbfs": -35.0,
    "min_active_duration_ms": 300.0,
    "max_clip_ratio": 0.01,
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_env_payload(env_file: str) -> dict[str, Any]:
    if not env_file:
        local = ROOT / "polaris.local.json"
        if local.exists():
            env_file = str(local)
    if not env_file:
        return {}
    return normalize_env_payload(read_json(resolve_path(env_file)))


def acoustic_config(env_payload: dict[str, Any]) -> dict[str, Any]:
    audio = env_payload.get("audio", {}) if isinstance(env_payload.get("audio"), dict) else {}
    oracle = audio.get("acoustic_oracle", {}) if isinstance(audio.get("acoustic_oracle"), dict) else {}
    thresholds = deep_merge(DEFAULT_THRESHOLDS, oracle.get("thresholds", {}) if isinstance(oracle.get("thresholds"), dict) else {})
    return {
        "capture_device_key": str(audio.get("capture_device_key") or audio.get("loopback_device_key") or audio.get("default_playback_device_key") or "").strip(),
        "capture_device_name": str(audio.get("capture_device_name") or "").strip(),
        "capture_sample_rate": int(audio.get("capture_sample_rate") or oracle.get("sample_rate") or 16000),
        "capture_channels": int(audio.get("capture_channels") or oracle.get("channels") or 1),
        "capture_duration_s": float(audio.get("capture_duration_s") or oracle.get("duration_s") or 5.0),
        "thresholds": thresholds,
    }


def run_cmd(cmd: list[str], timeout_s: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
        check=False,
    )


def probe_laid_capture(install_if_missing: bool = False) -> dict[str, Any]:
    cmd = [sys.executable, "tools/audio/polaris_laid.py", "list", "--direction", "Capture", "--json"]
    if install_if_missing:
        cmd.append("--install-if-missing")
    completed = run_cmd(cmd)
    payload: Any = None
    stdout = completed.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except Exception:
            payload = stdout
    return {
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": completed.stderr.strip(),
        "devices": payload,
    }


def normalize_devices(devices: Any) -> list[dict[str, Any]]:
    if isinstance(devices, dict):
        return [devices]
    if isinstance(devices, list):
        return [item for item in devices if isinstance(item, dict)]
    return []


def friendly_name_for_key(device_key: str, laid_payload: dict[str, Any]) -> str:
    if not device_key:
        return ""
    for device in normalize_devices(laid_payload.get("devices")):
        if str(device.get("DeviceKey") or "").strip() == device_key:
            return str(device.get("FriendlyName") or "").strip()
    return ""


def dbfs(value: float, full_scale: float) -> float:
    if value <= 0:
        return -120.0
    return 20.0 * math.log10(max(value, 1e-12) / full_scale)


def samples_from_wav(path: Path) -> tuple[list[int], dict[str, Any]]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if sample_width == 2:
        values = array.array("h")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        samples = list(values)
        full_scale = 32767.0
    elif sample_width == 1:
        samples = [int(byte) - 128 for byte in raw]
        full_scale = 127.0
    elif sample_width == 4:
        values = array.array("i")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        samples = list(values)
        full_scale = 2147483647.0
    else:
        raise ValueError(f"unsupported wav sample width: {sample_width}")
    meta = {
        "path": str(path),
        "channels": channels,
        "sample_width": sample_width,
        "sample_rate": sample_rate,
        "frames": frames,
        "duration_s": frames / float(sample_rate or 1),
        "full_scale": full_scale,
    }
    return samples, meta


def downmix_abs_samples(samples: list[int], channels: int) -> list[float]:
    if channels <= 1:
        return [float(item) for item in samples]
    mono: list[float] = []
    for index in range(0, len(samples), channels):
        frame = samples[index : index + channels]
        if frame:
            mono.append(sum(float(item) for item in frame) / len(frame))
    return mono


def analyze_wav(path: Path, thresholds: dict[str, Any]) -> dict[str, Any]:
    samples, meta = samples_from_wav(path)
    mono = downmix_abs_samples(samples, int(meta["channels"]))
    if not mono:
        return {
            "schema": "polaris.acoustic_oracle.v1",
            "generated_at": now_iso(),
            "audio_file": str(path),
            "result": "BLOCKED",
            "attribution": "audio_capture_empty",
            "reason": "音频文件没有可分析采样点。",
            "metrics": {},
            "thresholds": thresholds,
            "metadata": meta,
        }
    full_scale = float(meta["full_scale"])
    squares = [sample * sample for sample in mono]
    rms = math.sqrt(sum(squares) / len(squares))
    peak = max(abs(sample) for sample in mono)
    active_threshold = full_scale * (10.0 ** (float(thresholds["active_threshold_dbfs"]) / 20.0))
    sample_rate = int(meta["sample_rate"])
    frame_size = max(1, int(sample_rate * 0.02))
    active_frames = 0
    total_frames = 0
    for index in range(0, len(mono), frame_size):
        frame = mono[index : index + frame_size]
        if not frame:
            continue
        total_frames += 1
        frame_rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame))
        if frame_rms >= active_threshold:
            active_frames += 1
    active_duration_ms = active_frames * 20.0
    clip_count = sum(1 for sample in mono if abs(sample) >= full_scale * 0.999)
    clip_ratio = clip_count / float(len(mono))
    metrics = {
        "rms_dbfs": round(dbfs(rms, full_scale), 3),
        "peak_dbfs": round(dbfs(peak, full_scale), 3),
        "active_duration_ms": round(active_duration_ms, 3),
        "active_ratio": round(active_frames / float(total_frames or 1), 4),
        "clip_ratio": round(clip_ratio, 6),
        "sample_count": len(mono),
    }
    failures: list[str] = []
    if metrics["rms_dbfs"] < float(thresholds["min_rms_dbfs"]):
        failures.append(f"rms_dbfs {metrics['rms_dbfs']} < {thresholds['min_rms_dbfs']}")
    if metrics["peak_dbfs"] < float(thresholds["min_peak_dbfs"]):
        failures.append(f"peak_dbfs {metrics['peak_dbfs']} < {thresholds['min_peak_dbfs']}")
    if metrics["active_duration_ms"] < float(thresholds["min_active_duration_ms"]):
        failures.append(f"active_duration_ms {metrics['active_duration_ms']} < {thresholds['min_active_duration_ms']}")
    if metrics["clip_ratio"] > float(thresholds["max_clip_ratio"]):
        failures.append(f"clip_ratio {metrics['clip_ratio']} > {thresholds['max_clip_ratio']}")
    result = "FAIL" if failures else "PASS"
    return {
        "schema": "polaris.acoustic_oracle.v1",
        "generated_at": now_iso(),
        "audio_file": str(path),
        "result": result,
        "attribution": "acoustic_evidence" if result == "PASS" else "acoustic_signal_quality",
        "reason": "声学回采满足阈值。" if result == "PASS" else "声学回采未满足阈值：" + "; ".join(failures),
        "metrics": metrics,
        "thresholds": thresholds,
        "metadata": meta,
        "oracle_scope": "captured_audio_signal",
    }


def record_with_sounddevice(output_wav: Path, device: str | int | None, sample_rate: int, channels: int, duration_s: float) -> dict[str, Any]:
    try:
        import numpy as np  # type: ignore
        import sounddevice as sd  # type: ignore
    except Exception as exc:
        return {
            "result": "BLOCKED",
            "reason": f"sounddevice/numpy 不可用，无法直接录音：{exc}",
            "dependency": "pip install sounddevice numpy",
        }
    try:
        recording = sd.rec(int(duration_s * sample_rate), samplerate=sample_rate, channels=channels, dtype="int16", device=device)
        sd.wait()
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        data = np.asarray(recording, dtype=np.int16)
        with wave.open(str(output_wav), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(data.tobytes())
        return {
            "result": "PASS",
            "output_wav": str(output_wav),
            "device": device,
            "sample_rate": sample_rate,
            "channels": channels,
            "duration_s": duration_s,
        }
    except Exception as exc:  # pragma: no cover - depends on audio hardware
        return {
            "result": "BLOCKED",
            "reason": f"录音失败：{exc}",
            "device": device,
            "sample_rate": sample_rate,
            "channels": channels,
            "duration_s": duration_s,
        }


def record_with_ffmpeg(output_wav: Path, device_name: str, sample_rate: int, channels: int, duration_s: float) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"result": "BLOCKED", "reason": "ffmpeg 不可用，无法使用 DirectShow/ALSA fallback 录音。"}
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    if platform.system().lower().startswith("win"):
        if not device_name:
            return {"result": "BLOCKED", "reason": "Windows ffmpeg dshow 录音需要 capture_device_name 或可由 laid key 映射到 FriendlyName。"}
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "dshow",
            "-i",
            f"audio={device_name}",
            "-t",
            str(duration_s),
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            str(output_wav),
        ]
    else:
        device = device_name or "default"
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "alsa",
            "-i",
            device,
            "-t",
            str(duration_s),
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            str(output_wav),
        ]
    completed = run_cmd(cmd, timeout_s=int(duration_s + 20))
    if completed.returncode != 0 or not output_wav.exists():
        return {
            "result": "BLOCKED",
            "reason": "ffmpeg 录音失败。",
            "cmd": cmd,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-1000:],
            "stderr": completed.stderr[-2000:],
        }
    return {
        "result": "PASS",
        "output_wav": str(output_wav),
        "device": device_name,
        "sample_rate": sample_rate,
        "channels": channels,
        "duration_s": duration_s,
        "backend": "ffmpeg",
        "cmd": cmd,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Polaris 声学回采 Oracle",
        "",
        f"- result：`{payload.get('result', '')}`",
        f"- attribution：`{payload.get('attribution', '')}`",
        f"- reason：{payload.get('reason', '')}",
        "",
    ]
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    if metrics:
        lines.extend(["## Metrics", ""])
        for key, value in metrics.items():
            lines.append(f"- `{key}`：`{value}`")
        lines.append("")
    lines.extend(
        [
            "## 使用口径",
            "",
            "- `PASS` 只能说明回采文件中有足够能量、持续时间和未削波证据。",
            "- 该 oracle 不做语义识别；语义仍由串口/ASR/媒体日志 oracle 判断。",
            "- 未配置或无法打开回采设备时必须输出 `BLOCKED`，不能声称真实出声通过。",
            "",
        ]
    )
    return "\n".join(lines)


def generate_self_test_wav(path: Path, sample_rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    duration_s = 1.2
    amplitude = 8000
    samples: list[int] = []
    for index in range(int(sample_rate * duration_s)):
        value = int(amplitude * math.sin(2.0 * math.pi * 880.0 * index / sample_rate))
        samples.append(value)
    data = array.array("h", samples)
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(data.tobytes())
    return path


def cmd_probe(args: argparse.Namespace) -> int:
    payload = {
        "schema": "polaris.acoustic_probe.v1",
        "generated_at": now_iso(),
        "laid_capture": probe_laid_capture(install_if_missing=args.install_laid_if_missing),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["laid_capture"]["returncode"] == 0 else 2


def cmd_analyze(args: argparse.Namespace) -> int:
    env_payload = load_env_payload(args.env_file)
    config = acoustic_config(env_payload)
    thresholds = deep_merge(config["thresholds"], read_json(resolve_path(args.thresholds)) if args.thresholds else {})
    payload = analyze_wav(resolve_path(args.audio_file), thresholds)
    out_dir = resolve_path(args.out_dir) if args.out_dir else resolve_path(args.audio_file).parent / "acoustic_oracle"
    write_json(out_dir / "acoustic_oracle.json", payload)
    (out_dir / "acoustic_oracle.md").write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({"result": payload["result"], "out_dir": str(out_dir), "metrics": payload.get("metrics", {})}, ensure_ascii=False, indent=2))
    return 0 if payload["result"] == "PASS" else 1


def cmd_record(args: argparse.Namespace) -> int:
    env_payload = load_env_payload(args.env_file)
    config = acoustic_config(env_payload)
    laid_payload = probe_laid_capture(install_if_missing=False)
    device: str | int | None = None
    if args.device_index is not None:
        device = int(args.device_index)
    elif args.device_name:
        device = args.device_name
    elif config["capture_device_name"]:
        device = config["capture_device_name"]
    else:
        name = friendly_name_for_key(config["capture_device_key"], laid_payload)
        if name:
            device = name
    out_dir = resolve_path(args.out_dir) if args.out_dir else ROOT / "satellite" / "cucumber-agent-testing" / "debug" / "acoustic_oracle" / stamp()
    output_wav = resolve_path(args.output_wav) if args.output_wav else out_dir / "capture.wav"
    record_result = record_with_sounddevice(
        output_wav,
        device,
        int(args.sample_rate or config["capture_sample_rate"]),
        int(args.channels or config["capture_channels"]),
        float(args.duration or config["capture_duration_s"]),
    )
    if record_result.get("result") != "PASS":
        fallback = record_with_ffmpeg(
            output_wav,
            str(device or ""),
            int(args.sample_rate or config["capture_sample_rate"]),
            int(args.channels or config["capture_channels"]),
            float(args.duration or config["capture_duration_s"]),
        )
        if fallback.get("result") == "PASS":
            fallback["primary_backend_blocked"] = record_result
            record_result = fallback
    if record_result.get("result") != "PASS":
        write_json(out_dir / "record_result.json", record_result)
        print(json.dumps(record_result, ensure_ascii=False, indent=2))
        return 2
    payload = analyze_wav(output_wav, config["thresholds"])
    payload["record"] = record_result
    payload["laid_capture"] = laid_payload
    write_json(out_dir / "acoustic_oracle.json", payload)
    (out_dir / "acoustic_oracle.md").write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({"result": payload["result"], "out_dir": str(out_dir), "metrics": payload.get("metrics", {})}, ensure_ascii=False, indent=2))
    return 0 if payload["result"] == "PASS" else 1


def cmd_self_test(args: argparse.Namespace) -> int:
    out_dir = resolve_path(args.out_dir) if args.out_dir else ROOT / "satellite" / "cucumber-agent-testing" / "debug" / "acoustic_oracle_selftest" / stamp()
    wav_path = generate_self_test_wav(out_dir / "synthetic_pass.wav")
    payload = analyze_wav(wav_path, DEFAULT_THRESHOLDS)
    write_json(out_dir / "acoustic_oracle.json", payload)
    (out_dir / "acoustic_oracle.md").write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({"result": payload["result"], "out_dir": str(out_dir), "metrics": payload.get("metrics", {})}, ensure_ascii=False, indent=2))
    return 0 if payload["result"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polaris acoustic loopback/capture oracle")
    sub = parser.add_subparsers(dest="action", required=True)

    probe = sub.add_parser("probe", help="List available capture endpoints through laid")
    probe.add_argument("--install-laid-if-missing", action="store_true")
    probe.set_defaults(func=cmd_probe)

    analyze = sub.add_parser("analyze", help="Analyze an existing WAV capture")
    analyze.add_argument("--audio-file", required=True)
    analyze.add_argument("--env-file", default="")
    analyze.add_argument("--thresholds", default="", help="Optional JSON threshold override")
    analyze.add_argument("--out-dir", default="")
    analyze.set_defaults(func=cmd_analyze)

    record = sub.add_parser("record", help="Record from a capture device with sounddevice, then analyze")
    record.add_argument("--env-file", default="")
    record.add_argument("--device-index", type=int, default=None)
    record.add_argument("--device-name", default="")
    record.add_argument("--sample-rate", type=int, default=0)
    record.add_argument("--channels", type=int, default=0)
    record.add_argument("--duration", type=float, default=0)
    record.add_argument("--output-wav", default="")
    record.add_argument("--out-dir", default="")
    record.set_defaults(func=cmd_record)

    self_test = sub.add_parser("self-test", help="Generate a synthetic WAV and verify the analyzer")
    self_test.add_argument("--out-dir", default="")
    self_test.set_defaults(func=cmd_self_test)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    start = time.time()
    code = args.func(args)
    _ = time.time() - start
    return code


if __name__ == "__main__":
    raise SystemExit(main())
