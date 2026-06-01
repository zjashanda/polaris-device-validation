#!/usr/bin/env python3
from pathlib import Path
import sys

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

import argparse
import asyncio
import base64
import hashlib
import json
import os
import subprocess
import time
import wave
from pathlib import Path
from typing import Dict, List, Tuple

import websockets
import yaml


BASE_URL = "ws://wsapi.xfyun.cn/v1/aiui"
VCN = "x4_yezi"
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
SAPI_VOICE_NAME = "Huihui"


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def cache_dir() -> Path:
    path = workspace_root() / "cache" / "audio" / "tts_pcm"
    path.mkdir(parents=True, exist_ok=True)
    return path


def windows_long_path(path: Path) -> str:
    text = str(path.resolve())
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return "\\\\?\\" + text
    return text


def xfyun_credentials() -> Dict[str, str]:
    """Return xfyun TTS credentials from environment, or an empty dict."""
    app_id = os.environ.get("POLARIS_XFYUN_APP_ID") or os.environ.get("XFYUN_APP_ID")
    api_key = os.environ.get("POLARIS_XFYUN_API_KEY") or os.environ.get("XFYUN_API_KEY")
    auth_id = os.environ.get("POLARIS_XFYUN_AUTH_ID") or os.environ.get("XFYUN_AUTH_ID")
    if app_id and api_key and auth_id:
        return {"app_id": app_id, "api_key": api_key, "auth_id": auth_id}
    return {}


def build_conn_param(credentials: Dict[str, str]) -> str:
    cur_time = str(int(time.time()))
    param = {
        "auth_id": credentials["auth_id"],
        "data_type": "text",
        "speed": "50",
        "pitch": "50",
        "volume": "100",
        "ent": "xtts",
        "vcn": VCN,
        "aue": "raw",
        "scene": "IFLYTEK.tts",
        "sample_rate": str(SAMPLE_RATE),
        "vad_info": "end",
        "ver_type": "monitor",
        "result_level": "plain",
    }
    param_b64 = base64.b64encode(json.dumps(param, ensure_ascii=False).encode("utf-8")).decode("utf-8")
    checksum = hashlib.md5((credentials["api_key"] + cur_time + param_b64).encode("utf-8")).hexdigest()
    return f"?appid={credentials['app_id']}&checksum={checksum}&param={param_b64}&curtime={cur_time}&signtype=md5"


async def synthesize_pcm_bytes(text: str) -> bytes:
    credentials = xfyun_credentials()
    if not credentials:
        raise RuntimeError("xfyun TTS credentials are not configured")
    chunks: List[bytes] = []
    async with websockets.connect(BASE_URL + build_conn_param(credentials), origin="*", close_timeout=1000) as websocket:
        await websocket.send(text)
        while True:
            response = await websocket.recv()
            payload = json.loads(response)
            data = payload.get("data") or {}
            content = data.get("content")
            if content:
                chunks.append(base64.b64decode(content))
            if data.get("is_finish") is True:
                break
    raw = b"".join(chunks)
    if not raw:
        raise RuntimeError(f"TTS returned empty audio for text: {text}")
    return raw


def tts_cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def cached_pcm_path(text: str) -> Path:
    return cache_dir() / f"{tts_cache_key(text)}.pcm"


def synthesize_pcm_with_sapi(text: str, pcm_path: Path) -> Path:
    pcm_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = pcm_path.with_suffix(".sapi.wav")
    escaped_wav = str(wav_path).replace("'", "''")
    escaped_text = text.replace("'", "''")
    ps1_path = pcm_path.with_suffix(".sapi.ps1")
    synth_script = "\n".join(
        [
            "$ErrorActionPreference='Stop'",
            "Add-Type -AssemblyName System.Speech",
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
            f"$voice = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Name -like '*{SAPI_VOICE_NAME}*' }} | Select-Object -First 1",
            "if ($voice) { $s.SelectVoice($voice.VoiceInfo.Name) }",
            f"$s.SetOutputToWaveFile('{escaped_wav}')",
            f"$s.Speak('{escaped_text}')",
            "$s.Dispose()",
            "",
        ]
    )
    ps1_path.write_text(synth_script, encoding="utf-8-sig")
    synth_run = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if synth_run.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size <= 46:
        raise RuntimeError(
            "local SAPI synthesis failed: "
            f"code={synth_run.returncode}, stderr={synth_run.stderr.strip()}"
        )

    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(wav_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        str(pcm_path),
    ]
    completed = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not pcm_path.exists() or pcm_path.stat().st_size == 0:
        raise RuntimeError(
            "ffmpeg failed to convert SAPI wav to pcm: "
            f"code={completed.returncode}, stderr={completed.stderr.strip()}"
        )
    return pcm_path


def ensure_tts_pcm(text: str) -> Path:
    path = cached_pcm_path(text)
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        raw = asyncio.run(synthesize_pcm_bytes(text))
    except Exception:
        raw = b""
    engine = "xfyun"
    if raw:
        path.write_bytes(raw)
    else:
        synthesize_pcm_with_sapi(text, path)
        engine = "sapi"
    meta = {
        "text": text,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "sample_width": SAMPLE_WIDTH,
        "engine": engine,
    }
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_pcm(path: Path) -> bytes:
    data = path.read_bytes()
    if not data:
        raise RuntimeError(f"PCM file is empty: {path}")
    return data


def silence_pcm(duration_ms: int) -> bytes:
    frames = int(SAMPLE_RATE * duration_ms / 1000)
    return b"\x00" * frames * SAMPLE_WIDTH * CHANNELS


def build_sequence(sequence: List[Dict[str, object]], output_wav: Path) -> Dict[str, object]:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    combined = bytearray()
    step_manifest: List[Dict[str, object]] = []
    for index, step in enumerate(sequence, start=1):
        step_type = step["type"]
        if step_type == "tts":
            text = str(step["text"])
            pcm_path = ensure_tts_pcm(text)
            pcm = read_pcm(pcm_path)
            combined.extend(pcm)
            step_manifest.append(
                {
                    "index": index,
                    "type": "tts",
                    "text": text,
                    "pcm_path": str(pcm_path),
                    "bytes": len(pcm),
                }
            )
        elif step_type == "silence":
            duration_ms = int(step["duration_ms"])
            pcm = silence_pcm(duration_ms)
            combined.extend(pcm)
            step_manifest.append(
                {
                    "index": index,
                    "type": "silence",
                    "duration_ms": duration_ms,
                    "bytes": len(pcm),
                }
            )
        else:
            raise ValueError(f"unsupported step type: {step_type}")

    with wave.open(windows_long_path(output_wav), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(bytes(combined))

    manifest = {
        "output_wav": str(output_wav),
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "sample_width": SAMPLE_WIDTH,
        "total_bytes": len(combined),
        "duration_ms": int(len(combined) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS) * 1000),
        "sequence": step_manifest,
    }
    Path(windows_long_path(output_wav.with_suffix(".json"))).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build_from_case(case_path: Path, output_dir: Path) -> Tuple[Path, Dict[str, object]]:
    spec = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    sequence = spec["playback"]["sequence"]
    case_id = spec["case_id"]
    output_wav = output_dir / f"{case_id}.wav"
    manifest = build_sequence(sequence, output_wav)
    return output_wav, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polaris audio builder")
    parser.add_argument("--case-file", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    wav_path, manifest = build_from_case(Path(args.case_file), Path(args.output_dir))
    print(wav_path)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
