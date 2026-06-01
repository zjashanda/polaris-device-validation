#!/usr/bin/env python3
from pathlib import Path
import sys

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import serial

from tools.core.polaris_runtime import current_session_dir, ensure_dir, new_artifact_dir, queue_command, read_lines_between


ROOT = Path(__file__).resolve().parents[2]

from docs.api.common_request import MideaCloudRequest  # noqa: E402


FIELD_MAP = {
    "SN": "sn",
    "IoT ID": "iot_id",
    "Mac": "mac",
    "IP": "ip",
    "WakeupID": "wakeup_id",
    "ClientID": "client_id",
    "ClientSec": "client_sec",
}
ENV_CODE_TO_LABEL = {"0": "pro", "1": "uat", "2": "sit"}
ENV_LABEL_TO_CODE = {value: key for key, value in ENV_CODE_TO_LABEL.items()}
FLASH_ENV_RE = re.compile(r"(?:^|\b)env=(?P<env>[0-2])\b", re.I)
FLASH_GET_ENV_RE = re.compile(r"flash\s+get\s+env:(?P<env>[0-2])\s+success", re.I)
FLASH_SET_ENV_RE = re.compile(r"flash\s+set\s+env:(?P<env>[0-2])\s+success", re.I)


def now_iso_ms() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]


def smart_decode(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def normalize_env_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    projects = payload.get("projects")
    if not isinstance(projects, dict):
        return payload
    active = str(payload.get("active_project") or payload.get("project_id") or "").strip()
    if not active and len(projects) == 1:
        active = next(iter(projects))
    profile = projects.get(active)
    if not isinstance(profile, dict):
        return {}
    common = payload.get("common") if isinstance(payload.get("common"), dict) else {}
    merged = deep_merge(common, profile)
    merged.setdefault("project_id", active)
    return merged


def resolve_env_file(value: str = "") -> Path:
    if value:
        path = Path(value)
        return path if path.is_absolute() else (ROOT / path).resolve()
    local = ROOT / "polaris.local.json"
    if local.exists():
        return local
    return ROOT / "config" / "polaris_env.json"


def load_env_payload(env_file: str = "") -> Dict[str, Any]:
    return normalize_env_payload(read_json(resolve_env_file(env_file)))


def load_env_label(env_payload: Dict[str, Any]) -> str:
    cloud = env_payload.get("cloud", {}) if isinstance(env_payload.get("cloud"), dict) else {}
    for value in (cloud.get("api_environment"), cloud.get("device_env"), env_payload.get("current_env_label")):
        label = str(value or "").strip().lower()
        if label in {"sit", "uat", "pro"}:
            return label
    return "sit"


def env_ports(env_payload: Dict[str, Any]) -> Dict[str, str]:
    serial = env_payload.get("serial", {}) if isinstance(env_payload.get("serial"), dict) else {}
    ports = serial.get("ports", {}) if isinstance(serial.get("ports"), dict) else {}
    return {str(key): str(value or "").strip() for key, value in ports.items()}


def env_baudrate(env_payload: Dict[str, Any]) -> int:
    serial_payload = env_payload.get("serial", {}) if isinstance(env_payload.get("serial"), dict) else {}
    try:
        return int(serial_payload.get("baudrate") or env_payload.get("baudrate") or 115200)
    except Exception:
        return 115200


def expected_cloud_env(env_payload: Dict[str, Any]) -> str:
    cloud = env_payload.get("cloud", {}) if isinstance(env_payload.get("cloud"), dict) else {}
    for value in (cloud.get("device_env"), cloud.get("api_environment"), env_payload.get("current_env_label")):
        label = str(value or "").strip().lower()
        if label in ENV_LABEL_TO_CODE:
            return label
    return ""


def normalize_log_line(line: str) -> str:
    if len(line) > 24 and "] " in line:
        parts = line.split("] ", 1)
        if len(parts) == 2:
            return parts[1]
    return line


def parse_device_env(lines: List[str]) -> Dict[str, str]:
    for raw in lines:
        text = normalize_log_line(str(raw)).strip()
        match = FLASH_GET_ENV_RE.search(text) or FLASH_SET_ENV_RE.search(text) or FLASH_ENV_RE.search(text)
        if not match:
            continue
        code = match.group("env").strip()
        return {"code": code, "label": ENV_CODE_TO_LABEL.get(code, "")}
    return {"code": "", "label": ""}


def parse_deviceinfo(lines: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw in lines:
        clean = normalize_log_line(raw).strip()
        for prefix, key in FIELD_MAP.items():
            token = f"{prefix}:"
            if token in clean:
                result[key] = clean.split(token, 1)[1].strip()
    return result


def merge_deviceinfo_with_env(deviceinfo: Dict[str, str], env_payload: Dict[str, Any]) -> Dict[str, str]:
    merged = dict(deviceinfo)
    current_deviceinfo = env_payload.get("current_deviceinfo", {}) or {}
    fallback_keys = ("sn", "iot_id", "mac", "wakeup_id")
    for key in fallback_keys:
        if merged.get(key):
            continue
        value = str(current_deviceinfo.get(key, "")).strip()
        if value:
            merged[key] = value
    device = env_payload.get("device", {}) if isinstance(env_payload.get("device"), dict) else {}
    for key in fallback_keys:
        if merged.get(key):
            continue
        value = str(device.get(key, "")).strip()
        if value:
            merged[key] = value
    if not merged.get("wakeup_id"):
        for key in ("wakeupid_from_deviceinfo", "current_wakeup_word"):
            value = str(env_payload.get(key, "")).strip()
            if value:
                merged["wakeup_id"] = value
                break
    return merged


def direct_query_serial(env_payload: Dict[str, Any], command: str, timeout_s: float = 3.5) -> Dict[str, Any]:
    ports = env_ports(env_payload)
    ap_port = ports.get("ap") or "COM14"
    baudrate = env_baudrate(env_payload)
    started_at = datetime.now().isoformat(timespec="milliseconds")
    partial = ""
    lines: List[str] = []
    try:
        with serial.Serial(ap_port, baudrate, timeout=0.2, write_timeout=1.0) as ser:
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            ser.write((command.rstrip("\r\n") + "\r\n").encode("utf-8", errors="ignore"))
            try:
                ser.flush()
            except Exception:
                pass
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                waiting = ser.in_waiting or 0
                if not waiting:
                    time.sleep(0.05)
                    continue
                partial += smart_decode(ser.read(waiting)).replace("\r", "")
                while "\n" in partial:
                    line, partial = partial.split("\n", 1)
                    if line.strip():
                        lines.append(line.strip())
    except Exception as exc:
        return {
            "port": ap_port,
            "baudrate": baudrate,
            "command": command,
            "started_at": started_at,
            "ended_at": datetime.now().isoformat(timespec="milliseconds"),
            "ok": False,
            "error": repr(exc),
            "lines": [],
        }
    if partial.strip():
        lines.append(partial.strip())
    return {
        "port": ap_port,
        "baudrate": baudrate,
        "command": command,
        "started_at": started_at,
        "ended_at": datetime.now().isoformat(timespec="milliseconds"),
        "ok": True,
        "lines": lines,
    }


def session_query_serial(session_dir: Path, env_payload: Dict[str, Any], command: str, timeout_s: float = 3.5) -> Dict[str, Any]:
    ports = env_ports(env_payload)
    ap_port = ports.get("ap") or "COM14"
    started = datetime.now()
    try:
        queue_command(ap_port, command, session_dir=session_dir)
        time.sleep(float(timeout_s))
        ended = datetime.now()
        lines = read_lines_between(ap_port, started, ended, session_dir=session_dir)
        return {
            "port": ap_port,
            "baudrate": env_baudrate(env_payload),
            "command": command,
            "started_at": started.isoformat(timespec="milliseconds"),
            "ended_at": ended.isoformat(timespec="milliseconds"),
            "ok": True,
            "lines": lines,
        }
    except Exception as exc:
        return {
            "port": ap_port,
            "baudrate": env_baudrate(env_payload),
            "command": command,
            "started_at": started.isoformat(timespec="milliseconds"),
            "ended_at": datetime.now().isoformat(timespec="milliseconds"),
            "ok": False,
            "error": repr(exc),
            "lines": [],
        }


def verify_device_env_gate(session_dir: Optional[Path], env_payload: Dict[str, Any], timeout_s: float = 3.5) -> Dict[str, Any]:
    """Block cloud API calls unless AP-side env is proven to match config."""
    expected = expected_cloud_env(env_payload)
    expected_code = ENV_LABEL_TO_CODE.get(expected, "")
    results: List[Dict[str, Any]] = []
    observed = {"code": "", "label": ""}
    for command in ("flash.get.int env", "flash.show"):
        result = session_query_serial(session_dir, env_payload, command, timeout_s) if session_dir is not None else direct_query_serial(env_payload, command, timeout_s)
        results.append(result)
        observed = parse_device_env([str(line) for line in result.get("lines") or []])
        if observed.get("label"):
            break
    if not expected:
        status = "BLOCKED"
        reason = "cloud.api_environment/cloud.device_env 未配置，禁止调用云端配置 API。"
    elif not observed.get("label"):
        status = "BLOCKED"
        reason = f"未能从 AP 串口确认设备端 env，不能证明设备处于 {expected.upper()}，禁止调用云端配置 API。"
    elif observed.get("label") != expected:
        status = "BLOCKED"
        reason = (
            f"设备端 env={observed.get('code')}/{observed.get('label')} 与 "
            f"cloud.api_environment={expected} 不一致，禁止调用云端配置 API。"
        )
    else:
        status = "PASS"
        reason = f"设备端 env={observed.get('code')}/{observed.get('label')} 与云端 API 环境一致。"
    return {
        "status": status,
        "reason": reason,
        "expected_env": expected,
        "expected_code": expected_code,
        "observed_env": observed.get("label", ""),
        "observed_code": observed.get("code", ""),
        "queries": results,
    }


def capture_deviceinfo(session_dir: Path, env_payload: Dict[str, Any], timeout_s: float = 4.0) -> Dict[str, Any]:
    ports = env_ports(env_payload)
    ap_port = ports.get("ap") or "COM14"
    start_dt = datetime.now()
    queue_command(ap_port, "deviceinfo", session_dir=session_dir)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        lines = read_lines_between(ap_port, start_dt, session_dir=session_dir)
        merged = "\n".join(lines)
        if "Device Info:" in merged and "WakeupID:" in merged:
            time.sleep(0.3)
            break
        time.sleep(0.2)
    end_dt = datetime.now()
    lines = read_lines_between(ap_port, start_dt, end_dt, session_dir=session_dir)
    info = merge_deviceinfo_with_env(parse_deviceinfo(lines), env_payload)
    return {
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
        "ap_port": ap_port,
        "lines": lines,
        "parsed": info,
    }


def direct_capture_deviceinfo(env_payload: Dict[str, Any], timeout_s: float = 4.0) -> Dict[str, Any]:
    ports = env_ports(env_payload)
    ap_port = ports.get("ap") or "COM14"
    baudrate = env_baudrate(env_payload)
    start_dt = datetime.now()
    partial = ""
    lines: List[str] = []
    with serial.Serial(ap_port, baudrate, timeout=0.2, write_timeout=1.0) as ser:
        ser.write(b"deviceinfo\r\n")
        try:
            ser.flush()
        except Exception:
            pass
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            waiting = ser.in_waiting or 0
            if not waiting:
                time.sleep(0.05)
                continue
            partial += smart_decode(ser.read(waiting)).replace("\r", "")
            while "\n" in partial:
                line, partial = partial.split("\n", 1)
                if line.strip():
                    lines.append(line.strip())
            merged = "\n".join(lines)
            if "Device Info:" in merged and "WakeupID:" in merged:
                time.sleep(0.2)
                break
    if partial.strip():
        lines.append(partial.strip())
    end_dt = datetime.now()
    info = merge_deviceinfo_with_env(parse_deviceinfo(lines), env_payload)
    return {
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
        "ap_port": ap_port,
        "baudrate": baudrate,
        "lines": lines,
        "parsed": info,
    }


def response_to_dict(response: Any) -> Dict[str, Any]:
    if response is None:
        return {
            "ok": False,
            "status_code": None,
            "elapsed_s": None,
            "text": "",
            "url": None,
        }
    elapsed_s = None
    try:
        elapsed_s = response.elapsed.total_seconds()
    except Exception:
        elapsed_s = None
    payload = {
        "ok": bool(getattr(response, "ok", False)),
        "status_code": getattr(response, "status_code", None),
        "elapsed_s": elapsed_s,
        "text": getattr(response, "text", ""),
        "url": getattr(response, "url", None),
    }
    payload["business_ok"] = cloud_response_ok(payload)
    return payload


def cloud_response_ok(response_dict: Dict[str, Any]) -> bool:
    if int(response_dict.get("status_code") or 0) != 200:
        return False
    text = str(response_dict.get("text") or "").strip()
    if not text:
        return bool(response_dict.get("ok"))
    try:
        payload = json.loads(text)
    except Exception:
        return bool(response_dict.get("ok"))
    error_code = payload.get("errorCode")
    if error_code not in {None, 0, "0"}:
        return False
    nested = payload.get("result", {}).get("returnData", {}) if isinstance(payload.get("result"), dict) else {}
    business_code = nested.get("code", payload.get("code")) if isinstance(nested, dict) else payload.get("code")
    return business_code in {None, 0, "0", 200, "200"}


def collect_log_excerpt(
    session_dir: Path,
    start_dt: datetime,
    end_dt: datetime,
    port: str,
    keywords: List[str],
) -> List[str]:
    lines = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
    if not keywords:
        return lines
    lowered = [item.lower() for item in keywords]
    result: List[str] = []
    for line in lines:
        text = line.lower()
        if any(keyword in text for keyword in lowered):
            result.append(line)
    return result


def build_request(deviceinfo: Dict[str, str], env_payload: Dict[str, Any]) -> MideaCloudRequest:
    device_id = deviceinfo.get("iot_id")
    if not device_id:
        raise RuntimeError("deviceinfo did not return IoT ID")
    return MideaCloudRequest(int(device_id), environment=load_env_label(env_payload))


def action_probe(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return None


def action_set_full_duplex(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.fullDuplex_switch_new(onoroff=int(args.enable), timeOut=int(args.timeout))


def action_set_volume(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.set_volume(value=int(args.value))


def action_set_multi_wakeup(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.multi_wakeup_switch(enable=int(args.enable))


def action_set_accent(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.accent_switch(
        accentId=str(args.accent_id),
        enableAccent=int(args.enable_accent),
        mixedResEnable=int(args.mixed_res_enable),
    )


def action_set_wakeup_word(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.wakeup_switch(str(args.word))


def action_set_wakeup_threshold(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.wakeup_Threshold_switch(int(args.threshold))


def action_set_log(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.log_set(status=int(args.status), logLevel=int(args.level))


def action_set_wakeup_audio_upload(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.wakeupAudio_upload_new(onoroff=int(args.enable))


def action_set_mic(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.mic_switch(enable=int(args.enable))


def action_set_night_mode(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.night_mode(
        enable=int(args.enable),
        timeFrom=str(args.time_from),
        timeTo=str(args.time_to),
        volume=int(args.volume),
        awakeThreshold=int(args.awake_threshold),
    )


def action_set_character_value(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.characterValue_switch(voice_type=str(args.voice_type))


def action_proactive_interaction(request: MideaCloudRequest, args: argparse.Namespace) -> Any:
    return request.Proactive_interaction(
        interrupt="True" if args.interrupt else "False",
        # docs/common_request.py expects the legacy typo "Ture" for truthy flags.
        endssion="Ture" if args.end_session else "False",
        tts_long="Ture" if args.tts_long else "False",
    )


ACTION_TABLE: Dict[str, Dict[str, Any]] = {
    "probe-device": {
        "handler": action_probe,
        "keywords": ["deviceinfo", "iot id", "clientid", "clientsec"],
    },
    "check-env": {
        "handler": action_probe,
        "keywords": ["env", "flash", "deviceinfo"],
    },
    "set-full-duplex": {
        "handler": action_set_full_duplex,
        "keywords": ["fullduplex", "voiceconfig", "fullduplex", "cloud.instructions", "recv ai"],
    },
    "set-volume": {
        "handler": action_set_volume,
        "keywords": ["volume", "cloud.instructions", "recv ai"],
    },
    "set-multi-wakeup": {
        "handler": action_set_multi_wakeup,
        "keywords": ["multi", "wakeup", "cloud.instructions", "recv ai"],
    },
    "set-accent": {
        "handler": action_set_accent,
        "keywords": ["accent", "cloud.instructions", "recv ai"],
    },
    "set-wakeup-word": {
        "handler": action_set_wakeup_word,
        "keywords": ["wakeup", "wake", "cloud.instructions", "recv ai"],
    },
    "set-wakeup-threshold": {
        "handler": action_set_wakeup_threshold,
        "keywords": ["threshold", "awake", "cloud.instructions", "recv ai"],
    },
    "set-log": {
        "handler": action_set_log,
        "keywords": ["log", "cloud.instructions", "recv ai"],
    },
    "set-wakeup-audio-upload": {
        "handler": action_set_wakeup_audio_upload,
        "keywords": ["wakeup", "audio", "upload", "cloud.instructions", "recv ai"],
    },
    "set-mic": {
        "handler": action_set_mic,
        "keywords": ["mic", "cloud.instructions", "recv ai"],
    },
    "set-night-mode": {
        "handler": action_set_night_mode,
        "keywords": ["night", "mode", "cloud.instructions", "recv ai"],
    },
    "set-character-value": {
        "handler": action_set_character_value,
        "keywords": ["voice", "character", "cloud.instructions", "recv ai"],
    },
    "proactive-interaction": {
        "handler": action_proactive_interaction,
        "keywords": ["proactive", "cloud.instructions", "recv ai"],
    },
}


def write_text(path: Path, lines: List[str]) -> None:
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def save_summary(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def app_control_artifact_dir(action: str, session_dir: Optional[Path]) -> Path:
    suffix = action.replace("-", "_")
    if session_dir is not None:
        try:
            return new_artifact_dir(f"app_control_{suffix}", session_dir=session_dir)
        except OSError:
            # Deep debug run paths can exceed legacy Windows path limits. Keep
            # cloud-control evidence in a shorter fallback instead of crashing.
            pass
    return ensure_dir(ROOT / "satellite" / "cucumber-agent-testing" / "debug" / "app_control_direct" / f"{stamp()}_app_control_{suffix}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polaris app-side cloud control helper")
    parser.add_argument("--env-file", default="", help="Polaris local config; defaults to root polaris.local.json")
    parser.add_argument("--env-check-timeout", type=float, default=3.5, help="Seconds per AP env query before cloud actions")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("probe-device", help="Read deviceinfo and print the latest device identity")
    sub.add_parser("check-env", help="Verify AP-side device env matches cloud.api_environment; does not call cloud API")

    full = sub.add_parser("set-full-duplex", help="Set full-duplex switch and timeout via cloud")
    full.add_argument("--enable", type=int, choices=[0, 1], required=True)
    full.add_argument("--timeout", type=int, required=True)

    volume = sub.add_parser("set-volume", help="Set volume via cloud")
    volume.add_argument("--value", type=int, required=True)

    multi = sub.add_parser("set-multi-wakeup", help="Set multi-wakeup via cloud")
    multi.add_argument("--enable", type=int, choices=[0, 1], required=True)

    accent = sub.add_parser("set-accent", help="Set accent via cloud")
    accent.add_argument("--accent-id", required=True)
    accent.add_argument("--enable-accent", type=int, choices=[0, 1], required=True)
    accent.add_argument("--mixed-res-enable", type=int, choices=[0, 1], required=True)

    wake_word = sub.add_parser("set-wakeup-word", help="Set wakeup word via cloud")
    wake_word.add_argument("--word", required=True)

    wake_th = sub.add_parser("set-wakeup-threshold", help="Set wakeup threshold via cloud")
    wake_th.add_argument("--threshold", type=int, required=True)

    log_set = sub.add_parser("set-log", help="Set cloud log upload status and level")
    log_set.add_argument("--status", type=int, choices=[0, 1], required=True)
    log_set.add_argument("--level", type=int, required=True)

    wake_audio = sub.add_parser("set-wakeup-audio-upload", help="Set wakeup audio upload via cloud")
    wake_audio.add_argument("--enable", type=int, choices=[0, 1], required=True)

    mic = sub.add_parser("set-mic", help="Set mic on/off via cloud")
    mic.add_argument("--enable", type=int, choices=[0, 1], required=True)

    night = sub.add_parser("set-night-mode", help="Set night mode via cloud")
    night.add_argument("--enable", type=int, choices=[0, 1], required=True)
    night.add_argument("--time-from", default="09:00")
    night.add_argument("--time-to", default="18:00")
    night.add_argument("--volume", type=int, default=0)
    night.add_argument("--awake-threshold", type=int, default=0)

    voice = sub.add_parser("set-character-value", help="Set TTS/character voice type via cloud")
    voice.add_argument("--voice-type", required=True)

    proactive = sub.add_parser("proactive-interaction", help="Trigger proactive interaction via cloud")
    proactive.add_argument("--interrupt", action="store_true")
    proactive.add_argument("--end-session", action="store_true")
    proactive.add_argument("--tts-long", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    env_payload = load_env_payload(args.env_file)
    try:
        session_dir: Optional[Path] = current_session_dir(ROOT)
    except FileNotFoundError:
        session_dir = None
    artifact_dir = app_control_artifact_dir(args.action, session_dir)
    ports = env_ports(env_payload)
    ap_port = ports.get("ap") or "COM14"
    asr_port = ports.get("asr") or ports.get("upper") or "COM13"

    try:
        deviceinfo_capture = capture_deviceinfo(session_dir, env_payload) if session_dir is not None else direct_capture_deviceinfo(env_payload)
    except Exception as exc:
        payload = {
            "artifact_dir": str(artifact_dir),
            "action": args.action,
            "args": vars(args),
            "env": load_env_label(env_payload),
            "env_file": str(resolve_env_file(args.env_file)),
            "ports": {"ap": ap_port, "asr": asr_port},
            "result": "BLOCKED",
            "reason": f"deviceinfo capture failed: {exc}",
            "evidence_mode": "session" if session_dir is not None else "direct_serial",
        }
        save_summary(artifact_dir / "summary.json", payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 3
    deviceinfo = deviceinfo_capture["parsed"]
    write_text(artifact_dir / "deviceinfo.log", deviceinfo_capture["lines"])
    save_summary(artifact_dir / "deviceinfo.json", deviceinfo)

    env_gate = None
    if args.action != "probe-device":
        env_gate = verify_device_env_gate(session_dir, env_payload, timeout_s=float(args.env_check_timeout))
        save_summary(artifact_dir / "env_gate.json", env_gate)
        if env_gate.get("status") != "PASS":
            payload = {
                "artifact_dir": str(artifact_dir),
                "action": args.action,
                "args": vars(args),
                "env": load_env_label(env_payload),
                "env_file": str(resolve_env_file(args.env_file)),
                "ports": {"ap": ap_port, "asr": asr_port},
                "deviceinfo": deviceinfo,
                "env_gate": env_gate,
                "result": "BLOCKED",
                "reason": env_gate.get("reason"),
                "evidence_mode": "session" if session_dir is not None else "direct_serial",
            }
            save_summary(artifact_dir / "summary.json", payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 3
        if args.action == "check-env":
            payload = {
                "artifact_dir": str(artifact_dir),
                "action": args.action,
                "args": vars(args),
                "env": load_env_label(env_payload),
                "env_file": str(resolve_env_file(args.env_file)),
                "ports": {"ap": ap_port, "asr": asr_port},
                "deviceinfo": deviceinfo,
                "env_gate": env_gate,
                "result": "PASS",
                "reason": env_gate.get("reason"),
                "evidence_mode": "session" if session_dir is not None else "direct_serial",
            }
            save_summary(artifact_dir / "summary.json", payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

    start_dt = datetime.now()
    response = None
    action_meta = ACTION_TABLE[args.action]
    try:
        if args.action != "probe-device":
            request = build_request(deviceinfo, env_payload)
            response = action_meta["handler"](request, args)
            time.sleep(2.0)
    except Exception as exc:
        payload = {
            "artifact_dir": str(artifact_dir),
            "action": args.action,
            "args": vars(args),
            "env": load_env_label(env_payload),
            "env_file": str(resolve_env_file(args.env_file)),
            "ports": {"ap": ap_port, "asr": asr_port},
            "deviceinfo": deviceinfo,
            "result": "BLOCKED",
            "reason": f"cloud action failed: {exc}",
            "evidence_mode": "session" if session_dir is not None else "direct_serial",
        }
        save_summary(artifact_dir / "summary.json", payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 3
    end_dt = datetime.now()

    keywords = action_meta["keywords"]
    if session_dir is not None:
        ap_window = read_lines_between(ap_port, start_dt, end_dt, session_dir=session_dir)
        wb_window = read_lines_between(asr_port, start_dt, end_dt, session_dir=session_dir)
        ap_excerpt = collect_log_excerpt(session_dir, start_dt, end_dt, ap_port, keywords)
        wb_excerpt = collect_log_excerpt(session_dir, start_dt, end_dt, asr_port, keywords)
    else:
        ap_window = []
        wb_window = []
        ap_excerpt = []
        wb_excerpt = []
    write_text(artifact_dir / f"{ap_port}_window.log", ap_window)
    write_text(artifact_dir / f"{asr_port}_window.log", wb_window)
    write_text(artifact_dir / f"{ap_port}_excerpt.log", ap_excerpt)
    write_text(artifact_dir / f"{asr_port}_excerpt.log", wb_excerpt)

    response_dict = response_to_dict(response)
    response_ok = bool(response_dict.get("business_ok"))
    save_summary(artifact_dir / "response.json", response_dict)

    payload = {
        "artifact_dir": str(artifact_dir),
        "action": args.action,
        "args": vars(args),
        "env": load_env_label(env_payload),
        "env_file": str(resolve_env_file(args.env_file)),
        "ports": {"ap": ap_port, "asr": asr_port},
        "deviceinfo": deviceinfo,
        "env_gate": env_gate,
        "response": response_dict,
        "result": "PASS" if args.action == "probe-device" or response_ok else "BLOCKED",
        "evidence_mode": "session" if session_dir is not None else "direct_serial",
        "ap_window_count": len(ap_window),
        "wb_window_count": len(wb_window),
        "ap_excerpt_count": len(ap_excerpt),
        "wb_excerpt_count": len(wb_excerpt),
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
    }
    save_summary(artifact_dir / "summary.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.action != "probe-device" and not response_ok:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
