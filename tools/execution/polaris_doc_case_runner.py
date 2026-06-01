# -*- coding: utf-8 -*-
from pathlib import Path
import sys

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from tools.cloud.polaris_app_control import build_request as _build_cloud_request
from tools.cloud.polaris_app_control import capture_deviceinfo as _capture_cloud_deviceinfo
from tools.cloud.polaris_app_control import collect_log_excerpt as collect_cloud_log_excerpt
from tools.cloud.polaris_app_control import load_env_payload as load_cloud_env_payload
from tools.cloud.polaris_app_control import response_to_dict as cloud_response_to_dict
from tools.audio.polaris_audio_builder import build_sequence
from tools.core.polaris_adapter_bridge import run_audio_playback_adapter, run_adapter_action_capture
from tools.execution.polaris_case_runner import (
    default_playback_device_key,
    playback_timeout_seconds,
    playback_device_label,
    run_playback,
    sanitize_logs,
    summarize_window,
)
from tools.library.polaris_doc_case_lib import MODE_OFFLINE, SUPPORTED_DOC_CASES, StepToken, default_doc_xlsx, load_doc_case, parse_tone_catalog
from tools.device.polaris_network_orchestrator import collect_window as collect_network_window
from tools.device.polaris_network_orchestrator import command_window as network_command_window
from tools.core.polaris_config import add_canonical_log_aliases, configured_log_ports, read_env_config
from tools.core.polaris_runtime import current_session_dir, new_artifact_dir, parse_prefixed_timestamp, queue_command, read_lines_between, workspace_root
from tools.probe.polaris_state_probe import diff_states, snapshot


CP_WAKE_RE = re.compile(r"WAKE\(1\)", re.I)
CP_CMD_RE = re.compile(r"WAKE\(0\)", re.I)
AP_WAKE_RE = re.compile(r"wakeup_callback, keyword:", re.I)
WB_WAKE_RE = re.compile(r"(?:offline[_ ]wakeup|line_wakeup)", re.I)
WB_ONLINE_WAKE_RE = re.compile(r"online_wakeup", re.I)
AP_ASR_RE = re.compile(r"offline_asr_callbak", re.I)
WB_ASR_RE = re.compile(r"offline_asr_callbak", re.I)
AP_ONLINE_ASR_RE = re.compile(r"online_asr_callbak,\s*text:\s*(.+)$", re.I)
WB_PLAY_START_RE = re.compile(r"local player status\s+2\s+PLAYING", re.I)
WB_PLAY_END_RE = re.compile(r"local player status\s+6\s+PLAYBACK_COMPLETE", re.I)
AP_CLOUD_TTS_PLAY_RE = re.compile(r"TTS playing with ", re.I)
AP_CLOUD_TTS_RECV_RE = re.compile(r"TTS recv with ", re.I)
AP_CLOUD_TTS_START_RE = re.compile(r'(ttsplayer play:|play audio (?:https?|mem)://|TTS playing with |"status":"play")', re.I)
AP_CLOUD_TTS_STOP_RE = re.compile(r'("status":"stop"|play complete, all len|tone player evt 6)', re.I)
AP_IGNORE_BROADCAST_RE = re.compile(r"mic disabled,\s*ignore broadcast", re.I)
COMMAND_RE = re.compile(r"\[COMMAND\]\s+(.+)$")
CP_CMD_KEYWORD_RE = re.compile(r"WAKE\(0\):.*?\(([^)]+)\)", re.I)
ASR_KEYWORD_RE = re.compile(r"offline_asr_callbak,\s*keyword:\s*([^,]+)", re.I)
ALGO_THRESHOLD_RE = re.compile(r'algo info:\s*.*?"ncmThreshold":(-?\d+),"keyword":"([^"]+)"', re.I)
WB_TTS_CALLBACK_RE = re.compile(r"offline_tts_callbak,\s*tts:\s*(\d+)", re.I)
AP_TTS_FAIL_RE = re.compile(r"\btts\s+(\d+)\s+can't play\b", re.I)
AP_CLOUD_LOGLEV_RE = re.compile(r"set device loglev\s+(?P<level>\d+)\s+by\s+(?P<source>cloud_change|status_0)", re.I)
AP_CLOUD_LOGLEV_NOCHANGE_RE = re.compile(r"cloud loglev\s+(?P<cloud_level>\d+)\s+not change", re.I)
WAKEUP_UPLOAD_SESSION_RE = re.compile(r"wakeup_upload.*session id:\s*(?P<session>[0-9a-f-]+)", re.I)
SPLIT_ASR_HEAD_RE = re.compile(r"offline_asr_", re.I)
SPLIT_ASR_TAIL_RE = re.compile(r"callbak,\s*keyword:\s*([^,]+)", re.I)
SPLIT_TTS_HEAD_RE = re.compile(r"offline_tts_", re.I)
SPLIT_TTS_TAIL_RE = re.compile(r"callbak,\s*tts:\s*(\d+)", re.I)
ALGO_VERSION_LINE_RE = re.compile(r"Algo Version,\s*(.+)$", re.I)
PLAYER_RESET_USER_RE = re.compile(r'player reset by "user"', re.I)


def adapter_hotspot_status() -> Dict[str, Any]:
    result = run_adapter_action_capture(
        adapter_id="network.local",
        action="hotspot_status",
        timeout_s=60,
        execute=True,
        allow_side_effects=True,
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        payload = {}
    payload.setdefault("_adapter_result", result.to_dict())
    return payload


def adapter_hotspot_set(enable: bool) -> Dict[str, Any]:
    result = run_adapter_action_capture(
        adapter_id="network.local",
        action="hotspot_on" if enable else "hotspot_off",
        timeout_s=90,
        execute=True,
        allow_side_effects=True,
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        payload = {}
    payload.setdefault("_adapter_result", result.to_dict())
    return payload
BOOT_MARKER_RE = re.compile(r"\bboot\.action=boot_image\b", re.I)
CRASH_MARKER_RE = re.compile(r"\b(panic|assert|exception|watchdog|hardfault|guru meditation)\b", re.I)
AI_DISCONNECT_RE = re.compile(r"AI disconnected|wifiLink_update:disconnect close", re.I)
WB_AI_STATE4_RE = re.compile(r"class:\s*ai\(2\),\s*state:\s*4\b", re.I)
STREAM_TTS_URL_ID_RE = re.compile(r"/stream_tts/v2/([0-9a-f-]+)", re.I)
SESSION_TIMEOUT_RE = re.compile(r"stop interactive by session timeout", re.I)
TIMEOUT_AUDIO_RE = re.compile(r"play timeout audio\s+(\d+)", re.I)
FULL_TIMEOUT_REFRESH_RE = re.compile(r"fullduplex timeout refresh to (\d+)s", re.I)
HALF_TIMEOUT_REFRESH_RE = re.compile(r"halfduplex timeout refresh to (\d+)s", re.I)
RESTART_SESSION_TIMER_RE = re.compile(r"restart session timer with (\d+)s", re.I)
SET_WAKE_THRESHOLD_LEVEL_RE = re.compile(r"set wake threshold,\s*\[(?P<label>[A-Z]+)\s+(?P<value>-?\d+)\]", re.I)
GET_WAKE_THRESHOLD_RE = re.compile(r"get threshold is\s+(?P<value>-?\d+)\s+tar_source\s+(?P<source>-?\d+)", re.I)

WAKE_WORD_TEXT = "小美小美"
TEXT_DIALOG_OPEN = "小美小美，打开自然对话"
TEXT_DIALOG_CLOSE = "小美小美，关闭自然对话"
TEXT_AC_ON = "小美小美，打开空调"
TEXT_AC_OFF = "小美小美，关闭空调"
TEXT_CMD_AC_ON = "打开空调"
TEXT_CMD_AC_OFF = "关闭空调"
TEXT_TIME_QUERY = "现在几点了"
TEXT_CHITCHAT_1 = "今天天气不错"
TEXT_CHITCHAT_2 = "我们下午去公园吧"
TEXT_CHITCHAT_3 = "晚上吃什么呢"
TEXT_MODE_COOL = "制冷模式"
LOCAL_HOTSPOT_SSID = os.environ.get("POLARIS_LOCAL_HOTSPOT_SSID", "")
LOCAL_HOTSPOT_PASSWORD = os.environ.get("POLARIS_LOCAL_HOTSPOT_PASSWORD", "")
CLOUD_FULL_DUPLEX_KEYWORDS = ["fullduplex", "voiceconfig", "cloud.instructions", "recv ai"]
CLOUD_MIC_KEYWORDS = ["mic", "mute", "cloud.report.status", "device.event.mic.ack"]
CLOUD_ACCENT_KEYWORDS = ["accent", "cloud.order.config", "recv ai", "set accent"]
CLOUD_WAKEUP_WORD_KEYWORDS = ["wake", "wakeup", "cloud.instructions", "recv ai"]
CLOUD_WAKEUP_THRESHOLD_KEYWORDS = ["threshold", "awake", "cloud.instructions", "recv ai"]
CLOUD_LOG_KEYWORDS = ["log", "cloud.instructions", "recv ai", "cloud_change"]
CLOUD_WAKE_AUDIO_KEYWORDS = ["wakeup_upload", "wakeaudio", "wake audio", "cloud.order.config", "recv ai"]
CLOUD_PROACTIVE_KEYWORDS = ["broadcast", "proactive", "cloud.instructions", "recv ai"]
ONLINE_SIGNAL_PATTERNS = (
    re.compile(r"cloud\.online\.reply", re.I),
    re.compile(r"device\.event\.keepAlive\.ack", re.I),
    re.compile(r"login success", re.I),
    re.compile(r"Cur router rssi=", re.I),
    re.compile(r"route info upload ok", re.I),
    re.compile(r"get heartbeat from cloud", re.I),
    re.compile(r"cloud status\s*:0x04", re.I),
)

COMMON_MOJIBAKE_ALIASES = {
    "\u935a\u5823\u5049\u6d60\u008a\u5929\u7684\u5929\u6c14": "\u5408\u80a5\u4eca\u5929\u7684\u5929\u6c14",
    "\u941c\u677f\u009c\u3125\u5691\u9410\u901b\u7c21": "\u5408\u80a5\u4eca\u5929\u7684\u5929\u6c14",
    "鎵撳紑绌º调": "打开空调",
    "鎵撳紑绌º皟": "打开空调",
    "鎵撳紑绌鸿皟": "打开空调",
    "打å紑绌鸿皟": "打开空调",
    "æå¼空调": "打开空调",
    "打开ç©ºè°": "打开空调",
    "鎵开空调": "打开空调",
    "鍏抽棴绌º调": "关闭空调",
    "鍏抽棴绌º皟": "关闭空调",
    "鍏抽棴绌鸿皟": "关闭空调",
    "鍏³闭空调": "关闭空调",
    "å³é­ç©鸿皟": "关闭空调",
    "关é棴绌鸿皟": "关闭空调",
    "关闭ç©ºè°": "关闭空调",
    "鍚堣偉浠婂ぉ鐨勫¤╂皵": "合肥今天的天气",
    "合肥今天的气": "合肥今天的天气",
    "浠婂¤╃殑鑲＄エ鎯呭喌": "今天的股票情况",
    "今的股票情况": "今天的股票情况",
    "å府鎴戝畾涓槑澶╂棭涓婁竷鐐圭殑闂归挓": "帮我定个明天早上七点的闹钟",
    "02我定个明天早上七点的闹钟": "帮我定个明天早上七点的闹钟",
    "我定丘天早上七点的闹钟": "帮我定个明天早上七点的闹钟",
}

ONLINE_ASR_TEXT_HINTS = [
    "小美",
    "打开",
    "关闭",
    "空调",
    "天气",
    "股票",
    "闹钟",
    "今天",
    "明天",
    "早上",
    "几点",
    "自然对话",
    "制冷",
    "模式",
    "合肥",
    "帮我",
    "定个",
]

ONLINE_ASR_MOJIBAKE_HINTS = "åæçé鍚浠婂鐨╂皵鎯呭喌鎴戝畾涓槑澶╂棭闂归挓"
TIME_NUMERAL_CANONICAL = {
    "零点": "0点",
    "一点": "1点",
    "二点": "2点",
    "两点": "2点",
    "三点": "3点",
    "四点": "4点",
    "五点": "5点",
    "六点": "6点",
    "七点": "7点",
    "八点": "8点",
    "九点": "9点",
    "十点": "10点",
}


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def cloud_response_ok(response_dict: Dict[str, Any]) -> bool:
    if int(response_dict.get("status_code") or 0) != 200:
        return False
    text = str(response_dict.get("text") or "").strip()
    if not text:
        return True
    try:
        payload = json.loads(text)
    except Exception:
        return True

    error_code = payload.get("errorCode")
    if error_code not in {None, 0, "0"}:
        return False

    nested = payload.get("result", {}).get("returnData", {})
    business_code = nested.get("code", payload.get("code"))
    if business_code in {None, 0, "0", 200, "200"}:
        return True
    return False


def map_cloud_upload_level_to_device_log_level(cloud_level: int) -> Optional[int]:
    mapping = {
        7: 4,
        6: 3,
        4: 2,
        3: 1,
    }
    return mapping.get(int(cloud_level))


def normalize_mac(raw: str) -> str:
    return raw.strip().lower().replace("-", ":")


def load_env_config() -> dict:
    return json.loads((workspace_root() / "config" / "polaris_env.json").read_text(encoding="utf-8"))


def current_runtime_env_config() -> dict:
    env_file = os.environ.get("POLARIS_ENV_FILE", "").strip()
    if env_file:
        return load_cloud_env_payload(env_file)
    return read_env_config()


def active_project_payload() -> dict:
    env = current_runtime_env_config()
    active = str(env.get("active_project") or env.get("project_id") or "").strip()
    projects = env.get("projects", {}) if isinstance(env.get("projects"), dict) else {}
    project = projects.get(active, {}) if active else {}
    return project if isinstance(project, dict) and project else env


def project_has_cp_log() -> bool:
    project = active_project_payload()
    serial = project.get("serial", {}) if isinstance(project.get("serial"), dict) else {}
    ports = serial.get("ports", {}) if isinstance(serial.get("ports"), dict) else {}
    assertion_profile = str(project.get("assertion_profile") or "").strip().lower()
    project_type = str(project.get("project_type") or "").strip().lower()
    cp_port = str(ports.get("cp") or "").strip()
    return bool(cp_port) and assertion_profile != "ap_upper_no_cp" and project_type not in {"ws63", "ap_wifi_control_no_cp"}


def strip_wake_word_text(text: str) -> str:
    cleaned = str(text or "").replace(WAKE_WORD_TEXT, "")
    return cleaned.strip(" ，,。.!！?？、")


def expected_online_texts_from_phase(phase: dict) -> List[str]:
    texts: List[str] = []
    for item in phase.get("sequence", []):
        if not isinstance(item, dict) or item.get("type") != "tts":
            continue
        text = strip_wake_word_text(str(item.get("text") or ""))
        if text:
            append_unique(texts, normalize_online_asr_text(text))
    return texts


def split_wake_prefixed_sequence(sequence: List[dict], wake_gap_ms: int = 2500) -> List[dict]:
    split: List[dict] = []
    for item in sequence:
        if not isinstance(item, dict) or item.get("type") != "tts":
            split.append(item)
            continue
        text = str(item.get("text") or "")
        command = strip_wake_word_text(text)
        if WAKE_WORD_TEXT in text and command:
            split.extend(
                [
                    {"type": "tts", "text": WAKE_WORD_TEXT},
                    {"type": "silence", "duration_ms": wake_gap_ms},
                    {"type": "tts", "text": command},
                ]
            )
        else:
            split.append(item)
    return split


def adapt_phase_for_current_project(phase: dict) -> dict:
    if project_has_cp_log():
        return phase
    adapted = dict(phase)
    metadata = dict(adapted.get("metadata", {}) if isinstance(adapted.get("metadata"), dict) else {})
    metadata["assertion_profile_adapter"] = "ap_upper_no_cp"
    if isinstance(adapted.get("sequence"), list):
        split_sequence = split_wake_prefixed_sequence(adapted["sequence"])
        if split_sequence != adapted["sequence"]:
            metadata["split_wake_prefixed_tts_for_no_cp"] = True
            adapted["sequence"] = split_sequence
    if "min_cp_wake" in adapted:
        adapted.setdefault("min_wb_online_wake", adapted.pop("min_cp_wake"))
    if "min_ap_wake" in adapted:
        adapted.setdefault("min_wb_online_wake", adapted.pop("min_ap_wake"))
    if "min_cp_command" in adapted:
        value = adapted.pop("min_cp_command")
        adapted.setdefault("min_ap_cloud_tts_recv", value)
    if "required_tones" in adapted:
        adapted.setdefault("min_ap_cloud_tts_start", 1)
        metadata["dropped_required_tones_for_no_cp"] = adapted.pop("required_tones")
    if "forbidden_tones" in adapted:
        metadata["dropped_forbidden_tones_for_no_cp"] = adapted.pop("forbidden_tones")
    if "required_keywords" in adapted:
        metadata["dropped_required_keywords_for_no_cp"] = adapted.pop("required_keywords")
    if "forbidden_keywords" in adapted:
        metadata["dropped_forbidden_keywords_for_no_cp"] = adapted.pop("forbidden_keywords")
    adapted["metadata"] = metadata
    return adapted


def adapt_rules_for_current_project(rules: dict) -> dict:
    if project_has_cp_log():
        return rules
    adapted = dict(rules)
    if "min_cp_wake" in adapted:
        adapted.setdefault("min_wb_online_wake", adapted.pop("min_cp_wake"))
    if "min_ap_wake" in adapted:
        adapted.setdefault("min_wb_online_wake", adapted.pop("min_ap_wake"))
    if "min_cp_command" in adapted:
        adapted.pop("min_cp_command")
    if "min_unique_command_keywords" in adapted:
        adapted.setdefault("min_ap_online_asr", adapted.pop("min_unique_command_keywords"))
    return adapted


def capture_cloud_deviceinfo(session_dir: Path) -> dict:
    return _capture_cloud_deviceinfo(session_dir, current_runtime_env_config())


def build_cloud_request(deviceinfo: Dict[str, str]):
    return _build_cloud_request(deviceinfo, current_runtime_env_config())


def hotspot_has_device(status: Dict[str, Any], device_mac: str) -> bool:
    target = normalize_mac(device_mac)
    if not target:
        return False
    for client in status.get("clients", []):
        if normalize_mac(str(client.get("mac_address", ""))) == target:
            return True
    return False


def summarize_hotspot_state(status: Dict[str, Any], device_mac: str) -> Dict[str, Any]:
    return {
        "operational_state": status.get("operational_state", ""),
        "client_count": int(status.get("client_count", 0) or 0),
        "ssid": status.get("ssid", ""),
        "device_attached": hotspot_has_device(status, device_mac),
        "device_mac": device_mac,
    }


def network_window_indicates_offline(window: Dict[str, Any]) -> bool:
    analysis = window.get("analysis", {})
    return any(
        int(analysis.get(key, 0) or 0) > 0
        for key in ["conn_scan_fail_count", "rssi_fail_count", "wifi_offline_count"]
    )


def network_window_indicates_online(window: Dict[str, Any]) -> bool:
    analysis = window.get("analysis", {})
    return any(
        int(analysis.get(key, 0) or 0) > 0
        for key in [
            "rssi_ok_count",
            "cloud_login_count",
            "keepalive_count",
            "route_info_upload_count",
            "heartbeat_count",
            "cloud_status_online_count",
        ]
    )


def wait_for_device_online(session_dir: Path, artifact_dir: Path, timeout_s: float = 45.0) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    start_dt = datetime.now()
    deadline = time.time() + timeout_s
    online = False
    while time.time() < deadline:
        end_dt = datetime.now()
        ap_lines = read_lines_between("COM14", start_dt, end_dt, session_dir=session_dir)
        wb_lines = read_lines_between("COM13", start_dt, end_dt, session_dir=session_dir)
        if any(pattern.search(line) for pattern in ONLINE_SIGNAL_PATTERNS for line in ap_lines + wb_lines):
            online = True
            break
        time.sleep(1.0)
    end_dt = datetime.now()
    ap_lines = read_lines_between("COM14", start_dt, end_dt, session_dir=session_dir)
    wb_lines = read_lines_between("COM13", start_dt, end_dt, session_dir=session_dir)
    (artifact_dir / "COM14.log").write_text("\n".join(ap_lines) + ("\n" if ap_lines else ""), encoding="utf-8")
    (artifact_dir / "COM13.log").write_text("\n".join(wb_lines) + ("\n" if wb_lines else ""), encoding="utf-8")
    summary = {
        "action": "wait_device_online",
        "artifact_dir": str(artifact_dir),
        "success": online,
        "timeout_s": timeout_s,
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
        "ap_line_count": len(ap_lines),
        "wb_line_count": len(wb_lines),
    }
    save_json(artifact_dir / "summary.json", summary)
    if not online:
        raise RuntimeError("等待设备恢复在线超时，当前不适合继续发 APP/cloud 指令。")
    return summary


def empty_metrics() -> dict:
    metrics = {
        "cp_wake_count": 0,
        "cp_command_count": 0,
        "ap_wake_count": 0,
        "wb_wake_count": 0,
        "wb_online_wake_count": 0,
        "ap_asr_count": 0,
        "wb_asr_count": 0,
        "ap_online_asr_texts": [],
        "ap_cloud_tts_play_count": 0,
        "ap_cloud_tts_recv_count": 0,
        "ap_cloud_tts_start_count": 0,
        "ap_cloud_tts_stop_count": 0,
        "ap_ignore_broadcast_count": 0,
        "wb_playback_start_count": 0,
        "wb_playback_end_count": 0,
        "tone_ids": [],
        "ap_instruction_broadcast_mids": [],
        "ap_speech_broadcast_mids": [],
        "ap_cloud_tts_url_ids": [],
        "command_lines": [],
        "cp_command_keywords": [],
        "ap_asr_keywords": [],
        "wb_asr_keywords": [],
        "recognized_command_keywords": [],
        "unique_command_keyword_count": 0,
        "wb_tts_callback_ids": [],
        "ap_tts_fail_ids": [],
        "interrupt_reset_count": 0,
        "wake_during_playback_count": 0,
        "boot_marker_count": 0,
        "crash_marker_count": 0,
        "ap_instruction_broadcast_count": 0,
        "ap_speech_broadcast_count": 0,
        "asr_total": 0,
    }
    return metrics


def token_to_sequence_item(token) -> Optional[Dict[str, object]]:
    if token.kind in {"Wakeup", "Asr", "UnAsr", "online_Asr", "online_UnAsr"} and token.channel == "talk":
        return {"type": "tts", "text": token.value}
    if token.kind == "Action" and token.channel == "sleep":
        return {"type": "silence", "duration_ms": int(token.value)}
    return None


def append_unique(values: List[Union[str, int]], value: Union[str, int]) -> None:
    if value not in values:
        values.append(value)


def repair_mojibake_text(raw: str) -> str:
    # Some AP online ASR lines arrive as a UTF-8 byte stream that was partly
    # decoded as latin-1/cp1252-ish text; rebuild bytes and decode once.
    if not raw:
        return raw
    suspicious = False
    rebuilt = bytearray()
    for ch in raw:
        code = ord(ch)
        if code <= 0xFF:
            rebuilt.append(code)
            if code >= 0x80:
                suspicious = True
        else:
            rebuilt.extend(ch.encode("utf-8"))
    if not suspicious:
        return raw
    try:
        fixed = bytes(rebuilt).decode("utf-8")
    except UnicodeDecodeError:
        return raw
    return fixed


def normalize_keyword(raw: str) -> str:
    fixed = repair_mojibake_text(raw.strip())
    fixed = COMMON_MOJIBAKE_ALIASES.get(fixed, fixed)
    if fixed.startswith("\u5408\u80a5\u4eca\u5929\u7684\u5929") and fixed != "\u5408\u80a5\u4eca\u5929\u7684\u5929\u6c14":
        fixed = "\u5408\u80a5\u4eca\u5929\u7684\u5929\u6c14"
    return " ".join(fixed.lower().split())


def score_online_asr_candidate(raw: str) -> int:
    score = 0
    score += sum(4 for hint in ONLINE_ASR_TEXT_HINTS if hint in raw)
    score += sum(1 for ch in raw if "\u4e00" <= ch <= "\u9fff")
    score -= sum(3 for ch in raw if ch in ONLINE_ASR_MOJIBAKE_HINTS)
    score -= raw.count("\ufffd") * 5
    return score


def normalize_online_asr_text(raw: str) -> str:
    original = " ".join(raw.strip().split())
    if not original:
        return original

    candidates: List[str] = []

    def append_candidate(value: str) -> None:
        fixed = COMMON_MOJIBAKE_ALIASES.get(" ".join(value.strip().split()), " ".join(value.strip().split()))
        if fixed and fixed not in candidates:
            candidates.append(fixed)

    append_candidate(original)
    append_candidate(repair_mojibake_text(original))
    for encoding in ("gbk", "gb18030", "utf-8"):
        try:
            append_candidate(original.encode(encoding).decode("utf-8"))
        except Exception:
            pass
        try:
            append_candidate(original.encode(encoding, errors="ignore").decode("utf-8", errors="ignore"))
        except Exception:
            pass
    best = max(candidates, key=score_online_asr_candidate)
    for src, dst in TIME_NUMERAL_CANONICAL.items():
        best = best.replace(src, dst)
    if "帮我定个明天早上7点" in best and "闹钟" not in best:
        best = "帮我定个明天早上7点的闹钟"
    if "帮我定个明天早上" in best and "闹钟" in best and ("7" in best or "七" in best):
        best = "帮我定个明天早上7点的闹钟"
    return " ".join(best.split())


def extract_keywords(
    lines: List[str],
    pattern: re.Pattern[str],
    *,
    normalizer: Callable[[str], str] = normalize_keyword,
) -> List[str]:
    values: List[str] = []
    for line in lines:
        match = pattern.search(line)
        if not match:
            continue
        append_unique(values, normalizer(match.group(1)))
    return values


def extract_ints(lines: List[str], pattern: re.Pattern[str]) -> List[int]:
    values: List[int] = []
    for line in lines:
        match = pattern.search(line)
        if not match:
            continue
        append_unique(values, int(match.group(1)))
    return values


def extract_strings(lines: List[str], pattern: re.Pattern[str]) -> List[str]:
    values: List[str] = []
    for line in lines:
        match = pattern.search(line)
        if not match:
            continue
        append_unique(values, str(match.group(1)).strip())
    return values


def count_split_marker_events(
    lines: List[str],
    full_pattern: re.Pattern[str],
    split_head_pattern: re.Pattern[str],
    split_tail_pattern: re.Pattern[str],
) -> int:
    count = 0
    for index, line in enumerate(lines):
        if full_pattern.search(line):
            count += 1
            continue
        if index > 0 and split_head_pattern.search(lines[index - 1]) and split_tail_pattern.search(line):
            count += 1
    return count


def extract_split_keywords(
    lines: List[str],
    full_pattern: re.Pattern[str],
    split_head_pattern: re.Pattern[str],
    split_tail_pattern: re.Pattern[str],
    *,
    normalizer: Callable[[str], str] = normalize_keyword,
) -> List[str]:
    values: List[str] = []
    for index, line in enumerate(lines):
        match = full_pattern.search(line)
        if match:
            append_unique(values, normalizer(match.group(1)))
            continue
        if index > 0 and split_head_pattern.search(lines[index - 1]):
            split_match = split_tail_pattern.search(line)
            if split_match:
                append_unique(values, normalizer(split_match.group(1)))
    return values


def extract_split_ints(
    lines: List[str],
    full_pattern: re.Pattern[str],
    split_head_pattern: re.Pattern[str],
    split_tail_pattern: re.Pattern[str],
) -> List[int]:
    values: List[int] = []
    for index, line in enumerate(lines):
        match = full_pattern.search(line)
        if match:
            append_unique(values, int(match.group(1)))
            continue
        if index > 0 and split_head_pattern.search(lines[index - 1]):
            split_match = split_tail_pattern.search(line)
            if split_match:
                append_unique(values, int(split_match.group(1)))
    return values


def parse_json_substring(line: str, start_index: int = 0) -> Optional[dict]:
    brace = line.find("{", start_index)
    if brace < 0:
        return None
    end = line.rfind("}")
    while end > brace:
        try:
            payload = json.loads(line[brace : end + 1])
        except Exception:
            end = line.rfind("}", brace, end)
            continue
        if isinstance(payload, dict):
            return payload
        return None
    return None


def split_serial_log_lines(text: str) -> List[str]:
    """Split serial logs only on CR/LF so mojibake bytes like U+0085 don't break JSON payloads."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def read_serial_log_lines(path: Path, *, errors: str = "ignore") -> List[str]:
    if not path.exists():
        return []
    return split_serial_log_lines(path.read_text(encoding="utf-8", errors=errors))


def extract_algo_info_payloads(lines: List[str]) -> List[dict]:
    records: List[dict] = []
    for line in lines:
        marker_index = line.lower().find("algo info:")
        if marker_index < 0:
            continue
        payload = parse_json_substring(line, marker_index)
        if not isinstance(payload, dict):
            continue
        rlt = payload.get("rlt") or []
        record = rlt[0] if isinstance(rlt, list) and rlt and isinstance(rlt[0], dict) else {}
        records.append({"line": line, "payload": payload, "record": record})
    return records


def extract_wake_info_uploads(lines: List[str]) -> List[dict]:
    records: List[dict] = []
    for line in lines:
        if '"topic":"device.report.wakeInfo"' not in line:
            continue
        payload = parse_json_substring(line)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("topic", "")).strip() != "device.report.wakeInfo":
            continue
        params = payload.get("params") or {}
        afe_status = params.get("afeStatus") or {}
        wakeup_info = afe_status.get("wakeupInfo") or {}
        rlt = wakeup_info.get("rlt") or []
        record = rlt[0] if isinstance(rlt, list) and rlt and isinstance(rlt[0], dict) else {}
        records.append({"line": line, "payload": payload, "record": record})
    return records


def extract_algo_version_lines(lines: List[str]) -> List[str]:
    values: List[str] = []
    for line in lines:
        match = ALGO_VERSION_LINE_RE.search(line)
        if not match:
            continue
        append_unique(values, " ".join(match.group(1).strip().split()))
    return values


def extract_topic_mids(lines: List[str], topic: str) -> List[str]:
    values: List[str] = []
    topic_token = f'"topic":"{topic}"'
    for line in lines:
        if topic_token not in line:
            continue
        payload = parse_json_substring(line)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("topic", "")).strip() != topic:
            continue
        mid = str(payload.get("mid", "")).strip()
        if mid:
            append_unique(values, mid)
    return values


def extract_algo_version_uploads(lines: List[str]) -> List[dict]:
    records: List[dict] = []
    for line in lines:
        if '"topic":"device.report.sdkException"' not in line:
            continue
        payload = parse_json_substring(line)
        if not isinstance(payload, dict):
            continue
        params = payload.get("params") or {}
        exception = params.get("exception") or {}
        if str(exception.get("name", "")).strip() != "algo_version":
            continue
        records.append(
            {
                "line": line,
                "payload": payload,
                "content": " ".join(str(exception.get("content", "")).strip().split()),
                "device_id": str(params.get("deviceId", "")).strip(),
            }
        )
    return records


def extract_cloud_log_level_changes(lines: List[str]) -> List[dict]:
    records: List[dict] = []
    for line in lines:
        match = AP_CLOUD_LOGLEV_RE.search(line)
        if match:
            records.append(
                {
                    "line": line,
                    "level": int(match.group("level")),
                    "source": str(match.group("source")).strip().lower(),
                }
            )
            continue
        not_change_match = AP_CLOUD_LOGLEV_NOCHANGE_RE.search(line)
        if not_change_match:
            cloud_level = int(not_change_match.group("cloud_level"))
            mapped_level = map_cloud_upload_level_to_device_log_level(cloud_level)
            records.append(
                {
                    "line": line,
                    "level": mapped_level if mapped_level is not None else cloud_level,
                    "source": "not_change",
                    "cloud_level": cloud_level,
                }
            )
    return records


def extract_wakeup_upload_events(lines: List[str]) -> List[dict]:
    records: List[dict] = []
    for line in lines:
        lower = line.lower()
        if "wakeup_upload" not in lower:
            continue
        record: Dict[str, Any] = {"line": line}
        session_match = WAKEUP_UPLOAD_SESSION_RE.search(line)
        if session_match:
            record["kind"] = "session"
            record["session_id"] = session_match.group("session")
            records.append(record)
            continue
        if "upload progress header" in lower:
            header_match = re.search(r"header len:\s*(\d+)", line, re.I)
            record["kind"] = "progress_header"
            record["header_len"] = int(header_match.group(1)) if header_match else None
            records.append(record)
            continue
        if "wake audio upload response:" in lower:
            payload = parse_json_substring(line)
            record["kind"] = "response"
            record["payload"] = payload if isinstance(payload, dict) else {}
            records.append(record)
            continue
        if "wake audio is uploading, ignore" in lower:
            record["kind"] = "ignore"
            records.append(record)
            continue
        if "cloud wake config type:" in lower:
            record["kind"] = "cloud_config_type"
            records.append(record)
            continue
        if "user wake config type:" in lower:
            record["kind"] = "user_config_type"
            records.append(record)
            continue
    return records


def extract_config_query_payloads(lines: List[str]) -> List[dict]:
    records: List[dict] = []
    seen = set()
    for line in lines:
        payload: Optional[dict] = None
        data: Optional[dict] = None
        if '"topic":"cloud.order.config.query.reply"' in line:
            payload = parse_json_substring(line)
            if isinstance(payload, dict):
                maybe_data = payload.get("data")
                if isinstance(maybe_data, dict):
                    data = maybe_data
        elif '"accent":' in line and '"tts":' in line and '"wakeUpWords"' in line:
            payload = parse_json_substring(line)
            if isinstance(payload, dict):
                data = payload
        if not isinstance(data, dict):
            continue
        fingerprint = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        records.append({"line": line, "payload": payload or data, "data": data})
    return records


def extract_accent_uploads(lines: List[str]) -> List[dict]:
    records: List[dict] = []
    seen = set()
    for line in lines:
        lower = line.lower()
        marker_index = -1
        if "upload accent json:" in lower:
            marker_index = lower.find("upload accent json:")
        elif "set accent is" in lower:
            marker_index = lower.find("set accent is")
        if marker_index < 0:
            continue
        payload = parse_json_substring(line, marker_index)
        if not isinstance(payload, dict):
            continue
        fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        records.append({"line": line, "payload": payload})
    return records


def read_clean_logs_from_artifact_dir(artifact_dir: Path) -> Dict[str, List[str]]:
    raw_logs: Dict[str, List[str]] = {}
    window_dir = artifact_dir / "window_logs"
    for port in ["COM12", "COM13", "COM14"]:
        lines: List[str] = []
        window_path = window_dir / f"{port}.log"
        if window_path.exists():
            lines.extend(read_serial_log_lines(window_path, errors="ignore"))
        else:
            # Cloud/app setup helpers store per-port windows directly under the
            # artifact root, not under window_logs/.
            root_window = artifact_dir / f"{port}_window.log"
            root_excerpt = artifact_dir / f"{port}_excerpt.log"
            if root_window.exists():
                lines.extend(read_serial_log_lines(root_window, errors="ignore"))
            if root_excerpt.exists():
                lines.extend(read_serial_log_lines(root_excerpt, errors="ignore"))
        raw_logs[port] = lines
    return sanitize_logs(raw_logs)


def extract_uploaded_esr_versions(lines: List[str]) -> List[dict]:
    records: List[dict] = []
    for line in lines:
        marker_index = line.find("Upload ESR Version JSON:")
        if marker_index < 0:
            continue
        payload = parse_json_substring(line, marker_index)
        if not isinstance(payload, dict):
            continue
        records.append(
            {
                "line": line,
                "payload": payload,
                "device_id": str(payload.get("deviceId", "")).strip(),
                "esr_version": " ".join(str(payload.get("esrVersion", "")).strip().split()),
            }
        )
    return records


def build_interrupt_sequences(case) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    first_sequence: List[Dict[str, object]] = []
    interrupt_sequence: List[Dict[str, object]] = []
    seen_first_command = False
    entered_interrupt = False

    for token in case.tokens:
        item = token_to_sequence_item(token)
        if item is None:
            continue

        if entered_interrupt:
            interrupt_sequence.append(item)
            continue

        if seen_first_command and token.kind == "Wakeup" and token.channel == "talk":
            entered_interrupt = True
            interrupt_sequence.append(item)
            continue

        if seen_first_command:
            continue

        first_sequence.append(item)
        if token.kind in {"Asr", "UnAsr", "online_Asr", "online_UnAsr"} and token.channel == "talk":
            seen_first_command = True

    return first_sequence, interrupt_sequence


def wait_for_regex_line(
    port: str,
    start_dt: datetime,
    patterns: Tuple[re.Pattern[str], ...],
    timeout_s: float,
    session_dir: Path,
) -> Optional[str]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for line in read_lines_between(port, start_dt, session_dir=session_dir):
            if any(pattern.search(line) for pattern in patterns):
                return line
        time.sleep(0.2)
    return None


def run_low_latency_playback(audio_file: Path, device_key: str, execution_dir: Path, log_prefix: str) -> subprocess.CompletedProcess:
    device_key = str(device_key or "").strip()
    normalized_wav = execution_dir / f"{log_prefix}_normalized.wav"
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(audio_file),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(normalized_wav),
    ]
    ffmpeg_run = subprocess.run(
        ffmpeg_cmd,
        cwd=str(workspace_root()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    (execution_dir / f"{log_prefix}_normalize_stdout.log").write_text(ffmpeg_run.stdout, encoding="utf-8")
    (execution_dir / f"{log_prefix}_normalize_stderr.log").write_text(ffmpeg_run.stderr, encoding="utf-8")
    if ffmpeg_run.returncode != 0:
        return ffmpeg_run

    capture = run_audio_playback_adapter(
        normalized_wav,
        device_key,
        low_latency=True,
        timeout_s=playback_timeout_seconds(normalized_wav),
    )
    completed = capture.completed
    (execution_dir / f"{log_prefix}_stdout.log").write_text(completed.stdout, encoding="utf-8")
    (execution_dir / f"{log_prefix}_stderr.log").write_text(completed.stderr, encoding="utf-8")
    (execution_dir / f"{log_prefix}_command.json").write_text(
        json.dumps(
            {
                "cmd": list(completed.args),
                "returncode": completed.returncode,
                "device_key": device_key,
                "playback_device": playback_device_label(device_key),
                "adapter_executor": capture.action_result.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return completed


def pair_playback_windows(lines: List[str]) -> List[Tuple[datetime, datetime]]:
    starts: List[datetime] = []
    ends: List[datetime] = []
    for line in lines:
        ts = parse_prefixed_timestamp(line)
        if ts is None:
            continue
        if WB_PLAY_START_RE.search(line):
            starts.append(ts)
        if WB_PLAY_END_RE.search(line):
            ends.append(ts)

    windows: List[Tuple[datetime, datetime]] = []
    end_index = 0
    for start in starts:
        while end_index < len(ends) and ends[end_index] < start:
            end_index += 1
        if end_index >= len(ends):
            break
        windows.append((start, ends[end_index]))
        end_index += 1
    return windows


def count_cp_wakes_during_wb_playback(clean_logs: Dict[str, List[str]]) -> int:
    playback_windows = pair_playback_windows(clean_logs.get("COM13", []))
    if not playback_windows:
        return 0

    count = 0
    for line in clean_logs.get("COM12", []):
        if not CP_WAKE_RE.search(line):
            continue
        ts = parse_prefixed_timestamp(line)
        if ts is None:
            continue
        if any(start <= ts <= end for start, end in playback_windows):
            count += 1
    return count


def build_audio_sequence(case) -> List[Dict[str, object]]:
    sequence: List[Dict[str, object]] = []
    for token in case.tokens:
        item = token_to_sequence_item(token)
        if item is not None:
            sequence.append(item)
    return sequence


def dialog_mode_bundle(dialog_mode: str) -> dict:
    if dialog_mode == "half":
        return {
            "switch_text": TEXT_DIALOG_CLOSE,
            "switch_keyword": "guan bi zi ran dui hua",
            "switch_tone": 336,
        }
    if dialog_mode == "full":
        return {
            "switch_text": TEXT_DIALOG_OPEN,
            "switch_keyword": "da kai zi ran dui hua",
            "switch_tone": 335,
        }
    raise ValueError(f"unsupported dialog_mode: {dialog_mode}")


def build_timeout_sequence(noise_mode: str) -> List[Dict[str, object]]:
    if noise_mode == "silent":
        return [
            {"type": "tts", "text": WAKE_WORD_TEXT},
            {"type": "silence", "duration_ms": 16500},
        ]
    if noise_mode == "few":
        return [
            {"type": "tts", "text": WAKE_WORD_TEXT},
            {"type": "silence", "duration_ms": 900},
            {"type": "tts", "text": TEXT_CHITCHAT_1},
            {"type": "silence", "duration_ms": 16500},
        ]
    if noise_mode == "many":
        return [
            {"type": "tts", "text": WAKE_WORD_TEXT},
            {"type": "silence", "duration_ms": 900},
            {"type": "tts", "text": TEXT_CHITCHAT_1},
            {"type": "silence", "duration_ms": 1100},
            {"type": "tts", "text": TEXT_CHITCHAT_2},
            {"type": "silence", "duration_ms": 1100},
            {"type": "tts", "text": TEXT_CHITCHAT_3},
            {"type": "silence", "duration_ms": 16500},
        ]
    raise ValueError(f"unsupported noise_mode: {noise_mode}")


def build_half_duplex_first_command_sequence() -> List[Dict[str, object]]:
    return [
        {"type": "tts", "text": WAKE_WORD_TEXT},
        {"type": "silence", "duration_ms": 2200},
        {"type": "tts", "text": "打开空调"},
        {"type": "silence", "duration_ms": 2200},
        {"type": "tts", "text": "关闭空调"},
        {"type": "silence", "duration_ms": 2200},
        {"type": "tts", "text": TEXT_MODE_COOL},
        {"type": "silence", "duration_ms": 16500},
    ]


def build_switch_effect_sequence(switch_to: str) -> List[Dict[str, object]]:
    if switch_to == "half":
        switch_text = "关闭自然对话"
    elif switch_to == "full":
        switch_text = "打开自然对话"
    else:
        raise ValueError(f"unsupported switch target: {switch_to}")
    return [
        {"type": "tts", "text": WAKE_WORD_TEXT},
        {"type": "silence", "duration_ms": 2200},
        {"type": "tts", "text": switch_text},
        {"type": "silence", "duration_ms": 2200},
        {"type": "tts", "text": "打开空调"},
        {"type": "silence", "duration_ms": 16500},
    ]


def build_stress_sequence(cycles: int, seed: int) -> Tuple[List[Dict[str, object]], dict]:
    rng = random.Random(seed)
    sequence: List[Dict[str, object]] = []
    rounds: List[dict] = []
    command_pairs = [
        ("打开空调", "kong tiao kai ji"),
        ("关闭空调", "kong tiao guan ji"),
    ]

    for index in range(cycles):
        command_text, command_keyword = command_pairs[index % len(command_pairs)]
        wake_to_command_ms = rng.randint(120, 1250)
        settle_after_command_ms = rng.randint(2200, 3400)
        sequence.extend(
            [
                {"type": "tts", "text": WAKE_WORD_TEXT},
                {"type": "silence", "duration_ms": wake_to_command_ms},
                {"type": "tts", "text": command_text},
                {"type": "silence", "duration_ms": settle_after_command_ms},
            ]
        )
        rounds.append(
            {
                "round": index + 1,
                "wake_text": WAKE_WORD_TEXT,
                "command_text": command_text,
                "command_keyword": command_keyword,
                "wake_to_command_ms": wake_to_command_ms,
                "settle_after_command_ms": settle_after_command_ms,
            }
        )

    return sequence, {"seed": seed, "cycles": cycles, "rounds": rounds}


def resolve_stress_cycles(rules: dict) -> Tuple[int, Optional[int]]:
    default_cycles = int(rules.get("stress_cycles", 0))
    override_raw = os.environ.get("POLARIS_STRESS_CYCLES_OVERRIDE", "").strip()
    if not override_raw:
        return default_cycles, None
    try:
        override_value = int(override_raw)
    except ValueError as exc:
        raise RuntimeError(f"POLARIS_STRESS_CYCLES_OVERRIDE 不是合法整数: {override_raw}") from exc
    if override_value <= 0:
        raise RuntimeError(f"POLARIS_STRESS_CYCLES_OVERRIDE 必须大于 0: {override_raw}")
    return override_value, default_cycles


def build_online_stress_probe_case(case, rules: dict):
    cycles, default_cycles = resolve_stress_cycles(rules)
    seed = int(rules.get("stress_seed", cycles))
    rng = random.Random(seed)
    scenario = str(rules.get("scenario", "wake_only"))
    tokens: List[StepToken] = []
    rounds: List[dict] = []

    if scenario == "wake_only":
        for index in range(cycles):
            settle_after_wake_ms = rng.randint(2200, 3400)
            tokens.append(StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT))
            tokens.append(StepToken(kind="Action", channel="sleep", value=str(settle_after_wake_ms)))
            rounds.append(
                {
                    "round": index + 1,
                    "wake_text": WAKE_WORD_TEXT,
                    "settle_after_wake_ms": settle_after_wake_ms,
                }
            )
    elif scenario == "wake_command_interrupt":
        command_pairs = [
            ("打开空调", "kong tiao kai ji"),
            ("关闭空调", "kong tiao guan ji"),
        ]
        for index in range(cycles):
            command_text, command_keyword = command_pairs[index % len(command_pairs)]
            wake_to_command_ms = rng.randint(120, 920)
            interrupt_injected = (index % 5) == 0
            if interrupt_injected:
                settle_after_command_ms = rng.randint(520, 980)
            else:
                settle_after_command_ms = rng.randint(2200, 3400)
            tokens.extend(
                [
                    StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
                    StepToken(kind="Action", channel="sleep", value=str(wake_to_command_ms)),
                    StepToken(kind="Asr", channel="talk", value=command_text),
                    StepToken(kind="Action", channel="sleep", value=str(settle_after_command_ms)),
                ]
            )
            rounds.append(
                {
                    "round": index + 1,
                    "wake_text": WAKE_WORD_TEXT,
                    "command_text": command_text,
                    "command_keyword": command_keyword,
                    "wake_to_command_ms": wake_to_command_ms,
                    "settle_after_command_ms": settle_after_command_ms,
                    "interrupt_injected": interrupt_injected,
                }
            )
    else:
        raise RuntimeError(f"unsupported online stress scenario: {scenario}")

    probe_case = replace(case, tokens=tokens)
    metadata = {
        "scenario": scenario,
        "seed": seed,
        "cycles": cycles,
        "default_cycles": default_cycles,
        "override_env": os.environ.get("POLARIS_STRESS_CYCLES_OVERRIDE", "").strip(),
        "rounds": rounds,
    }
    return probe_case, metadata


def build_dialog_phase_plan(rules: dict) -> List[dict]:
    scenario = rules["scenario"]
    phases: List[dict] = []

    if scenario == "dialog_timeout":
        bundle = dialog_mode_bundle(rules["dialog_mode"])
        noise_mode = rules["noise_mode"]
        active_required_tones = [287]
        active_forbidden_tones: List[int] = []
        if noise_mode == "many":
            active_required_tones = [298, 287]
        elif noise_mode == "few":
            active_forbidden_tones = [298]
        elif noise_mode == "silent":
            active_forbidden_tones = [298]

        phases = [
            {
                "id": "switch_dialog_mode",
                "label": f"switch_{rules['dialog_mode']}",
                "sequence": [{"type": "tts", "text": bundle["switch_text"]}],
                "observe_after_ms": 8000,
                "required_keywords": [bundle["switch_keyword"]],
                "required_tones": [bundle["switch_tone"]],
                "min_cp_wake": 1,
                "min_ap_asr": 1,
                "min_wb_wake": 1,
            },
            {
                "id": "set_ac_on",
                "label": "set_on",
                "sequence": [{"type": "tts", "text": TEXT_AC_ON}],
                "observe_after_ms": 9000,
                "required_keywords": ["kong tiao kai ji"],
                "required_tones": [3],
                "min_cp_wake": 1,
                "min_ap_asr": 1,
            },
            {
                "id": "active_timeout_path",
                "label": f"on_{noise_mode}",
                "sequence": build_timeout_sequence(noise_mode),
                "observe_after_ms": 6000,
                "required_tones": active_required_tones,
                "forbidden_tones": active_forbidden_tones,
                "min_cp_wake": 1,
            },
            {
                "id": "set_ac_off",
                "label": "set_off",
                "sequence": [{"type": "tts", "text": TEXT_AC_OFF}],
                "observe_after_ms": 9000,
                "required_keywords": ["kong tiao guan ji"],
                "required_tones": [4],
                "min_cp_wake": 1,
                "min_ap_asr": 1,
            },
            {
                "id": "inactive_timeout_path",
                "label": f"off_{noise_mode}",
                "sequence": build_timeout_sequence(noise_mode),
                "observe_after_ms": 6000,
                "forbidden_tones": [287, 298],
                "min_cp_wake": 1,
            },
        ]
    elif scenario == "switch_effect":
        source_mode = rules["switch_from"]
        target_mode = rules["switch_to"]
        source_bundle = dialog_mode_bundle(source_mode)
        target_bundle = dialog_mode_bundle(target_mode)
        phases = [
            {
                "id": "prepare_source_mode",
                "label": f"prepare_{source_mode}",
                "sequence": [{"type": "tts", "text": source_bundle["switch_text"]}],
                "observe_after_ms": 8000,
                "required_keywords": [source_bundle["switch_keyword"]],
                "required_tones": [source_bundle["switch_tone"]],
                "min_cp_wake": 1,
                "min_ap_asr": 1,
            },
            {
                "id": "same_utterance_switch_effect",
                "label": f"{source_mode}_to_{target_mode}",
                "sequence": build_switch_effect_sequence(target_mode),
                "observe_after_ms": 6000,
                "required_keywords": [target_bundle["switch_keyword"]],
                "forbidden_keywords": ["kong tiao kai ji"],
                "required_tones": [target_bundle["switch_tone"]],
                "forbidden_tones": [3],
                "min_cp_wake": 1,
                "min_ap_asr": 1,
            },
        ]
    elif scenario == "half_duplex_first_command_only":
        phases = [
            {
                "id": "prepare_half_duplex",
                "label": "prepare_half",
                "sequence": [{"type": "tts", "text": TEXT_DIALOG_CLOSE}],
                "observe_after_ms": 8000,
                "required_keywords": ["guan bi zi ran dui hua"],
                "required_tones": [336],
                "min_cp_wake": 1,
                "min_ap_asr": 1,
            },
            {
                "id": "half_duplex_chain",
                "label": "first_command_only",
                "sequence": build_half_duplex_first_command_sequence(),
                "observe_after_ms": 6000,
                "required_keywords": ["kong tiao kai ji"],
                "required_tones": [3],
                "forbidden_tones": [4, 287],
                "min_cp_wake": 1,
                "min_ap_asr": 1,
            },
        ]
    elif scenario == "stress_interaction":
        bundle = dialog_mode_bundle(rules["dialog_mode"])
        stress_cycles = int(rules.get("stress_cycles", 8))
        stress_seed = int(rules.get("stress_seed", stress_cycles))
        stress_sequence, stress_plan = build_stress_sequence(stress_cycles, stress_seed)
        phases = [
            {
                "id": "switch_dialog_mode",
                "label": f"switch_{rules['dialog_mode']}",
                "sequence": [{"type": "tts", "text": bundle["switch_text"]}],
                "observe_after_ms": 8000,
                "required_keywords": [bundle["switch_keyword"]],
                "required_tones": [bundle["switch_tone"]],
                "min_cp_wake": 1,
                "min_ap_asr": 1,
                "min_wb_wake": 1,
            },
            {
                "id": "offline_stress_loop",
                "label": f"stress_{rules['dialog_mode']}",
                "sequence": stress_sequence,
                "observe_after_ms": 10000,
                "required_keywords": ["kong tiao kai ji", "kong tiao guan ji"],
                "required_tones": [3, 4],
                "min_cp_wake": stress_cycles,
                "min_cp_command": stress_cycles,
                "min_ap_asr": stress_cycles,
                "min_wb_asr": stress_cycles,
                "min_wb_playback_end": stress_cycles,
                "min_unique_command_keywords": 2,
                "max_boot_markers": 0,
                "max_crash_markers": 0,
                "metadata": stress_plan,
            },
        ]
    else:
        raise ValueError(f"unsupported dialog scenario: {scenario}")

    return phases


def write_phase_logs(phase_dir: Path, raw_logs: Dict[str, List[str]], clean_logs: Dict[str, List[str]]) -> None:
    logs_dir = phase_dir / "window_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for port, lines in raw_logs.items():
        (logs_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    for port, lines in clean_logs.items():
        (logs_dir / f"{port}.clean.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def evaluate_phase_checks(phase: dict, metrics: dict) -> List[dict]:
    checks: List[dict] = []

    def add_check(name: str, actual, expected, passed: bool) -> None:
        checks.append({"name": name, "actual": actual, "expected": expected, "passed": passed})

    if "min_cp_wake" in phase:
        add_check("cp_wake_count", metrics["cp_wake_count"], f">={phase['min_cp_wake']}", metrics["cp_wake_count"] >= phase["min_cp_wake"])
    if "min_cp_command" in phase:
        add_check("cp_command_count", metrics["cp_command_count"], f">={phase['min_cp_command']}", metrics["cp_command_count"] >= phase["min_cp_command"])
    if "min_ap_wake" in phase:
        add_check("ap_wake_count", metrics["ap_wake_count"], f">={phase['min_ap_wake']}", metrics["ap_wake_count"] >= phase["min_ap_wake"])
    if "min_ap_asr" in phase:
        add_check("ap_asr_count", metrics["ap_asr_count"], f">={phase['min_ap_asr']}", metrics["ap_asr_count"] >= phase["min_ap_asr"])
    if "min_wb_asr" in phase:
        add_check("wb_asr_count", metrics["wb_asr_count"], f">={phase['min_wb_asr']}", metrics["wb_asr_count"] >= phase["min_wb_asr"])
    if "min_wb_wake" in phase:
        add_check("wb_wake_count", metrics["wb_wake_count"], f">={phase['min_wb_wake']}", metrics["wb_wake_count"] >= phase["min_wb_wake"])
    if "min_wb_online_wake" in phase:
        add_check(
            "wb_online_wake_count",
            metrics["wb_online_wake_count"],
            f">={phase['min_wb_online_wake']}",
            metrics["wb_online_wake_count"] >= phase["min_wb_online_wake"],
        )
    if "min_wb_playback_end" in phase:
        add_check(
            "wb_playback_end_count",
            metrics["wb_playback_end_count"],
            f">={phase['min_wb_playback_end']}",
            metrics["wb_playback_end_count"] >= phase["min_wb_playback_end"],
        )
    if "min_ap_online_asr" in phase:
        add_check(
            "ap_online_asr_count",
            len(metrics["ap_online_asr_texts"]),
            f">={phase['min_ap_online_asr']}",
            len(metrics["ap_online_asr_texts"]) >= phase["min_ap_online_asr"],
        )
    if "max_ap_online_asr" in phase:
        add_check(
            "ap_online_asr_count_max",
            len(metrics["ap_online_asr_texts"]),
            f"<={phase['max_ap_online_asr']}",
            len(metrics["ap_online_asr_texts"]) <= phase["max_ap_online_asr"],
        )
    if "min_unique_command_keywords" in phase:
        add_check(
            "unique_command_keyword_count",
            metrics["unique_command_keyword_count"],
            f">={phase['min_unique_command_keywords']}",
            metrics["unique_command_keyword_count"] >= phase["min_unique_command_keywords"],
        )
    if "max_unique_command_keywords" in phase:
        add_check(
            "unique_command_keyword_count_max",
            metrics["unique_command_keyword_count"],
            f"<={phase['max_unique_command_keywords']}",
            metrics["unique_command_keyword_count"] <= phase["max_unique_command_keywords"],
        )
    if "min_ap_cloud_tts_play" in phase:
        add_check(
            "ap_cloud_tts_play_count",
            metrics["ap_cloud_tts_play_count"],
            f">={phase['min_ap_cloud_tts_play']}",
            metrics["ap_cloud_tts_play_count"] >= phase["min_ap_cloud_tts_play"],
        )
    if "min_ap_cloud_tts_recv" in phase:
        add_check(
            "ap_cloud_tts_recv_count",
            metrics["ap_cloud_tts_recv_count"],
            f">={phase['min_ap_cloud_tts_recv']}",
            metrics["ap_cloud_tts_recv_count"] >= phase["min_ap_cloud_tts_recv"],
        )
    if "min_ap_cloud_tts_start" in phase:
        add_check(
            "ap_cloud_tts_start_count",
            metrics["ap_cloud_tts_start_count"],
            f">={phase['min_ap_cloud_tts_start']}",
            metrics["ap_cloud_tts_start_count"] >= phase["min_ap_cloud_tts_start"],
        )
    if "min_ap_cloud_tts_stop" in phase:
        add_check(
            "ap_cloud_tts_stop_count",
            metrics["ap_cloud_tts_stop_count"],
            f">={phase['min_ap_cloud_tts_stop']}",
            metrics["ap_cloud_tts_stop_count"] >= phase["min_ap_cloud_tts_stop"],
        )
    if "max_ap_cloud_tts_play" in phase:
        add_check(
            "ap_cloud_tts_play_count_max",
            metrics["ap_cloud_tts_play_count"],
            f"<={phase['max_ap_cloud_tts_play']}",
            metrics["ap_cloud_tts_play_count"] <= phase["max_ap_cloud_tts_play"],
        )
    if "max_ap_cloud_tts_start" in phase:
        add_check(
            "ap_cloud_tts_start_count_max",
            metrics["ap_cloud_tts_start_count"],
            f"<={phase['max_ap_cloud_tts_start']}",
            metrics["ap_cloud_tts_start_count"] <= phase["max_ap_cloud_tts_start"],
        )
    if "max_ap_cloud_tts_stop" in phase:
        add_check(
            "ap_cloud_tts_stop_count_max",
            metrics["ap_cloud_tts_stop_count"],
            f"<={phase['max_ap_cloud_tts_stop']}",
            metrics["ap_cloud_tts_stop_count"] <= phase["max_ap_cloud_tts_stop"],
        )
    if "required_tones" in phase:
        tone_set = set(metrics["tone_ids"])
        required = set(phase["required_tones"])
        add_check("required_tones", sorted(tone_set), sorted(required), required.issubset(tone_set))
    if "forbidden_tones" in phase:
        tone_set = set(metrics["tone_ids"])
        forbidden = set(phase["forbidden_tones"])
        add_check("forbidden_tones", sorted(tone_set), sorted(forbidden), tone_set.isdisjoint(forbidden))
    if "required_keywords" in phase:
        actual = set(metrics["recognized_command_keywords"])
        required = {normalize_keyword(item) for item in phase["required_keywords"]}
        add_check("required_keywords", sorted(actual), sorted(required), required.issubset(actual))
    if "forbidden_keywords" in phase:
        actual = set(metrics["recognized_command_keywords"])
        forbidden = {normalize_keyword(item) for item in phase["forbidden_keywords"]}
        add_check("forbidden_keywords", sorted(actual), sorted(forbidden), actual.isdisjoint(forbidden))
    if "required_online_asr_texts" in phase:
        actual = [normalize_online_asr_text(item) for item in metrics["ap_online_asr_texts"]]
        required = [normalize_online_asr_text(item) for item in phase["required_online_asr_texts"]]
        add_check("required_online_asr_texts", actual, required, set(required).issubset(set(actual)))
    if "forbidden_online_asr_texts" in phase:
        actual = [normalize_online_asr_text(item) for item in metrics["ap_online_asr_texts"]]
        forbidden = {normalize_online_asr_text(item) for item in phase["forbidden_online_asr_texts"]}
        add_check("forbidden_online_asr_texts", actual, sorted(forbidden), forbidden.isdisjoint(set(actual)))
    if "max_boot_markers" in phase:
        add_check(
            "boot_marker_count",
            metrics["boot_marker_count"],
            f"<={phase['max_boot_markers']}",
            metrics["boot_marker_count"] <= phase["max_boot_markers"],
        )
    if "max_crash_markers" in phase:
        add_check(
            "crash_marker_count",
            metrics["crash_marker_count"],
            f"<={phase['max_crash_markers']}",
            metrics["crash_marker_count"] <= phase["max_crash_markers"],
        )
    return checks


def execute_dialog_phase(
    phase: dict,
    index: int,
    device_key: str,
    execution_dir: Path,
    session_dir: Path,
    tone_catalog: dict,
) -> dict:
    original_phase = phase
    phase = adapt_phase_for_current_project(phase)
    phase_dir = execution_dir / f"{index:02d}_{phase['id']}"
    phase_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = phase_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_file = audio_dir / f"{phase['id']}.wav"
    audio_manifest = build_sequence(phase["sequence"], audio_file)

    start_dt = datetime.now()
    playback = run_playback(audio_file, device_key, phase_dir, log_prefix="phase_play")
    time.sleep(int(phase.get("observe_after_ms", 8000)) / 1000.0)
    end_dt = datetime.now()

    raw_logs: Dict[str, List[str]] = {}
    for port in configured_log_ports():
        raw_logs[port] = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
    add_canonical_log_aliases(raw_logs)
    clean_logs = sanitize_logs(raw_logs)
    add_canonical_log_aliases(clean_logs)
    write_phase_logs(phase_dir, raw_logs, clean_logs)

    window_summary = summarize_window(clean_logs)
    metrics = collect_metrics(clean_logs, window_summary)
    checks = evaluate_phase_checks(phase, metrics)
    if playback.returncode != 0:
        phase_result = "BLOCKED"
        phase_reason = "播放音频阶段失败，未进入日志判定。"
    elif all(item["passed"] for item in checks):
        phase_result = "PASS"
        phase_reason = phase.get("label", phase["id"])
    else:
        phase_result = "FAIL"
        failed = [item for item in checks if not item["passed"]]
        head = failed[0]
        phase_reason = f"{phase['id']} 未满足 {head['name']}，actual={head['actual']} expected={head['expected']}"

    phase_payload = {
        "phase_id": phase["id"],
        "label": phase.get("label", phase["id"]),
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
        "playback": {
            "audio_file": str(audio_file),
            "manifest": audio_manifest,
            "returncode": playback.returncode,
        },
        "window_summary": window_summary,
        "metrics": metrics,
        "checks": checks,
        "result": phase_result,
        "reason": phase_reason,
        "tone_names": {str(tone_id): tone_catalog.get(tone_id, "unknown") for tone_id in metrics["tone_ids"]},
    }
    if "metadata" in phase:
        phase_payload["metadata"] = phase["metadata"]
    if phase is not original_phase:
        phase_payload["original_phase"] = original_phase
    (phase_dir / "phase_result.json").write_text(json.dumps(phase_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return phase_payload


def build_dialog_case_excerpt(case, diagnosis: dict, phases: List[dict], tone_catalog: dict) -> str:
    lines = [
        f"# {case.case_id}",
        "",
        f"- Name: `{case.name}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Confidence: `{diagnosis['confidence']}`",
        f"- Reason: {diagnosis['reason']}",
        "",
        "## Phases",
        "",
    ]
    for phase in phases:
        lines.append(f"- `{phase['phase_id']}` -> `{phase['result']}` | {phase['reason']}")
        lines.append(f"  - keywords=`{phase['metrics']['recognized_command_keywords']}` tones=`{phase['metrics']['tone_ids']}`")
        if phase.get("metadata"):
            lines.append(f"  - metadata=`{phase['metadata']}`")
    lines += [
        "",
        "## Tone Reference",
        "",
    ]
    used_tones = []
    for phase in phases:
        for tone_id in phase["metrics"]["tone_ids"]:
            if tone_id not in used_tones:
                used_tones.append(tone_id)
    if used_tones:
        for tone_id in used_tones:
            lines.append(f"- `{tone_id}` | `{tone_catalog.get(tone_id, 'unknown')}`")
    else:
        lines.append("- <none>")
    return "\n".join(lines) + "\n"


def run_dialog_phase_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    state_dir = execution_dir / "state"
    before_state = snapshot("before", state_dir, session_dir)

    phase_plan = build_dialog_phase_plan(rules)
    phase_results: List[dict] = []
    for index, phase in enumerate(phase_plan, start=1):
        phase_results.append(
            execute_dialog_phase(
                phase=phase,
                index=index,
                device_key=device_key,
                execution_dir=execution_dir,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        )
        time.sleep(1.0)

    after_state = snapshot("after", state_dir, session_dir)
    state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")

    blocked_phases = [phase for phase in phase_results if phase["result"] == "BLOCKED"]
    failed_phases = [phase for phase in phase_results if phase["result"] == "FAIL"]
    if blocked_phases:
        diagnosis = {
            "result": "BLOCKED",
            "confidence": rules.get("confidence", "medium"),
            "reason": blocked_phases[0]["reason"],
        }
    elif failed_phases:
        diagnosis = {
            "result": "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": failed_phases[0]["reason"],
        }
    else:
        diagnosis = {
            "result": "PASS",
            "confidence": rules.get("confidence", "medium"),
            "reason": rules["notes"],
        }

    fingerprint = {
        "case_id": case.case_id,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "phase_results": {phase["phase_id"]: phase["result"] for phase in phase_results},
        "phase_tones": {phase["phase_id"]: phase["metrics"]["tone_ids"] for phase in phase_results},
        "phase_keywords": {phase["phase_id"]: phase["metrics"]["recognized_command_keywords"] for phase in phase_results},
    }
    judge_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "reason": diagnosis["reason"],
        "phases": phase_results,
    }
    excerpt = build_dialog_case_excerpt(case, diagnosis, phase_results, tone_catalog)

    (execution_dir / "judge.json").write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "failure_excerpt.md").write_text(excerpt, encoding="utf-8")

    result_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "execution_dir": str(execution_dir),
        "diagnosis": diagnosis,
        "phases": phase_results,
        "states": {
            "before": str(before_state),
            "after": str(after_state),
            "diff": str(state_diff),
        },
        "artifacts": {
            "judge": str(execution_dir / "judge.json"),
            "fingerprint": str(execution_dir / "fingerprint.json"),
            "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
        },
    }
    result_path = execution_dir / "doc_case_result.json"
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# {case.case_id}",
        "",
        f"- Name: `{case.name}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Confidence: `{diagnosis['confidence']}`",
        f"- Reason: {diagnosis['reason']}",
        "",
        "## Phase checks",
        "",
    ]
    for phase in phase_results:
        lines.append(f"- `{phase['phase_id']}` -> `{phase['result']}` | {phase['reason']}")
        for check in phase["checks"]:
            lines.append(f"  - `{check['name']}` -> `{'PASS' if check['passed'] else 'MISS'}` | actual=`{check['actual']}` expected=`{check['expected']}`")
    (execution_dir / "doc_case_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result_path



def shell_commands(case) -> List[str]:
    return [token.value for token in case.tokens if token.kind == "Action" and token.channel == "shell"]



def route_command(command: str) -> str:
    return "COM13" if command.lower().startswith("listen ") else "COM14"



def collect_metrics(clean_logs: Dict[str, List[str]], window_summary: dict) -> dict:
    cp_command_keywords = extract_keywords(clean_logs.get("COM12", []), CP_CMD_KEYWORD_RE)
    ap_asr_keywords = extract_keywords(clean_logs.get("COM14", []), ASR_KEYWORD_RE)
    ap_online_asr_texts: List[str] = []
    for value in extract_keywords(clean_logs.get("COM14", []), AP_ONLINE_ASR_RE, normalizer=normalize_online_asr_text):
        append_unique(ap_online_asr_texts, value)
    for value in extract_keywords(clean_logs.get("COM13", []), AP_ONLINE_ASR_RE, normalizer=normalize_online_asr_text):
        append_unique(ap_online_asr_texts, value)
    wb_asr_keywords = extract_split_keywords(
        clean_logs.get("COM13", []),
        ASR_KEYWORD_RE,
        SPLIT_ASR_HEAD_RE,
        SPLIT_ASR_TAIL_RE,
    )
    recognized_keywords: List[str] = []
    for value in cp_command_keywords + ap_asr_keywords + wb_asr_keywords:
        append_unique(recognized_keywords, value)
    ap_instruction_broadcast_mids = extract_topic_mids(clean_logs.get("COM14", []), "cloud.instructions.audioBroadcast")
    ap_speech_broadcast_mids = extract_topic_mids(clean_logs.get("COM14", []), "cloud.speech.broadcast")
    ap_cloud_tts_url_ids = extract_strings(clean_logs.get("COM14", []), STREAM_TTS_URL_ID_RE)

    metrics = {
        "cp_wake_count": sum(1 for line in clean_logs.get("COM12", []) if CP_WAKE_RE.search(line)),
        "cp_command_count": sum(1 for line in clean_logs.get("COM12", []) if CP_CMD_RE.search(line)),
        "ap_wake_count": sum(1 for line in clean_logs.get("COM14", []) if AP_WAKE_RE.search(line)),
        "wb_wake_count": sum(1 for line in clean_logs.get("COM13", []) if WB_WAKE_RE.search(line)),
        "wb_online_wake_count": sum(1 for line in clean_logs.get("COM13", []) if WB_ONLINE_WAKE_RE.search(line)),
        "ap_asr_count": sum(1 for line in clean_logs.get("COM14", []) if AP_ASR_RE.search(line)),
        "wb_asr_count": count_split_marker_events(
            clean_logs.get("COM13", []),
            WB_ASR_RE,
            SPLIT_ASR_HEAD_RE,
            SPLIT_ASR_TAIL_RE,
        ),
        "ap_online_asr_texts": ap_online_asr_texts,
        "ap_cloud_tts_play_count": sum(1 for line in clean_logs.get("COM14", []) if AP_CLOUD_TTS_PLAY_RE.search(line)),
        "ap_cloud_tts_recv_count": sum(1 for line in clean_logs.get("COM14", []) if AP_CLOUD_TTS_RECV_RE.search(line)),
        "ap_cloud_tts_start_count": sum(1 for line in clean_logs.get("COM14", []) if AP_CLOUD_TTS_START_RE.search(line)),
        "ap_cloud_tts_stop_count": sum(1 for line in clean_logs.get("COM14", []) if AP_CLOUD_TTS_STOP_RE.search(line)),
        "ap_ignore_broadcast_count": sum(1 for line in clean_logs.get("COM14", []) if AP_IGNORE_BROADCAST_RE.search(line)),
        "wb_playback_start_count": sum(1 for line in clean_logs.get("COM13", []) if WB_PLAY_START_RE.search(line)),
        "wb_playback_end_count": sum(1 for line in clean_logs.get("COM13", []) if WB_PLAY_END_RE.search(line)),
        "tone_ids": [item["tone_id"] for item in window_summary["tones"]],
        "ap_instruction_broadcast_mids": ap_instruction_broadcast_mids,
        "ap_speech_broadcast_mids": ap_speech_broadcast_mids,
        "ap_cloud_tts_url_ids": ap_cloud_tts_url_ids,
        "command_lines": [line for port in ("COM13", "COM14") for line in clean_logs.get(port, []) if COMMAND_RE.search(line)],
        "cp_command_keywords": cp_command_keywords,
        "ap_asr_keywords": ap_asr_keywords,
        "wb_asr_keywords": wb_asr_keywords,
        "recognized_command_keywords": recognized_keywords,
        "unique_command_keyword_count": len(recognized_keywords),
        "wb_tts_callback_ids": extract_split_ints(
            clean_logs.get("COM13", []),
            WB_TTS_CALLBACK_RE,
            SPLIT_TTS_HEAD_RE,
            SPLIT_TTS_TAIL_RE,
        ),
        "ap_tts_fail_ids": extract_ints(clean_logs.get("COM14", []), AP_TTS_FAIL_RE),
        "interrupt_reset_count": sum(1 for line in clean_logs.get("COM14", []) if PLAYER_RESET_USER_RE.search(line)),
        "wake_during_playback_count": count_cp_wakes_during_wb_playback(clean_logs),
        "boot_marker_count": sum(
            1
            for port in ("COM12", "COM13", "COM14")
            for line in clean_logs.get(port, [])
            if BOOT_MARKER_RE.search(line)
        ),
        "crash_marker_count": sum(
            1
            for port in ("COM12", "COM13", "COM14")
            for line in clean_logs.get(port, [])
            if CRASH_MARKER_RE.search(line)
        ),
        "ap_instruction_broadcast_count": len(ap_instruction_broadcast_mids),
        "ap_speech_broadcast_count": len(ap_speech_broadcast_mids),
    }
    metrics["asr_total"] = metrics["ap_asr_count"] + metrics["wb_asr_count"]
    return metrics


def case_online_text_expectations(case) -> Tuple[List[str], List[str]]:
    required: List[str] = []
    forbidden: List[str] = []
    for token in case.tokens:
        if token.channel != "talk":
            continue
        if token.kind == "online_Asr":
            append_unique(required, normalize_online_asr_text(str(token.value)))
        elif token.kind == "online_UnAsr":
            append_unique(forbidden, normalize_online_asr_text(str(token.value)))
    return required, forbidden


def case_tail_sleep_ms(case) -> int:
    for token in reversed(case.tokens):
        if token.kind == "Action" and token.channel == "sleep":
            try:
                return int(token.value)
            except Exception:
                return 0
    return 0


def case_wakeup_count(case) -> int:
    return sum(1 for token in case.tokens if token.kind == "Wakeup" and token.channel == "talk")


def dialog_observe_after_ms(case, rules: dict) -> int:
    base_ms = int(rules.get("observe_after_ms", 10000))
    timeout_seconds = int(rules.get("timeout_seconds", 15))
    notes = str(rules.get("notes", ""))
    tail_sleep_ms = case_tail_sleep_ms(case)
    if tail_sleep_ms >= timeout_seconds * 1000 or "超时" in notes or "等待" in notes:
        return max(base_ms, timeout_seconds * 1000 + 12000)
    return base_ms


def extract_audio_broadcast_records(lines: List[str]) -> List[dict]:
    records: List[dict] = []
    seen = set()
    for line in lines:
        if '"topic":"cloud.instructions.audioBroadcast"' not in line:
            continue
        payload = parse_json_substring(line)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("topic", "")).strip() != "cloud.instructions.audioBroadcast":
            continue
        mid = str(payload.get("mid", "")).strip()
        key = mid or line
        if key in seen:
            continue
        seen.add(key)
        params = payload.get("params") or {}
        content = payload.get("content") or []
        texts: List[str] = []
        urls: List[str] = []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", "")).strip()
                url = str(item.get("url", "")).strip()
                if text:
                    texts.append(normalize_online_asr_text(text))
                if url:
                    urls.append(url)
        records.append(
            {
                "mid": mid,
                "skill_id": str(params.get("mideaSkillId", "")).strip(),
                "stream": bool(params.get("stream")),
                "end_session": bool(params.get("endSession")),
                "texts": texts,
                "urls": urls,
                "line": line,
                "timestamp": parse_prefixed_timestamp(line),
            }
        )
    return records


def collect_dialog_behavior_metrics(clean_logs: Dict[str, List[str]]) -> dict:
    ap_lines = clean_logs.get("COM14", [])
    audio_records = extract_audio_broadcast_records(ap_lines)
    timeout_audio_ids: List[int] = []
    timeout_audio_times: List[datetime] = []
    session_timeout_times: List[datetime] = []
    player_stop_times: List[datetime] = []
    wake_times: List[datetime] = []
    half_timeout_values: List[int] = []
    full_timeout_values: List[int] = []
    restart_session_values: List[int] = []

    for line in ap_lines:
        ts = parse_prefixed_timestamp(line)
        if ts and AP_WAKE_RE.search(line):
            wake_times.append(ts)
        if ts and AP_CLOUD_TTS_STOP_RE.search(line):
            player_stop_times.append(ts)
        if ts and SESSION_TIMEOUT_RE.search(line):
            session_timeout_times.append(ts)
        timeout_match = TIMEOUT_AUDIO_RE.search(line)
        if timeout_match:
            tone_id = int(timeout_match.group(1))
            if tone_id not in timeout_audio_ids:
                timeout_audio_ids.append(tone_id)
            if ts:
                timeout_audio_times.append(ts)
        half_match = HALF_TIMEOUT_REFRESH_RE.search(line)
        if half_match:
            append_unique(half_timeout_values, int(half_match.group(1)))
        full_match = FULL_TIMEOUT_REFRESH_RE.search(line)
        if full_match:
            append_unique(full_timeout_values, int(full_match.group(1)))
        restart_match = RESTART_SESSION_TIMER_RE.search(line)
        if restart_match:
            append_unique(restart_session_values, int(restart_match.group(1)))

    successful_response_records = [
        item
        for item in audio_records
        if str(item.get("skill_id", "")).strip().lower() != "asrinvalid"
        and (item.get("urls") or item.get("texts"))
    ]
    asr_invalid_records = [
        item for item in audio_records if str(item.get("skill_id", "")).strip().lower() == "asrinvalid"
    ]
    return {
        "audio_broadcast_records": audio_records,
        "successful_response_records": successful_response_records,
        "asr_invalid_records": asr_invalid_records,
        "timeout_audio_ids": timeout_audio_ids,
        "timeout_audio_times": timeout_audio_times,
        "session_timeout_times": session_timeout_times,
        "player_stop_times": player_stop_times,
        "wake_times": wake_times,
        "half_timeout_values": half_timeout_values,
        "full_timeout_values": full_timeout_values,
        "restart_session_values": restart_session_values,
    }


def dialog_timeout_anchor(
    behavior: dict,
    *,
    after_time: Optional[datetime] = None,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    candidates = [
        ts
        for ts in behavior["session_timeout_times"]
        if ts is not None and (after_time is None or ts > after_time)
    ]
    if not candidates:
        return None, None
    timeout_ts = candidates[0]
    anchor_candidates = [
        ts
        for ts in behavior["player_stop_times"]
        if ts is not None and ts < timeout_ts and (after_time is None or ts > after_time)
    ]
    anchor_ts = anchor_candidates[-1] if anchor_candidates else after_time
    return anchor_ts, timeout_ts


def evaluate_dialog_behavior_case(
    case,
    behavior_case,
    rules: dict,
    payload: dict,
    clean_logs: Dict[str, List[str]],
    setup_records: Optional[List[dict]] = None,
) -> Tuple[dict, dict]:
    rules = adapt_rules_for_current_project(rules)
    metrics = payload["metrics"]
    actual_online_texts = [normalize_online_asr_text(item) for item in metrics["ap_online_asr_texts"]]
    required_online_texts, forbidden_online_texts = case_online_text_expectations(behavior_case)
    behavior = collect_dialog_behavior_metrics(clean_logs)
    successful_response_count = len(behavior["successful_response_records"])
    closure_prompt_count = len(behavior["asr_invalid_records"]) + len(behavior["timeout_audio_ids"])
    wake_count = case_wakeup_count(behavior_case)
    tail_sleep_ms = case_tail_sleep_ms(behavior_case)
    timeout_seconds = int(rules.get("timeout_seconds", 15))
    notes = str(rules.get("notes", ""))
    full_duplex = bool(rules.get("full_duplex_enable", False))
    expect_no_timeout_prompt = "不应再播报超时提示" in notes
    has_forbidden_followup = bool(forbidden_online_texts)
    has_required_commands = bool(required_online_texts)
    long_tail_wait = tail_sleep_ms >= timeout_seconds * 1000
    repeated_wake_only = wake_count > 1 and not has_required_commands and not has_forbidden_followup

    expect_timeout_prompt = False
    require_timeout_timing = False
    if expect_no_timeout_prompt:
        expect_timeout_prompt = False
    elif repeated_wake_only:
        expect_timeout_prompt = True
    elif not has_required_commands and not has_forbidden_followup:
        expect_timeout_prompt = True
        require_timeout_timing = wake_count <= 1
    elif has_forbidden_followup and not has_required_commands:
        expect_timeout_prompt = True
        require_timeout_timing = True
    elif has_required_commands and full_duplex and long_tail_wait and not has_forbidden_followup:
        expect_timeout_prompt = True
        require_timeout_timing = True

    checks: List[dict] = []

    def add_check(name: str, actual, expected, passed: bool) -> None:
        checks.append({"name": name, "actual": actual, "expected": expected, "passed": passed})

    add_check("cloud_apply_success", bool(setup_records and all(item.get("success", False) for item in setup_records if item.get("action") in {"cloud_full_duplex", "prepare_local_hotspot", "wait_device_online", "cloud_wakeup_word", "cloud_mic_switch", "voice_dialog_switch", "voice_command_phrase", "cloud_wakeup_threshold"})), True, True if not setup_records else all(item.get("success", False) for item in setup_records if item.get("action") in {"cloud_full_duplex", "prepare_local_hotspot", "wait_device_online", "cloud_wakeup_word", "cloud_mic_switch", "voice_dialog_switch", "voice_command_phrase", "cloud_wakeup_threshold"}))
    if "min_cp_wake" in rules:
        add_check("cp_wake_count", metrics["cp_wake_count"], f">={rules['min_cp_wake']}", metrics["cp_wake_count"] >= rules["min_cp_wake"])
    if "min_ap_wake" in rules:
        add_check("ap_wake_count", metrics["ap_wake_count"], f">={rules['min_ap_wake']}", metrics["ap_wake_count"] >= rules["min_ap_wake"])

    if required_online_texts:
        actual_set = set(actual_online_texts)
        required_set = set(required_online_texts)
        required_command_keywords = [normalize_keyword(str(item)) for item in rules.get("required_command_keywords", [])]
        recognized_keyword_set = set(metrics["recognized_command_keywords"])
        command_keyword_fallback = bool(required_command_keywords) and set(required_command_keywords).issubset(recognized_keyword_set)
        required_online_pass = required_set.issubset(actual_set) or command_keyword_fallback
        add_check(
            "required_online_asr_texts",
            {
                "actual_online_asr_texts": actual_online_texts,
                "recognized_command_keywords": metrics["recognized_command_keywords"],
            },
            {
                "required_online_asr_texts": required_online_texts,
                "required_command_keywords": required_command_keywords,
            },
            required_online_pass,
        )
        add_check(
            "successful_response_count",
            successful_response_count,
            f">={len(required_online_texts)}",
            successful_response_count >= len(required_online_texts),
        )
    else:
        add_check("successful_response_count", successful_response_count, ">=0", True)

    if forbidden_online_texts:
        actual_forbidden = set(actual_online_texts)
        forbidden_full_pass = set(forbidden_online_texts).isdisjoint(actual_forbidden)
        add_check("forbidden_online_asr_texts", actual_online_texts, forbidden_online_texts, forbidden_full_pass)
        add_check(
            "successful_response_count_max",
            successful_response_count,
            f"<={len(required_online_texts)}",
            successful_response_count <= len(required_online_texts),
        )
    elif not required_online_texts and not expect_no_timeout_prompt:
        add_check("successful_response_count_reference", successful_response_count, "reference-only", True)

    add_check(
        "closure_prompt_count" if expect_timeout_prompt or repeated_wake_only else "closure_prompt_count_reference",
        closure_prompt_count,
        ">=1" if expect_timeout_prompt or repeated_wake_only else "reference-only",
        closure_prompt_count >= 1 if expect_timeout_prompt or repeated_wake_only else True,
    )
    if expect_no_timeout_prompt:
        add_check("closure_prompt_count_max", closure_prompt_count, "<=0", closure_prompt_count <= 0)

    timeout_anchor_after = behavior["wake_times"][-1] if behavior["wake_times"] else None
    if has_required_commands and behavior["successful_response_records"]:
        response_times = [
            item["timestamp"] for item in behavior["successful_response_records"] if item.get("timestamp") is not None
        ]
        if response_times:
            timeout_anchor_after = max(response_times)
    anchor_ts, timeout_ts = dialog_timeout_anchor(behavior, after_time=timeout_anchor_after)
    timeout_elapsed_s = None if not anchor_ts or not timeout_ts else round((timeout_ts - anchor_ts).total_seconds(), 3)
    if require_timeout_timing:
        tolerance_low = 4.0
        tolerance_high = 8.0
        timing_pass = (
            timeout_elapsed_s is not None
            and (timeout_seconds - tolerance_low) <= timeout_elapsed_s <= (timeout_seconds + tolerance_high)
        )
        add_check(
            "timeout_elapsed_s",
            timeout_elapsed_s,
            f"{timeout_seconds}s±({tolerance_low},{tolerance_high})",
            timing_pass,
        )

    all_passed = all(item["passed"] for item in checks)
    if all_passed:
        reason = rules["notes"]
    elif (
        metrics["cp_wake_count"] == 0
        and metrics["ap_wake_count"] == 0
        and metrics.get("wb_wake_count", 0) == 0
        and metrics.get("wb_online_wake_count", 0) == 0
    ):
        reason = "整段交互没有形成稳定 wake 证据，当前更像设备/音频链路问题而不是断言问题。"
    else:
        first_failed = next(item for item in checks if not item["passed"])
        reason = f"自然对话行为未满足 {first_failed['name']}，actual={first_failed['actual']} expected={first_failed['expected']}。"

    return (
        {
            "result": "PASS" if all_passed else "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": reason,
            "checks": checks,
        },
        {
            "required_online_asr_texts": required_online_texts,
            "forbidden_online_asr_texts": forbidden_online_texts,
            "actual_online_asr_texts": actual_online_texts,
            "successful_response_count": successful_response_count,
            "successful_response_urls": metrics["ap_cloud_tts_url_ids"],
            "asr_invalid_broadcast_count": len(behavior["asr_invalid_records"]),
            "timeout_audio_ids": behavior["timeout_audio_ids"],
            "session_timeout_count": len(behavior["session_timeout_times"]),
            "timeout_elapsed_s": timeout_elapsed_s,
            "half_timeout_values": behavior["half_timeout_values"],
            "full_timeout_values": behavior["full_timeout_values"],
            "restart_session_values": behavior["restart_session_values"],
        },
    )


def build_dialog_behavior_excerpt(case, diagnosis: dict, payload: dict, dialog_info: dict, clean_logs: Dict[str, List[str]]) -> str:
    lines = [
        f"# {case.case_id}",
        "",
        f"- Name: `{case.name}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Confidence: `{diagnosis['confidence']}`",
        f"- Reason: {diagnosis['reason']}",
        "",
        "## Checks",
        "",
    ]
    for item in diagnosis["checks"]:
        lines.append(f"- `{item['name']}` -> `{'PASS' if item['passed'] else 'MISS'}` | actual=`{item['actual']}` expected=`{item['expected']}`")
    lines += [
        "",
        "## Dialog Info",
        "",
        f"- required_online_asr_texts=`{dialog_info['required_online_asr_texts']}`",
        f"- forbidden_online_asr_texts=`{dialog_info['forbidden_online_asr_texts']}`",
        f"- actual_online_asr_texts=`{dialog_info['actual_online_asr_texts']}`",
        f"- successful_response_urls=`{dialog_info['successful_response_urls']}`",
        f"- asr_invalid_broadcast_count=`{dialog_info['asr_invalid_broadcast_count']}`",
        f"- timeout_audio_ids=`{dialog_info['timeout_audio_ids']}`",
        f"- session_timeout_count=`{dialog_info['session_timeout_count']}`",
        f"- timeout_elapsed_s=`{dialog_info['timeout_elapsed_s']}`",
        "",
        "## Key lines",
        "",
    ]
    key_lines: List[str] = []
    for line in clean_logs.get("COM14", []):
        lower = line.lower()
        if any(
            token in lower
            for token in [
                "wakeup_callback",
                "online_asr_callbak",
                "cloud.instructions.audiobroadcast",
                "asrinvalid",
                "stop interactive by session timeout",
                "play timeout audio",
                "ttsplayer report state: play 2",
                "ttsplayer report state: stop 6",
                "tts playing with",
                "fullduplex timeout refresh",
                "halfduplex timeout refresh",
                "restart session timer with",
            ]
        ):
            key_lines.append(line)
    if key_lines:
        for line in key_lines[:80]:
            lines.append(f"- `{line}`")
    else:
        lines.append("- <none>")
    return "\n".join(lines) + "\n"


def build_threshold_case_excerpt(
    case,
    diagnosis: dict,
    metrics: dict,
    tone_catalog: dict,
    clean_logs: Dict[str, List[str]],
    threshold_info: dict,
) -> str:
    excerpt = build_excerpt(case, diagnosis, metrics, tone_catalog, clean_logs).rstrip()
    setup_info = threshold_info.get("setup_info", {})
    lines = [
        excerpt,
        "",
        "## Threshold Setup",
        "",
        f"- threshold_request_value=`{threshold_info.get('threshold_request_value')}`",
        f"- level_values=`{setup_info.get('level_values', [])}`",
        f"- get_threshold_values=`{setup_info.get('get_threshold_values', [])}`",
        f"- target_thresholds=`{threshold_info.get('target_thresholds', [])}`",
        f"- primary_thresholds=`{threshold_info.get('primary_thresholds', [])}`",
        "",
        "## Setup lines",
        "",
    ]
    level_records = setup_info.get("level_records", [])
    if level_records:
        for item in level_records[:10]:
            lines.append(f"- `{item['line']}`")
    else:
        lines.append("- <none>")
    lines += [
        "",
        "## Threshold Hits",
        "",
    ]
    threshold_hits = threshold_info.get("threshold_hits", [])
    if threshold_hits:
        for item in threshold_hits[:20]:
            lines.append(f"- `{item['keyword']}` -> `{item['threshold']}` | `{item['line']}`")
    else:
        lines.append("- <none>")
    return "\n".join(lines) + "\n"


def build_failure_reason(case_id: str, metrics: dict) -> str:
    if case_id == "美的空调_1":
        tone_set = set(metrics["tone_ids"])
        if 290 in tone_set and 406 not in tone_set:
            return "断网硬重启后仍走在线欢迎播报链路，观测到 102/290，未出现文档要求的离线提示 tone 406。"
        return "硬重启后未满足离线欢迎播报判定，欢迎/未联网提示链路不完整。"
    if case_id == "美的空调_5":
        tone_set = set(metrics["tone_ids"])
        if 406 in tone_set and 290 not in tone_set:
            return "联网硬重启后仍走离线欢迎播报链路，观测到离线提示 tone 406，未出现主人请吩咐 tone 290。"
        return "硬重启后未满足在线欢迎播报判定，欢迎/主人请吩咐链路不完整。"
    if case_id == "美的空调_30":
        return (
            "自然对话链路只识别到部分命令，"
            f"当前识别关键词为 {metrics['recognized_command_keywords']}，"
            "未同时命中“关闭空调”和“打开空调”。"
        )
    if case_id == "美的空调_32":
        if metrics["unique_command_keyword_count"] == 0:
            return "首条命令未形成稳定识别，尚未进入有效的播报打断验证。"
        return (
            "首条命令已识别，但第二次唤醒没有在播报窗口内形成稳定打断，"
            f"wake_during_playback_count={metrics['wake_during_playback_count']}。"
        )
    if case_id == "美的空调_51":
        if metrics["ap_tts_fail_ids"]:
            ids = ",".join(str(item) for item in metrics["ap_tts_fail_ids"])
            return f"WB01 已触发离线 TTS 回调，但 AP 侧报 tts {ids} can't play，当前固件资源缺失或示例 ID 无效。"
        return "串口命令已发送，但未观测到完整的播报开始/结束标记。"
    if case_id == "美的空调_585":
        if metrics.get("ap_ai_disconnected_count", 0) <= 0:
            return "断网窗口内未在 AP 日志观测到 AI disconnected / wifiLink_update:disconnect close。"
        return "AP 已观测到 AI 断连，但 WB01 未出现 class ai state 4，对应 ai,4 上报链路不完整。"
    if case_id == "美的空调_21":
        if metrics.get("recognized_command_keywords"):
            return (
                "连续在线唤醒后虽然识别到了云端 ASR 文本 do，但同时命中了本地命令关键词 "
                f"{metrics['recognized_command_keywords']}，不符合 nlu 为空的纯兜底播报预期。"
            )
        if "do" not in metrics.get("ap_online_asr_texts", []):
            return "连续在线唤醒后未稳定识别出云端 ASR 文本 do，当前更像识别链路或语料映射问题。"
        return "已识别出在线 ASR 文本 do，但云端兜底 TTS 次数不足，未达到文档要求的连续兜底播报。"
    if case_id == "美的空调_44":
        return (
            "在线 1000 次纯唤醒压测未满足“每次唤醒都稳定播报”的判定阈值，"
            f"当前 CP/AP/WB 在线唤醒计数为 {metrics['cp_wake_count']}/{metrics['ap_wake_count']}/{metrics['wb_online_wake_count']}，"
            f"云端播报指令计数为 {metrics.get('ap_instruction_broadcast_count', 0)}。"
        )
    if case_id == "美的空调_45":
        return (
            "在线 1000 次唤醒+识别压测在识别/播报连续性上未达标，"
            f"当前 CP wake/cmd 为 {metrics['cp_wake_count']}/{metrics['cp_command_count']}，"
            f"关键词为 {metrics['recognized_command_keywords']}，"
            f"云端播报指令计数为 {metrics.get('ap_instruction_broadcast_count', 0)}，"
            f"player reset 次数为 {metrics['interrupt_reset_count']}。"
        )
    if case_id == "美的空调_704":
        return "开启方言后 one-shot 仍识别出完整“打开空调”在线 ASR，未复现文档要求的降级/截断结果。"
    if case_id == "美的空调_705":
        return "关闭方言恢复普通话后，one-shot 未稳定识别出纯命令词“打开空调”，或在线 ASR 仍夹带唤醒词。"
    return "未满足该 doc 用例的自动判定阈值。"


def evaluate_case_with_rules(case, metrics: dict, rules: dict) -> dict:
    checks = []

    def add_check(name: str, actual: Union[int, bool, List[int]], expected, passed: bool) -> None:
        checks.append({"name": name, "actual": actual, "expected": expected, "passed": passed})

    if "min_cp_wake" in rules:
        add_check("cp_wake_count", metrics["cp_wake_count"], f">={rules['min_cp_wake']}", metrics["cp_wake_count"] >= rules["min_cp_wake"])
    if "max_cp_wake" in rules:
        add_check("cp_wake_count_max", metrics["cp_wake_count"], f"<={rules['max_cp_wake']}", metrics["cp_wake_count"] <= rules["max_cp_wake"])
    if "min_ap_wake" in rules:
        add_check("ap_wake_count", metrics["ap_wake_count"], f">={rules['min_ap_wake']}", metrics["ap_wake_count"] >= rules["min_ap_wake"])
    if "max_ap_wake" in rules:
        add_check("ap_wake_count_max", metrics["ap_wake_count"], f"<={rules['max_ap_wake']}", metrics["ap_wake_count"] <= rules["max_ap_wake"])
    if "min_wb_wake" in rules:
        add_check("wb_wake_count", metrics["wb_wake_count"], f">={rules['min_wb_wake']}", metrics["wb_wake_count"] >= rules["min_wb_wake"])
    if "max_wb_wake" in rules:
        add_check("wb_wake_count_max", metrics["wb_wake_count"], f"<={rules['max_wb_wake']}", metrics["wb_wake_count"] <= rules["max_wb_wake"])
    if "min_wb_online_wake" in rules:
        add_check(
            "wb_online_wake_count",
            metrics["wb_online_wake_count"],
            f">={rules['min_wb_online_wake']}",
            metrics["wb_online_wake_count"] >= rules["min_wb_online_wake"],
        )
    if "max_wb_online_wake" in rules:
        add_check(
            "wb_online_wake_count_max",
            metrics["wb_online_wake_count"],
            f"<={rules['max_wb_online_wake']}",
            metrics["wb_online_wake_count"] <= rules["max_wb_online_wake"],
        )
    if "min_ap_asr" in rules:
        add_check("ap_asr_count", metrics["ap_asr_count"], f">={rules['min_ap_asr']}", metrics["ap_asr_count"] >= rules["min_ap_asr"])
    if "max_ap_asr" in rules:
        add_check("ap_asr_count_max", metrics["ap_asr_count"], f"<={rules['max_ap_asr']}", metrics["ap_asr_count"] <= rules["max_ap_asr"])
    if "min_wb_asr" in rules:
        add_check("wb_asr_count", metrics["wb_asr_count"], f">={rules['min_wb_asr']}", metrics["wb_asr_count"] >= rules["min_wb_asr"])
    if "max_wb_asr" in rules:
        add_check("wb_asr_count_max", metrics["wb_asr_count"], f"<={rules['max_wb_asr']}", metrics["wb_asr_count"] <= rules["max_wb_asr"])
    if "min_asr_total" in rules:
        add_check("asr_total", metrics["asr_total"], f">={rules['min_asr_total']}", metrics["asr_total"] >= rules["min_asr_total"])
    if "max_asr_total" in rules:
        add_check("asr_total_max", metrics["asr_total"], f"<={rules['max_asr_total']}", metrics["asr_total"] <= rules["max_asr_total"])
    if "min_unique_command_keywords" in rules:
        add_check(
            "unique_command_keyword_count",
            metrics["unique_command_keyword_count"],
            f">={rules['min_unique_command_keywords']}",
            metrics["unique_command_keyword_count"] >= rules["min_unique_command_keywords"],
        )
    if "max_unique_command_keywords" in rules:
        add_check(
            "unique_command_keyword_count_max",
            metrics["unique_command_keyword_count"],
            f"<={rules['max_unique_command_keywords']}",
            metrics["unique_command_keyword_count"] <= rules["max_unique_command_keywords"],
        )
    if "required_command_keywords" in rules:
        actual = metrics["recognized_command_keywords"]
        required = [normalize_keyword(item) for item in rules["required_command_keywords"]]
        add_check("required_command_keywords", actual, required, set(required).issubset(set(actual)))
    if "min_ap_online_asr" in rules:
        add_check(
            "ap_online_asr_count",
            len(metrics["ap_online_asr_texts"]),
            f">={rules['min_ap_online_asr']}",
            len(metrics["ap_online_asr_texts"]) >= rules["min_ap_online_asr"],
        )
    if "max_ap_online_asr" in rules:
        add_check(
            "ap_online_asr_count_max",
            len(metrics["ap_online_asr_texts"]),
            f"<={rules['max_ap_online_asr']}",
            len(metrics["ap_online_asr_texts"]) <= rules["max_ap_online_asr"],
        )
    if "required_online_asr_texts" in rules:
        actual = [normalize_online_asr_text(item) for item in metrics["ap_online_asr_texts"]]
        required = [normalize_online_asr_text(item) for item in rules["required_online_asr_texts"]]
        add_check("required_online_asr_texts", actual, required, set(required).issubset(set(actual)))
    if "min_wb_playback_start" in rules:
        add_check("wb_playback_start_count", metrics["wb_playback_start_count"], f">={rules['min_wb_playback_start']}", metrics["wb_playback_start_count"] >= rules["min_wb_playback_start"])
    if "max_wb_playback_start" in rules:
        add_check(
            "wb_playback_start_count_max",
            metrics["wb_playback_start_count"],
            f"<={rules['max_wb_playback_start']}",
            metrics["wb_playback_start_count"] <= rules["max_wb_playback_start"],
        )
    if "min_wb_playback_end" in rules:
        add_check("wb_playback_end_count", metrics["wb_playback_end_count"], f">={rules['min_wb_playback_end']}", metrics["wb_playback_end_count"] >= rules["min_wb_playback_end"])
    if "max_wb_playback_end" in rules:
        add_check(
            "wb_playback_end_count_max",
            metrics["wb_playback_end_count"],
            f"<={rules['max_wb_playback_end']}",
            metrics["wb_playback_end_count"] <= rules["max_wb_playback_end"],
        )
    if "min_wb_tts_callback" in rules:
        add_check("wb_tts_callback_ids", metrics["wb_tts_callback_ids"], f">={rules['min_wb_tts_callback']} callback(s)", len(metrics["wb_tts_callback_ids"]) >= rules["min_wb_tts_callback"])
    if "min_ap_cloud_tts_play" in rules:
        add_check(
            "ap_cloud_tts_play_count",
            metrics["ap_cloud_tts_play_count"],
            f">={rules['min_ap_cloud_tts_play']}",
            metrics["ap_cloud_tts_play_count"] >= rules["min_ap_cloud_tts_play"],
        )
    if "min_ap_cloud_tts_start" in rules:
        add_check(
            "ap_cloud_tts_start_count",
            metrics["ap_cloud_tts_start_count"],
            f">={rules['min_ap_cloud_tts_start']}",
            metrics["ap_cloud_tts_start_count"] >= rules["min_ap_cloud_tts_start"],
        )
    if "min_ap_cloud_tts_stop" in rules:
        add_check(
            "ap_cloud_tts_stop_count",
            metrics["ap_cloud_tts_stop_count"],
            f">={rules['min_ap_cloud_tts_stop']}",
            metrics["ap_cloud_tts_stop_count"] >= rules["min_ap_cloud_tts_stop"],
        )
    if "min_ap_instruction_broadcast" in rules:
        add_check(
            "ap_instruction_broadcast_count",
            metrics["ap_instruction_broadcast_count"],
            f">={rules['min_ap_instruction_broadcast']}",
            metrics["ap_instruction_broadcast_count"] >= rules["min_ap_instruction_broadcast"],
        )
    if "max_ap_cloud_tts_play" in rules:
        add_check(
            "ap_cloud_tts_play_count_max",
            metrics["ap_cloud_tts_play_count"],
            f"<={rules['max_ap_cloud_tts_play']}",
            metrics["ap_cloud_tts_play_count"] <= rules["max_ap_cloud_tts_play"],
        )
    if "max_ap_cloud_tts_start" in rules:
        add_check(
            "ap_cloud_tts_start_count_max",
            metrics["ap_cloud_tts_start_count"],
            f"<={rules['max_ap_cloud_tts_start']}",
            metrics["ap_cloud_tts_start_count"] <= rules["max_ap_cloud_tts_start"],
        )
    if "max_ap_cloud_tts_stop" in rules:
        add_check(
            "ap_cloud_tts_stop_count_max",
            metrics["ap_cloud_tts_stop_count"],
            f"<={rules['max_ap_cloud_tts_stop']}",
            metrics["ap_cloud_tts_stop_count"] <= rules["max_ap_cloud_tts_stop"],
        )
    if "min_ap_ignore_broadcast" in rules:
        add_check(
            "ap_ignore_broadcast_count",
            metrics["ap_ignore_broadcast_count"],
            f">={rules['min_ap_ignore_broadcast']}",
            metrics["ap_ignore_broadcast_count"] >= rules["min_ap_ignore_broadcast"],
        )
    if "max_ap_ignore_broadcast" in rules:
        add_check(
            "ap_ignore_broadcast_count_max",
            metrics["ap_ignore_broadcast_count"],
            f"<={rules['max_ap_ignore_broadcast']}",
            metrics["ap_ignore_broadcast_count"] <= rules["max_ap_ignore_broadcast"],
        )
    if case.case_id == "美的空调_51":
        add_check("command_echo", bool(metrics["command_lines"]), ">=1 command line", bool(metrics["command_lines"]))
    if "require_wake_during_playback" in rules:
        add_check(
            "wake_during_playback_count",
            metrics["wake_during_playback_count"],
            ">=1",
            metrics["wake_during_playback_count"] >= 1,
        )
    if "min_interrupt_reset_count" in rules:
        add_check(
            "interrupt_reset_count",
            metrics["interrupt_reset_count"],
            f">={rules['min_interrupt_reset_count']}",
            metrics["interrupt_reset_count"] >= rules["min_interrupt_reset_count"],
        )
    if "max_interrupt_reset_count" in rules:
        add_check(
            "interrupt_reset_count_max",
            metrics["interrupt_reset_count"],
            f"<={rules['max_interrupt_reset_count']}",
            metrics["interrupt_reset_count"] <= rules["max_interrupt_reset_count"],
        )
    if "required_tones" in rules:
        tone_set = set(metrics["tone_ids"])
        required = set(rules["required_tones"])
        add_check("required_tones", sorted(tone_set), sorted(required), required.issubset(tone_set))
    if "forbidden_tones" in rules:
        tone_set = set(metrics["tone_ids"])
        forbidden = set(rules["forbidden_tones"])
        add_check("forbidden_tones", sorted(tone_set), sorted(forbidden), not tone_set.intersection(forbidden))

    all_passed = all(item["passed"] for item in checks)
    result = "PASS" if all_passed else "FAIL"
    reason = rules["notes"] if all_passed else build_failure_reason(case.case_id, metrics)
    return {
        "result": result,
        "confidence": rules.get("confidence", "medium"),
        "reason": reason,
        "checks": checks,
    }


def evaluate_case(case, metrics: dict) -> dict:
    return evaluate_case_with_rules(case, metrics, SUPPORTED_DOC_CASES[case.case_id])


def read_clean_logs_from_execution(execution_dir: Path) -> Dict[str, List[str]]:
    clean_logs: Dict[str, List[str]] = {}
    for port in configured_log_ports():
        path = execution_dir / "window_logs" / f"{port}.clean.log"
        if not path.exists():
            clean_logs[port] = []
            continue
        clean_logs[port] = read_serial_log_lines(path, errors="strict")
    add_canonical_log_aliases(clean_logs)
    return clean_logs


def extract_algo_threshold_hits(lines: List[str]) -> List[dict]:
    hits: List[dict] = []
    seen = set()
    for line in lines:
        match = ALGO_THRESHOLD_RE.search(line)
        if not match:
            continue
        threshold = int(match.group(1))
        keyword = normalize_keyword(match.group(2))
        key = (threshold, keyword)
        if key in seen:
            continue
        seen.add(key)
        hits.append({"threshold": threshold, "keyword": keyword, "line": line})
    return hits


def extract_threshold_setup_info(lines: List[str]) -> dict:
    level_records: List[dict] = []
    level_values: List[int] = []
    get_threshold_values: List[int] = []
    for line in lines:
        level_match = SET_WAKE_THRESHOLD_LEVEL_RE.search(line)
        if level_match:
            value = int(level_match.group("value"))
            level_records.append({"label": level_match.group("label"), "value": value, "line": line})
            append_unique(level_values, value)
        get_match = GET_WAKE_THRESHOLD_RE.search(line)
        if get_match:
            append_unique(get_threshold_values, int(get_match.group("value")))
    return {
        "level_records": level_records,
        "level_values": level_values,
        "get_threshold_values": get_threshold_values,
    }


def evaluate_threshold_case(
    case,
    rules: dict,
    metrics: dict,
    clean_logs: Dict[str, List[str]],
    setup_records: Optional[List[dict]] = None,
) -> Tuple[dict, dict]:
    checks = []

    def add_check(name: str, actual, expected, passed: bool) -> None:
        checks.append({"name": name, "actual": actual, "expected": expected, "passed": passed})

    threshold_hits = extract_algo_threshold_hits(clean_logs.get("COM14", []))
    expected_keyword = normalize_keyword(str(rules["expected_wakeup_keyword"]))
    target_thresholds = [item["threshold"] for item in threshold_hits if item["keyword"] == expected_keyword]
    recognition_threshold = int(rules.get("expected_recognition_threshold", -308))
    threshold_request_value = int(
        rules.get("threshold_request", rules.get("pre_threshold_low", rules.get("pre_threshold_high", 0)))
    )
    primary_thresholds = [item for item in target_thresholds if item != recognition_threshold]

    setup_ap_lines: List[str] = []
    threshold_records = [item for item in (setup_records or []) if item.get("action") == "cloud_wakeup_threshold"]
    threshold_setup_record = next(
        (item for item in reversed(threshold_records) if int(item.get("threshold", -9999)) == threshold_request_value),
        None,
    )
    if threshold_setup_record is None and threshold_records:
        threshold_setup_record = threshold_records[-1]
    if threshold_setup_record and threshold_setup_record.get("artifact_dir"):
        setup_path = Path(str(threshold_setup_record["artifact_dir"])) / "COM14_window.log"
        if setup_path.exists():
            setup_ap_lines = read_serial_log_lines(setup_path, errors="ignore")
    setup_info = extract_threshold_setup_info(setup_ap_lines)

    add_check("cp_wake_count", metrics["cp_wake_count"], f">={rules['min_cp_wake']}", metrics["cp_wake_count"] >= rules["min_cp_wake"])
    add_check("ap_wake_count", metrics["ap_wake_count"], f">={rules['min_ap_wake']}", metrics["ap_wake_count"] >= rules["min_ap_wake"])
    add_check(
        "threshold_setup_trace",
        {
            "level_records": setup_info["level_records"],
            "get_threshold_values": setup_info["get_threshold_values"],
        },
        "level record or threshold readback",
        bool(setup_info["level_records"]) or bool(setup_info["get_threshold_values"]),
    )
    add_check(
        "current_threshold_after_set",
        setup_info["get_threshold_values"],
        threshold_request_value,
        threshold_request_value in setup_info["get_threshold_values"],
    )
    add_check(
        "observed_wakeup_threshold_present",
        primary_thresholds,
        ">=1 non-recognition threshold",
        bool(primary_thresholds),
    )
    add_check(
        "observed_recognition_threshold",
        target_thresholds,
        recognition_threshold,
        recognition_threshold in target_thresholds,
    )
    add_check(
        "observed_wakeup_threshold_diff_from_recognition",
        primary_thresholds,
        f"!= {recognition_threshold}",
        all(item != recognition_threshold for item in primary_thresholds),
    )

    all_passed = all(item["passed"] for item in checks)
    if all_passed:
        reason = rules["notes"]
    elif metrics["cp_wake_count"] == 0 and metrics["ap_wake_count"] == 0:
        reason = f"目标唤醒词 {expected_keyword} 未形成任何 wake 标记，阈值日志为空，当前更像设备/音频链路问题而不是判定逻辑问题。"
    else:
        reason = f"阈值日志未满足预期，keyword={expected_keyword}，当前命中的阈值={target_thresholds}。"
    diagnosis = {
        "result": "PASS" if all_passed else "FAIL",
        "confidence": rules.get("confidence", "medium"),
        "reason": reason,
        "checks": checks,
    }
    return diagnosis, {
        "threshold_hits": threshold_hits,
        "target_thresholds": target_thresholds,
        "primary_thresholds": primary_thresholds,
        "setup_info": setup_info,
        "threshold_request_value": threshold_request_value,
    }


def build_fingerprint(case, metrics: dict, diagnosis: dict) -> dict:
    return {
        "case_id": case.case_id,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "cp_wake_count": metrics["cp_wake_count"],
        "cp_command_count": metrics["cp_command_count"],
        "ap_wake_count": metrics["ap_wake_count"],
        "wb_wake_count": metrics["wb_wake_count"],
        "ap_asr_count": metrics["ap_asr_count"],
        "wb_asr_count": metrics["wb_asr_count"],
        "wb_playback_start_count": metrics["wb_playback_start_count"],
        "wb_playback_end_count": metrics["wb_playback_end_count"],
        "tone_ids": metrics["tone_ids"],
        "recognized_command_keywords": metrics["recognized_command_keywords"],
        "wb_tts_callback_ids": metrics["wb_tts_callback_ids"],
        "ap_tts_fail_ids": metrics["ap_tts_fail_ids"],
        "wake_during_playback_count": metrics["wake_during_playback_count"],
        "interrupt_reset_count": metrics["interrupt_reset_count"],
        "boot_marker_count": metrics["boot_marker_count"],
        "crash_marker_count": metrics["crash_marker_count"],
    }



def build_excerpt(case, diagnosis: dict, metrics: dict, tone_catalog: dict, clean_logs: Dict[str, List[str]]) -> str:
    lines = [
        f"# {case.case_id}",
        "",
        f"- Name: `{case.name}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Confidence: `{diagnosis['confidence']}`",
        f"- Reason: {diagnosis['reason']}",
        "",
        "## Checks",
        "",
    ]
    for item in diagnosis["checks"]:
        lines.append(f"- `{item['name']}` -> `{'PASS' if item['passed'] else 'MISS'}` | actual=`{item['actual']}` expected=`{item['expected']}`")
    lines += [
        "",
        "## Tone IDs",
        "",
    ]
    if metrics["tone_ids"]:
        for tone_id in metrics["tone_ids"]:
            lines.append(f"- `{tone_id}` | `{tone_catalog.get(tone_id, 'unknown')}`")
    else:
        lines.append("- <none>")
    lines += [
        "",
        "## Key lines",
        "",
    ]
    key_lines = []
    for port in ["COM12", "COM13", "COM14"]:
        for line in clean_logs.get(port, []):
            if any(
                token in line
                for token in [
                    "WAKE(",
                    "wakeup_callback",
                    "offline_wakeup",
                    "offline_asr_callbak",
                    "play next tone",
                    "local player status 2 PLAYING",
                    "local player status 6 PLAYBACK_COMPLETE",
                    "tts ",
                    "player reset by \"user\"",
                    "[COMMAND]",
                ]
            ):
                key_lines.append(line)
    for line in key_lines[:40]:
        lines.append(f"- `{line}`")
    if not key_lines:
        lines.append("- <none>")
    return "\n".join(lines) + "\n"


def execute_standard_audio_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> dict:
    state_dir = execution_dir / "state"
    logs_dir = execution_dir / "window_logs"
    audio_dir = execution_dir / "audio"
    logs_dir.mkdir(parents=True, exist_ok=True)

    before_state = snapshot("before", state_dir, session_dir)
    audio_file = None
    audio_manifest = None
    playback_segments: List[dict] = []
    sequence = build_audio_sequence(case)
    commands = shell_commands(case)
    interrupt_audio_file = None
    interrupt_audio_manifest = None
    if rules["runner_kind"] == "offline_interrupt_voice":
        first_sequence, interrupt_sequence = build_interrupt_sequences(case)
        if first_sequence:
            audio_file = audio_dir / f"{case.case_id}_segment1.wav"
            audio_manifest = build_sequence(first_sequence, audio_file)
        if interrupt_sequence:
            interrupt_audio_file = audio_dir / f"{case.case_id}_segment2.wav"
            interrupt_audio_manifest = build_sequence(interrupt_sequence, interrupt_audio_file)
    elif sequence:
        audio_file = audio_dir / f"{case.case_id}.wav"
        audio_manifest = build_sequence(sequence, audio_file)

    start_dt = datetime.now()
    playback_result = {"returncode": 0, "mode": rules["runner_kind"], "segments": playback_segments}

    for command in commands:
        queue_command(route_command(command), command, session_dir=session_dir)
        time.sleep(0.8)

    if rules["runner_kind"] == "offline_interrupt_voice":
        overall_returncode = 0
        first_asr_line = None
        interrupt_trigger_line = None
        if audio_file is not None:
            completed = run_playback(audio_file, device_key, execution_dir, log_prefix="segment1_play")
            overall_returncode = max(overall_returncode, completed.returncode)
            playback_segments.append(
                {
                    "name": "initial_command_audio",
                    "audio_file": str(audio_file),
                    "manifest": audio_manifest,
                    "returncode": completed.returncode,
                }
            )
            first_asr_line = wait_for_regex_line("COM13", start_dt, (WB_ASR_RE,), 8.0, session_dir)
            if first_asr_line is None:
                first_asr_line = wait_for_regex_line("COM14", start_dt, (AP_ASR_RE,), 8.0, session_dir)
            playback_anchor_dt = parse_prefixed_timestamp(first_asr_line) if first_asr_line else start_dt
            if playback_anchor_dt is None:
                playback_anchor_dt = start_dt
            interrupt_trigger_line = first_asr_line
            if interrupt_trigger_line is None:
                interrupt_trigger_line = wait_for_regex_line("COM13", playback_anchor_dt, (WB_PLAY_START_RE,), 8.0, session_dir)

        if interrupt_audio_file is not None:
            if interrupt_trigger_line is not None:
                time.sleep(0.05)
            completed = run_low_latency_playback(
                interrupt_audio_file,
                device_key,
                execution_dir,
                log_prefix="segment2_play",
            )
            overall_returncode = max(overall_returncode, completed.returncode)
            playback_segments.append(
                {
                    "name": "interrupt_wakeup_audio",
                    "audio_file": str(interrupt_audio_file),
                    "manifest": interrupt_audio_manifest,
                    "returncode": completed.returncode,
                    "trigger_line": interrupt_trigger_line or "",
                    "anchor_asr_line": first_asr_line or "",
                }
            )

        playback_result = {
            "returncode": overall_returncode,
            "mode": rules["runner_kind"],
            "segments": playback_segments,
        }
    elif audio_file is not None:
        completed = run_playback(audio_file, device_key, execution_dir, log_prefix="main_play")
        playback_segments.append(
            {
                "name": "main_audio",
                "audio_file": str(audio_file),
                "manifest": audio_manifest,
                "returncode": completed.returncode,
            }
        )
        playback_result = {"returncode": completed.returncode, "mode": rules["runner_kind"], "segments": playback_segments}
    observe_after_ms = int(rules.get("observe_after_ms", 10000))
    time.sleep(observe_after_ms / 1000.0)
    end_dt = datetime.now()

    after_state = snapshot("after", state_dir, session_dir)
    state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")

    raw_logs: Dict[str, List[str]] = {}
    for port in configured_log_ports():
        lines = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
        raw_logs[port] = lines
        (logs_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    add_canonical_log_aliases(raw_logs)
    clean_logs = sanitize_logs(raw_logs)
    add_canonical_log_aliases(clean_logs)
    for port, lines in clean_logs.items():
        (logs_dir / f"{port}.clean.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    window_summary = summarize_window(clean_logs)
    metrics = collect_metrics(clean_logs, window_summary)
    diagnosis = evaluate_case_with_rules(case, metrics, adapt_rules_for_current_project(SUPPORTED_DOC_CASES[case.case_id]))
    if playback_result["returncode"] != 0:
        diagnosis = {
            "result": "BLOCKED",
            "confidence": diagnosis["confidence"],
            "reason": "播放音频阶段失败，未进入日志判定。",
            "checks": diagnosis["checks"],
        }

    judge_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "reason": diagnosis["reason"],
        "checks": diagnosis["checks"],
        "metrics": metrics,
        "tone_names": {str(tone_id): tone_catalog.get(tone_id, "unknown") for tone_id in metrics["tone_ids"]},
    }
    fingerprint = build_fingerprint(case, metrics, diagnosis)
    excerpt = build_excerpt(case, diagnosis, metrics, tone_catalog, clean_logs)
    return {
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
        "playback": {
            "audio_file": str(audio_file) if audio_file else "",
            "manifest": audio_manifest,
            "returncode": playback_result["returncode"],
            "device_key": str(device_key or "").strip(),
            "playback_device": playback_device_label(device_key),
            "commands": commands,
            "segments": playback_result.get("segments", []),
        },
        "states": {
            "before": str(before_state),
            "after": str(after_state),
            "diff": str(state_diff),
        },
        "window_summary": window_summary,
        "metrics": metrics,
        "diagnosis": diagnosis,
        "judge_payload": judge_payload,
        "fingerprint": fingerprint,
        "failure_excerpt": excerpt,
    }


def _replace_excerpt_reason(excerpt: str, reason: str) -> str:
    if not excerpt:
        return excerpt
    lines = excerpt.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("- Reason: "):
            lines[index] = f"- Reason: {reason}"
            break
    return "\n".join(lines)


def _refine_standard_audio_failure_reason(payload: dict) -> None:
    diagnosis = payload["diagnosis"]
    if diagnosis.get("result") != "FAIL":
        return

    metrics = payload["metrics"]
    setup_records = list(payload.get("setup") or [])
    wakeup_setup = next(
        (item for item in setup_records if item.get("action") == "cloud_wakeup_word"),
        None,
    )
    power_cycle_setup = next(
        (item for item in setup_records if str(item.get("action", "")).startswith("power_cycle_")),
        None,
    )

    refined_reason = ""
    if wakeup_setup and wakeup_setup.get("device_rejected") and metrics["cp_wake_count"] == 0 and metrics["ap_wake_count"] == 0:
        refined_reason = (
            f"AP setup stage explicitly rejected wakeup word {wakeup_setup.get('wakeup_word')} "
            "(log contains invalid wakeup word); no later wake/threshold evidence was produced, so this points to a device/config capability issue."
        )
        if "failure_excerpt" in payload and "invalid wakeup word" not in payload["failure_excerpt"]:
            payload["failure_excerpt"] += (
                "\n## Setup Hint\n\n"
                f"- AP setup excerpt reported `invalid wakeup word` for `{wakeup_setup.get('wakeup_word')}`.\n"
            )
    elif wakeup_setup and wakeup_setup.get("success") and not wakeup_setup.get("device_rejected"):
        target_word = str(wakeup_setup.get("wakeup_word", "")).strip() or "目标唤醒词"
        cp_wake_count = int(metrics.get("cp_wake_count", 0) or 0)
        ap_wake_count = int(metrics.get("ap_wake_count", 0) or 0)
        wb_playback_end_count = int(metrics.get("wb_playback_end_count", 0) or 0)
        ap_broadcast_count = int(metrics.get("ap_instruction_broadcast_count", 0) or 0)
        if cp_wake_count == 0 and ap_wake_count == 0:
            if power_cycle_setup and power_cycle_setup.get("success"):
                refined_reason = (
                    f"云端已返回唤醒词“{target_word}”设置成功，且 WB01 硬重启证据成立；"
                    "但重启后播放探测音频仍未看到 COM12/COM14 形成 wake 证据，"
                    "说明该唤醒词未在设备侧保留生效。"
                )
            else:
                refined_reason = (
                    f"云端已返回唤醒词“{target_word}”设置成功，但播放探测音频后 COM12/COM14 均未形成 wake 证据，"
                    "说明设备侧未真正切换到该唤醒词，或切换后识别链路未生效。"
                )
        elif wb_playback_end_count == 0 and ap_broadcast_count == 0:
            refined_reason = (
                f"目标唤醒词“{target_word}”已形成 CP/AP 唤醒证据，但后续未看到 AP 指令播报或 WB 播报完成标记，"
                "说明响应/播报链路未跑通。"
            )

    if refined_reason:
        diagnosis["reason"] = refined_reason
        payload["judge_payload"]["reason"] = refined_reason
        if "failure_excerpt" in payload:
            payload["failure_excerpt"] = _replace_excerpt_reason(payload["failure_excerpt"], refined_reason)


def persist_standard_audio_case(case, execution_dir: Path, payload: dict) -> Path:
    diagnosis = payload["diagnosis"]
    metrics = payload["metrics"]
    if diagnosis["result"] == "FAIL" and payload.get("setup"):
        _refine_standard_audio_failure_reason(payload)

    (execution_dir / "judge.json").write_text(json.dumps(payload["judge_payload"], ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "fingerprint.json").write_text(json.dumps(payload["fingerprint"], ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "failure_excerpt.md").write_text(payload["failure_excerpt"], encoding="utf-8")

    result_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "execution_dir": str(execution_dir),
        "started_at": payload["started_at"],
        "ended_at": payload["ended_at"],
        "playback": payload["playback"],
        "states": payload["states"],
        "metrics": metrics,
        "window_summary": payload["window_summary"],
        "diagnosis": diagnosis,
        "artifacts": {
            "judge": str(execution_dir / "judge.json"),
            "fingerprint": str(execution_dir / "fingerprint.json"),
            "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
        },
    }
    if "setup" in payload:
        result_payload["setup"] = payload["setup"]
    if "recovery" in payload:
        result_payload["recovery"] = payload["recovery"]
    if "setup_error" in payload:
        result_payload["setup_error"] = payload["setup_error"]

    result_path = execution_dir / "doc_case_result.json"
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_lines = [
        f"# {case.case_id}",
        "",
        f"- Name: `{case.name}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Confidence: `{diagnosis['confidence']}`",
        f"- Reason: {diagnosis['reason']}",
        f"- Commands: `{payload['playback']['commands']}`",
        f"- Tone IDs: `{metrics['tone_ids']}`",
        f"- Recognized keywords: `{metrics['recognized_command_keywords']}`",
        f"- WB playback start/end: `{metrics['wb_playback_start_count']}` / `{metrics['wb_playback_end_count']}`",
        f"- CP wake during WB playback: `{metrics['wake_during_playback_count']}`",
        f"- WB TTS callback ids: `{metrics['wb_tts_callback_ids']}`",
        f"- AP TTS fail ids: `{metrics['ap_tts_fail_ids']}`",
        "",
        "## Checks",
        "",
    ]
    for item in diagnosis["checks"]:
        summary_lines.append(f"- `{item['name']}` -> `{'PASS' if item['passed'] else 'MISS'}` | actual=`{item['actual']}` expected=`{item['expected']}`")
    if "setup" in payload:
        summary_lines += [
            "",
            "## Setup",
            "",
        ]
        for item in payload["setup"]:
            summary_lines.append(
                f"- `{item.get('action', item.get('label', 'setup'))}` -> `{'PASS' if item.get('success', False) else 'MISS'}` | artifact=`{item.get('artifact_dir', '')}`"
            )
    if "recovery" in payload:
        summary_lines += [
            "",
            "## Recovery",
            "",
        ]
        for item in payload["recovery"]:
            summary_lines.append(
                f"- `{item.get('action', item.get('label', 'recovery'))}` -> `{'PASS' if item.get('success', False) else 'MISS'}` | artifact=`{item.get('artifact_dir', '')}`"
            )
    (execution_dir / "doc_case_summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    return result_path


def build_blocked_case_payload(case, rules: dict, reason: str, tone_catalog: dict) -> dict:
    diagnosis = {
        "result": "BLOCKED",
        "confidence": rules.get("confidence", "medium"),
        "reason": reason,
        "checks": [],
    }
    metrics = empty_metrics()
    return {
        "started_at": datetime.now().isoformat(timespec="milliseconds"),
        "ended_at": datetime.now().isoformat(timespec="milliseconds"),
        "playback": {
            "audio_file": "",
            "manifest": None,
            "returncode": None,
            "commands": shell_commands(case),
            "segments": [],
        },
        "states": {
            "before": "",
            "after": "",
            "diff": "",
        },
        "window_summary": {"tones": []},
        "metrics": metrics,
        "diagnosis": diagnosis,
        "judge_payload": {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "checks": diagnosis["checks"],
            "metrics": metrics,
            "tone_names": {},
        },
        "fingerprint": build_fingerprint(case, metrics, diagnosis),
        "failure_excerpt": build_excerpt(case, diagnosis, metrics, tone_catalog, {"COM12": [], "COM13": [], "COM14": []}),
    }


def prepare_local_hotspot_attachment(execution_dir: Path, session_dir: Path, device_mac: str, wait_s: float = 60.0) -> dict:
    artifact_dir = execution_dir / "setup" / "01_prepare_local_hotspot"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    before_status = adapter_hotspot_status()
    startup_window = None

    if str(before_status.get("operational_state", "")).lower() != "on":
        startup_start = datetime.now()
        startup_result = adapter_hotspot_set(True)
        time.sleep(10.0)
        startup_end = datetime.now()
        startup_window = collect_network_window(session_dir, startup_start, startup_end, artifact_dir, "hotspot_start")
        before_status = adapter_hotspot_status()
        save_json(artifact_dir / "hotspot_start_result.json", startup_result)

    already_attached = (
        str(before_status.get("operational_state", "")).lower() == "on"
        and str(before_status.get("ssid", "")) == LOCAL_HOTSPOT_SSID
        and hotspot_has_device(before_status, device_mac)
    )
    if already_attached:
        summary = {
            "action": "prepare_local_hotspot",
            "artifact_dir": str(artifact_dir),
            "success": True,
            "already_attached": True,
            "before_status": summarize_hotspot_state(before_status, device_mac),
            "startup_window": startup_window,
        }
        save_json(artifact_dir / "summary.json", summary)
        return summary

    commands = [
        network_command_window("COM13", f"listen flash set string vir_ssid {LOCAL_HOTSPOT_SSID}", session_dir=session_dir),
        network_command_window("COM13", f"listen flash set string vir_pwd {LOCAL_HOTSPOT_PASSWORD}", session_dir=session_dir),
        network_command_window("COM13", "listen flash show", session_dir=session_dir, settle_s=2.5),
    ]
    for index, entry in enumerate(commands, 1):
        save_json(artifact_dir / f"command_{index:02d}.json", entry)

    reboot_start = datetime.now()
    queue_command("COM13", "reboot", session_dir=session_dir)
    time.sleep(wait_s)
    reboot_end = datetime.now()
    after_status = adapter_hotspot_status()
    reboot_window = collect_network_window(session_dir, reboot_start, reboot_end, artifact_dir, "after_reboot")
    success = (
        str(after_status.get("operational_state", "")).lower() == "on"
        and str(after_status.get("ssid", "")) == LOCAL_HOTSPOT_SSID
        and hotspot_has_device(after_status, device_mac)
        and network_window_indicates_online(reboot_window)
    )
    summary = {
        "action": "prepare_local_hotspot",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "already_attached": False,
        "before_status": summarize_hotspot_state(before_status, device_mac),
        "after_status": summarize_hotspot_state(after_status, device_mac),
        "commands": commands,
        "reboot_window": reboot_window,
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError("未能把设备挂回本机可控热点，无法执行热点断网用例。")
    return summary


def apply_cloud_full_duplex_setting(
    execution_dir: Path,
    session_dir: Path,
    *,
    enable: bool,
    timeout_seconds: int,
    phase_root: str,
    label: str,
    apply_wait_s: float = 12.0,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    wait_for_device_online(session_dir, artifact_dir / "00_wait_online")

    deviceinfo_capture = capture_cloud_deviceinfo(session_dir)
    deviceinfo = deviceinfo_capture["parsed"]
    (artifact_dir / "deviceinfo.log").write_text("\n".join(deviceinfo_capture["lines"]) + "\n", encoding="utf-8")
    save_json(artifact_dir / "deviceinfo.json", deviceinfo)

    start_dt = datetime.now()
    request = build_cloud_request(deviceinfo)
    onoroff = 1 if enable else 0
    response_v1 = request.fullDuplex_switch(onoroff=onoroff, timeOut=int(timeout_seconds))
    response_v2 = request.fullDuplex_switch_new(onoroff=onoroff, timeOut=int(timeout_seconds))
    time.sleep(apply_wait_s)
    end_dt = datetime.now()

    ap_window = read_lines_between("COM14", start_dt, end_dt, session_dir=session_dir)
    wb_window = read_lines_between("COM13", start_dt, end_dt, session_dir=session_dir)
    ap_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM14", CLOUD_FULL_DUPLEX_KEYWORDS)
    wb_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM13", CLOUD_FULL_DUPLEX_KEYWORDS)
    (artifact_dir / "COM14_window.log").write_text("\n".join(ap_window) + ("\n" if ap_window else ""), encoding="utf-8")
    (artifact_dir / "COM13_window.log").write_text("\n".join(wb_window) + ("\n" if wb_window else ""), encoding="utf-8")
    (artifact_dir / "COM14_excerpt.log").write_text("\n".join(ap_excerpt) + ("\n" if ap_excerpt else ""), encoding="utf-8")
    (artifact_dir / "COM13_excerpt.log").write_text("\n".join(wb_excerpt) + ("\n" if wb_excerpt else ""), encoding="utf-8")

    response_dict = {
        "v1": cloud_response_to_dict(response_v1),
        "v2": cloud_response_to_dict(response_v2),
    }
    save_json(artifact_dir / "response.json", response_dict)
    success = any(cloud_response_ok(item) for item in response_dict.values())
    summary = {
        "action": "cloud_full_duplex",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "enable": bool(enable),
        "timeout_seconds": int(timeout_seconds),
        "apply_wait_s": apply_wait_s,
        "deviceinfo": deviceinfo,
        "response": response_dict,
        "ap_excerpt_count": len(ap_excerpt),
        "wb_excerpt_count": len(wb_excerpt),
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError(f"APP/cloud 设置自然对话失败，enable={onoroff}, HTTP={response_dict}.")
    return summary


def apply_cloud_full_duplex(execution_dir: Path, session_dir: Path, timeout_seconds: int, apply_wait_s: float = 12.0) -> dict:
    return apply_cloud_full_duplex_setting(
        execution_dir,
        session_dir,
        enable=True,
        timeout_seconds=timeout_seconds,
        phase_root="setup",
        label=f"02_cloud_full_duplex_{timeout_seconds}s",
        apply_wait_s=apply_wait_s,
    )


def apply_cloud_mic_switch(
    execution_dir: Path,
    session_dir: Path,
    *,
    enable: bool,
    phase_root: str,
    label: str,
    apply_wait_s: float = 6.0,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    wait_for_device_online(session_dir, artifact_dir / "00_wait_online")

    deviceinfo_capture = capture_cloud_deviceinfo(session_dir)
    deviceinfo = deviceinfo_capture["parsed"]
    (artifact_dir / "deviceinfo.log").write_text("\n".join(deviceinfo_capture["lines"]) + "\n", encoding="utf-8")
    save_json(artifact_dir / "deviceinfo.json", deviceinfo)

    start_dt = datetime.now()
    request = build_cloud_request(deviceinfo)
    response = request.mic_switch(1 if enable else 0)
    time.sleep(apply_wait_s)
    end_dt = datetime.now()

    ap_window = read_lines_between("COM14", start_dt, end_dt, session_dir=session_dir)
    wb_window = read_lines_between("COM13", start_dt, end_dt, session_dir=session_dir)
    ap_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM14", CLOUD_MIC_KEYWORDS)
    wb_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM13", CLOUD_MIC_KEYWORDS)
    (artifact_dir / "COM14_window.log").write_text("\n".join(ap_window) + ("\n" if ap_window else ""), encoding="utf-8")
    (artifact_dir / "COM13_window.log").write_text("\n".join(wb_window) + ("\n" if wb_window else ""), encoding="utf-8")
    (artifact_dir / "COM14_excerpt.log").write_text("\n".join(ap_excerpt) + ("\n" if ap_excerpt else ""), encoding="utf-8")
    (artifact_dir / "COM13_excerpt.log").write_text("\n".join(wb_excerpt) + ("\n" if wb_excerpt else ""), encoding="utf-8")

    response_dict = cloud_response_to_dict(response)
    save_json(artifact_dir / "response.json", response_dict)
    success = cloud_response_ok(response_dict)
    device_rejected = any("invalid wakeup word" in line.lower() for line in ap_window + ap_excerpt)
    summary = {
        "action": "cloud_mic_switch",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "enable": bool(enable),
        "apply_wait_s": apply_wait_s,
        "deviceinfo": deviceinfo,
        "response": response_dict,
        "ap_excerpt_count": len(ap_excerpt),
        "wb_excerpt_count": len(wb_excerpt),
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError(f"APP/cloud 设置语音开关失败，HTTP={response_dict}.")
    return summary


def ensure_cloud_mic_on_baseline(
    execution_dir: Path,
    session_dir: Path,
    *,
    phase_root: str,
    label: str,
    apply_wait_s: float = 6.0,
    retry_wait_s: float = 2.0,
    max_attempts: int = 3,
) -> dict:
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        attempt_label = label if attempt == 1 else f"{label}_retry{attempt}"
        try:
            summary = apply_cloud_mic_switch(
                execution_dir,
                session_dir,
                enable=True,
                phase_root=phase_root,
                label=attempt_label,
                apply_wait_s=apply_wait_s,
            )
            summary["attempt"] = attempt
            return summary
        except Exception as exc:
            last_error = str(exc)
            if attempt >= max_attempts:
                break
            time.sleep(retry_wait_s)
    raise RuntimeError(f"无法恢复在线语音开关到开启态: {last_error}")


def apply_cloud_accent_switch(
    execution_dir: Path,
    session_dir: Path,
    *,
    accent_id: str,
    enable_accent: bool,
    mixed_res_enable: int,
    phase_root: str,
    label: str,
    apply_wait_s: float = 6.0,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    wait_for_device_online(session_dir, artifact_dir / "00_wait_online")

    deviceinfo_capture = capture_cloud_deviceinfo(session_dir)
    deviceinfo = deviceinfo_capture["parsed"]
    (artifact_dir / "deviceinfo.log").write_text("\n".join(deviceinfo_capture["lines"]) + "\n", encoding="utf-8")
    save_json(artifact_dir / "deviceinfo.json", deviceinfo)

    start_dt = datetime.now()
    request = build_cloud_request(deviceinfo)
    response = request.accent_switch(
        accentId=str(accent_id),
        enableAccent=1 if enable_accent else 0,
        mixedResEnable=int(mixed_res_enable),
    )
    time.sleep(apply_wait_s)
    end_dt = datetime.now()

    ap_window = read_lines_between("COM14", start_dt, end_dt, session_dir=session_dir)
    wb_window = read_lines_between("COM13", start_dt, end_dt, session_dir=session_dir)
    ap_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM14", CLOUD_ACCENT_KEYWORDS)
    wb_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM13", CLOUD_ACCENT_KEYWORDS)
    (artifact_dir / "COM14_window.log").write_text("\n".join(ap_window) + ("\n" if ap_window else ""), encoding="utf-8")
    (artifact_dir / "COM13_window.log").write_text("\n".join(wb_window) + ("\n" if wb_window else ""), encoding="utf-8")
    (artifact_dir / "COM14_excerpt.log").write_text("\n".join(ap_excerpt) + ("\n" if ap_excerpt else ""), encoding="utf-8")
    (artifact_dir / "COM13_excerpt.log").write_text("\n".join(wb_excerpt) + ("\n" if wb_excerpt else ""), encoding="utf-8")

    response_dict = cloud_response_to_dict(response)
    save_json(artifact_dir / "response.json", response_dict)
    success = cloud_response_ok(response_dict)
    summary = {
        "action": "cloud_accent_switch",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "accent_id": str(accent_id),
        "enable_accent": bool(enable_accent),
        "mixed_res_enable": int(mixed_res_enable),
        "apply_wait_s": apply_wait_s,
        "deviceinfo": deviceinfo,
        "response": response_dict,
        "ap_excerpt_count": len(ap_excerpt),
        "wb_excerpt_count": len(wb_excerpt),
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError(
            f"APP/cloud 设置方言失败，accent_id={accent_id}, enable={int(enable_accent)}, HTTP={response_dict}."
        )
    return summary


def cycle_case_power_target(
    execution_dir: Path,
    session_dir: Path,
    *,
    target: str,
    phase_root: str,
    label: str,
    off_wait_s: float = 2.0,
    observe_s: float = 20.0,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if target != "wb01":
        raise ValueError(f"unsupported power target: {target}")

    start_dt = datetime.now()
    adapter_result = run_adapter_action_capture(
        adapter_id="control.serial",
        action="cycle_target_window",
        params={
            "target": target,
            "off_wait": str(off_wait_s),
            "observe": str(observe_s),
            "output_dir": str(artifact_dir),
        },
        timeout_s=int(off_wait_s + observe_s + 60),
        execute=True,
        allow_side_effects=True,
    )
    end_dt = datetime.now()

    helper_summary_path = artifact_dir / "summary.json"
    helper_summary = json.loads(helper_summary_path.read_text(encoding="utf-8-sig")) if helper_summary_path.exists() else {}
    marker_summary = helper_summary.get("markers", {})
    inference = helper_summary.get("inference", {})
    success = bool(inference.get("hard_power_cycle_likely")) and bool(inference.get("wb01_booted")) and bool(inference.get("ap_booted"))
    summary = {
        "action": f"power_cycle_{target}",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "target": target,
        "off_wait_s": off_wait_s,
        "observe_s": observe_s,
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
        "actions": helper_summary.get("actions", []),
        "adapter_executor": adapter_result.to_dict(),
        "markers": marker_summary,
        "inference": inference,
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError(f"{target} 掉电上电未形成预期硬重启证据。")
    return summary


def apply_cloud_wakeup_word(
    execution_dir: Path,
    session_dir: Path,
    wakeup_word: str,
    *,
    phase_root: str,
    label: str,
    apply_wait_s: float = 12.0,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    wait_for_device_online(session_dir, artifact_dir / "00_wait_online")

    deviceinfo_before_capture = capture_cloud_deviceinfo(session_dir)
    deviceinfo_before = deviceinfo_before_capture["parsed"]
    (artifact_dir / "deviceinfo_before.log").write_text("\n".join(deviceinfo_before_capture["lines"]) + "\n", encoding="utf-8")
    save_json(artifact_dir / "deviceinfo_before.json", deviceinfo_before)

    start_dt = datetime.now()
    request = build_cloud_request(deviceinfo_before)
    response = request.wakeup_switch(str(wakeup_word))
    time.sleep(apply_wait_s)
    end_dt = datetime.now()

    deviceinfo_after_capture = capture_cloud_deviceinfo(session_dir)
    deviceinfo_after = deviceinfo_after_capture["parsed"]
    (artifact_dir / "deviceinfo_after.log").write_text("\n".join(deviceinfo_after_capture["lines"]) + "\n", encoding="utf-8")
    save_json(artifact_dir / "deviceinfo_after.json", deviceinfo_after)

    ap_window = read_lines_between("COM14", start_dt, end_dt, session_dir=session_dir)
    wb_window = read_lines_between("COM13", start_dt, end_dt, session_dir=session_dir)
    ap_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM14", CLOUD_WAKEUP_WORD_KEYWORDS)
    wb_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM13", CLOUD_WAKEUP_WORD_KEYWORDS)
    (artifact_dir / "COM14_window.log").write_text("\n".join(ap_window) + ("\n" if ap_window else ""), encoding="utf-8")
    (artifact_dir / "COM13_window.log").write_text("\n".join(wb_window) + ("\n" if wb_window else ""), encoding="utf-8")
    (artifact_dir / "COM14_excerpt.log").write_text("\n".join(ap_excerpt) + ("\n" if ap_excerpt else ""), encoding="utf-8")
    (artifact_dir / "COM13_excerpt.log").write_text("\n".join(wb_excerpt) + ("\n" if wb_excerpt else ""), encoding="utf-8")

    response_dict = cloud_response_to_dict(response)
    save_json(artifact_dir / "response.json", response_dict)
    success = cloud_response_ok(response_dict)
    device_rejected = any("invalid wakeup word" in line.lower() for line in ap_window + ap_excerpt)
    summary = {
        "action": "cloud_wakeup_word",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "wakeup_word": wakeup_word,
        "device_rejected": device_rejected,
        "apply_wait_s": apply_wait_s,
        "deviceinfo_before": deviceinfo_before,
        "deviceinfo_after": deviceinfo_after,
        "response": response_dict,
        "ap_excerpt_count": len(ap_excerpt),
        "wb_excerpt_count": len(wb_excerpt),
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError(f"APP/cloud 设置唤醒词失败，HTTP={response_dict}.")
    return summary


def apply_cloud_wakeup_threshold(
    execution_dir: Path,
    session_dir: Path,
    threshold: int,
    *,
    phase_root: str,
    label: str,
    apply_wait_s: float = 12.0,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    wait_for_device_online(session_dir, artifact_dir / "00_wait_online")

    deviceinfo_capture = capture_cloud_deviceinfo(session_dir)
    deviceinfo = deviceinfo_capture["parsed"]
    (artifact_dir / "deviceinfo.log").write_text("\n".join(deviceinfo_capture["lines"]) + "\n", encoding="utf-8")
    save_json(artifact_dir / "deviceinfo.json", deviceinfo)

    start_dt = datetime.now()
    request = build_cloud_request(deviceinfo)
    response = request.wakeup_Threshold_switch(int(threshold))
    time.sleep(apply_wait_s)
    end_dt = datetime.now()

    ap_window = read_lines_between("COM14", start_dt, end_dt, session_dir=session_dir)
    wb_window = read_lines_between("COM13", start_dt, end_dt, session_dir=session_dir)
    ap_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM14", CLOUD_WAKEUP_THRESHOLD_KEYWORDS)
    wb_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM13", CLOUD_WAKEUP_THRESHOLD_KEYWORDS)
    (artifact_dir / "COM14_window.log").write_text("\n".join(ap_window) + ("\n" if ap_window else ""), encoding="utf-8")
    (artifact_dir / "COM13_window.log").write_text("\n".join(wb_window) + ("\n" if wb_window else ""), encoding="utf-8")
    (artifact_dir / "COM14_excerpt.log").write_text("\n".join(ap_excerpt) + ("\n" if ap_excerpt else ""), encoding="utf-8")
    (artifact_dir / "COM13_excerpt.log").write_text("\n".join(wb_excerpt) + ("\n" if wb_excerpt else ""), encoding="utf-8")

    response_dict = cloud_response_to_dict(response)
    save_json(artifact_dir / "response.json", response_dict)
    success = cloud_response_ok(response_dict)
    summary = {
        "action": "cloud_wakeup_threshold",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "threshold": int(threshold),
        "apply_wait_s": apply_wait_s,
        "deviceinfo": deviceinfo,
        "response": response_dict,
        "ap_excerpt_count": len(ap_excerpt),
        "wb_excerpt_count": len(wb_excerpt),
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError(f"APP/cloud 设置唤醒阈值失败，HTTP={response_dict}.")
    return summary


def apply_cloud_log_setting(
    execution_dir: Path,
    session_dir: Path,
    *,
    status: int,
    level: int,
    phase_root: str,
    label: str,
    apply_wait_s: float = 8.0,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    wait_for_device_online(session_dir, artifact_dir / "00_wait_online")

    deviceinfo_capture = capture_cloud_deviceinfo(session_dir)
    deviceinfo = deviceinfo_capture["parsed"]
    (artifact_dir / "deviceinfo.log").write_text("\n".join(deviceinfo_capture["lines"]) + "\n", encoding="utf-8")
    save_json(artifact_dir / "deviceinfo.json", deviceinfo)

    start_dt = datetime.now()
    request = build_cloud_request(deviceinfo)
    response = request.log_set(status=int(status), logLevel=int(level))
    time.sleep(apply_wait_s)
    end_dt = datetime.now()

    ap_window = read_lines_between("COM14", start_dt, end_dt, session_dir=session_dir)
    wb_window = read_lines_between("COM13", start_dt, end_dt, session_dir=session_dir)
    ap_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM14", CLOUD_LOG_KEYWORDS)
    wb_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM13", CLOUD_LOG_KEYWORDS)
    (artifact_dir / "COM14_window.log").write_text("\n".join(ap_window) + ("\n" if ap_window else ""), encoding="utf-8")
    (artifact_dir / "COM13_window.log").write_text("\n".join(wb_window) + ("\n" if wb_window else ""), encoding="utf-8")
    (artifact_dir / "COM14_excerpt.log").write_text("\n".join(ap_excerpt) + ("\n" if ap_excerpt else ""), encoding="utf-8")
    (artifact_dir / "COM13_excerpt.log").write_text("\n".join(wb_excerpt) + ("\n" if wb_excerpt else ""), encoding="utf-8")

    response_dict = cloud_response_to_dict(response)
    save_json(artifact_dir / "response.json", response_dict)
    success = cloud_response_ok(response_dict)
    loglev_changes = extract_cloud_log_level_changes(sanitize_logs({"COM14": ap_window + ap_excerpt}).get("COM14", []))
    summary = {
        "action": "cloud_log_setting",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "status": int(status),
        "level": int(level),
        "apply_wait_s": apply_wait_s,
        "deviceinfo": deviceinfo,
        "response": response_dict,
        "ap_excerpt_count": len(ap_excerpt),
        "wb_excerpt_count": len(wb_excerpt),
        "cloud_change_levels": [item["level"] for item in loglev_changes],
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError(f"APP/cloud 设置日志上传失败，status={status}, level={level}, HTTP={response_dict}.")
    return summary


def apply_cloud_wakeup_audio_upload_setting(
    execution_dir: Path,
    session_dir: Path,
    *,
    enable: bool,
    phase_root: str,
    label: str,
    apply_wait_s: float = 8.0,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    wait_for_device_online(session_dir, artifact_dir / "00_wait_online")

    deviceinfo_capture = capture_cloud_deviceinfo(session_dir)
    deviceinfo = deviceinfo_capture["parsed"]
    (artifact_dir / "deviceinfo.log").write_text("\n".join(deviceinfo_capture["lines"]) + "\n", encoding="utf-8")
    save_json(artifact_dir / "deviceinfo.json", deviceinfo)

    start_dt = datetime.now()
    request = build_cloud_request(deviceinfo)
    response = request.wakeupAudio_upload_new(1 if enable else 0)
    time.sleep(apply_wait_s)
    end_dt = datetime.now()

    ap_window = read_lines_between("COM14", start_dt, end_dt, session_dir=session_dir)
    wb_window = read_lines_between("COM13", start_dt, end_dt, session_dir=session_dir)
    ap_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM14", CLOUD_WAKE_AUDIO_KEYWORDS)
    wb_excerpt = collect_cloud_log_excerpt(session_dir, start_dt, end_dt, "COM13", CLOUD_WAKE_AUDIO_KEYWORDS)
    (artifact_dir / "COM14_window.log").write_text("\n".join(ap_window) + ("\n" if ap_window else ""), encoding="utf-8")
    (artifact_dir / "COM13_window.log").write_text("\n".join(wb_window) + ("\n" if wb_window else ""), encoding="utf-8")
    (artifact_dir / "COM14_excerpt.log").write_text("\n".join(ap_excerpt) + ("\n" if ap_excerpt else ""), encoding="utf-8")
    (artifact_dir / "COM13_excerpt.log").write_text("\n".join(wb_excerpt) + ("\n" if wb_excerpt else ""), encoding="utf-8")

    response_dict = cloud_response_to_dict(response)
    save_json(artifact_dir / "response.json", response_dict)
    success = cloud_response_ok(response_dict)
    config_records = extract_config_query_payloads(sanitize_logs({"COM14": ap_window + ap_excerpt}).get("COM14", []))
    summary = {
        "action": "cloud_wakeup_audio_upload",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "enable": bool(enable),
        "apply_wait_s": apply_wait_s,
        "deviceinfo": deviceinfo,
        "response": response_dict,
        "ap_excerpt_count": len(ap_excerpt),
        "wb_excerpt_count": len(wb_excerpt),
        "config_record_count": len(config_records),
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError(f"APP/cloud 设置唤醒音频上传失败，enable={int(enable)}, HTTP={response_dict}.")
    return summary


def toggle_case_hotspot_state(
    execution_dir: Path,
    session_dir: Path,
    device_mac: str,
    *,
    enable: bool,
    wait_s: float,
    phase_root: str,
    label: str,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    before_status = adapter_hotspot_status()
    action_start = datetime.now()
    action_result = adapter_hotspot_set(enable)
    time.sleep(wait_s)
    action_end = datetime.now()
    after_status = adapter_hotspot_status()
    window = collect_network_window(session_dir, action_start, action_end, artifact_dir, "window")

    if enable:
        success = (
            str(after_status.get("operational_state", "")).lower() == "on"
            and hotspot_has_device(after_status, device_mac)
            and network_window_indicates_online(window)
        )
        expectation = "online"
    else:
        success = (
            str(after_status.get("operational_state", "")).lower() == "off"
            and network_window_indicates_offline(window)
        )
        expectation = "offline"

    summary = {
        "action": f"hotspot_{'on' if enable else 'off'}",
        "artifact_dir": str(artifact_dir),
        "success": success,
        "expected_state": expectation,
        "requested_enable": enable,
        "wait_s": wait_s,
        "before_status": summarize_hotspot_state(before_status, device_mac),
        "action_result": action_result,
        "after_status": summarize_hotspot_state(after_status, device_mac),
        "window": window,
    }
    save_json(artifact_dir / "summary.json", summary)
    if not success:
        raise RuntimeError(f"热点切到 {expectation} 状态后，设备日志未形成预期网络迹象。")
    return summary


def build_mic_probe_case(case, scenario: str):
    if scenario == "mic_off_reminder_window":
        tokens = [
            StepToken(kind="Action", channel="sleep", value="1500"),
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="1000"),
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="1000"),
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="11000"),
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
        ]
    elif scenario in {"mic_off_persist_after_power_cycle_online", "mic_off_persist_after_power_cycle_offline", "mic_off_toggle_persist_after_power_cycle_online"}:
        tokens = [
            StepToken(kind="Action", channel="sleep", value="1200"),
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
        ]
    elif scenario == "mic_on_online_command":
        tokens = [
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="1200"),
            StepToken(kind="Asr", channel="talk", value=TEXT_CMD_AC_ON),
        ]
    elif scenario == "mic_on_persist_after_power_cycle_online":
        tokens = [
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="1200"),
            StepToken(kind="Asr", channel="talk", value=TEXT_CMD_AC_ON),
        ]
    elif scenario == "mic_on_offline_interaction":
        tokens = [
            StepToken(kind="Action", channel="sleep", value="1200"),
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="1200"),
            StepToken(kind="Asr", channel="talk", value=TEXT_CMD_AC_ON),
            StepToken(kind="Action", channel="sleep", value="4500"),
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="1000"),
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="1000"),
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
        ]
    else:
        raise ValueError(f"unsupported mic scenario: {scenario}")
    return replace(case, tokens=tokens)


def run_app_mic_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    env = load_env_config()
    device_mac = str(env.get("current_deviceinfo", {}).get("mac", "")).strip()
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    scenario = str(rules["scenario"])
    enable = scenario in {"mic_on_online_command", "mic_on_offline_interaction", "mic_on_persist_after_power_cycle_online"}
    probe_case = build_mic_probe_case(case, scenario)

    try:
        initial_enable = enable
        initial_label = "01_set_mic_on" if enable else "01_set_mic_off"
        if scenario == "mic_on_persist_after_power_cycle_online":
            initial_enable = False
            initial_label = "01_set_mic_off"
        elif scenario == "mic_off_toggle_persist_after_power_cycle_online":
            initial_enable = True
            initial_label = "01_set_mic_on"
        setup_records.append(
            apply_cloud_mic_switch(
                execution_dir,
                session_dir,
                enable=initial_enable,
                phase_root="setup",
                label=initial_label,
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        if scenario == "mic_on_persist_after_power_cycle_online":
            setup_records.append(
                apply_cloud_mic_switch(
                    execution_dir,
                    session_dir,
                    enable=True,
                    phase_root="setup",
                    label="02_set_mic_on",
                    apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
                )
            )
        elif scenario == "mic_off_toggle_persist_after_power_cycle_online":
            setup_records.append(
                apply_cloud_mic_switch(
                    execution_dir,
                    session_dir,
                    enable=False,
                    phase_root="setup",
                    label="02_set_mic_off",
                    apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
                )
            )
        if scenario in {"mic_on_offline_interaction", "mic_off_persist_after_power_cycle_offline"}:
            setup_records.append(prepare_local_hotspot_attachment(execution_dir, session_dir, device_mac=device_mac))
            setup_records.append(
                toggle_case_hotspot_state(
                    execution_dir,
                    session_dir,
                    device_mac=device_mac,
                    enable=False,
                    wait_s=float(rules.get("disconnect_wait_s", 15.0)),
                    phase_root="setup",
                    label="02_hotspot_offline",
                )
            )
        if scenario in {
            "mic_off_persist_after_power_cycle_online",
            "mic_off_persist_after_power_cycle_offline",
            "mic_on_persist_after_power_cycle_online",
            "mic_off_toggle_persist_after_power_cycle_online",
        }:
            setup_records.append(
                cycle_case_power_target(
                    execution_dir,
                    session_dir,
                    target="wb01",
                    phase_root="setup",
                    label="03_power_cycle_wb01" if "offline" in scenario else "02_power_cycle_wb01",
                    off_wait_s=float(rules.get("power_off_wait_s", 2.0)),
                    observe_s=float(rules.get("power_observe_s", 20.0)),
                )
            )
        payload = execute_standard_audio_case(probe_case, rules, execution_dir, device_key, session_dir, tone_catalog)
    except Exception as exc:
        payload = build_blocked_case_payload(probe_case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        if scenario in {"mic_on_offline_interaction", "mic_off_persist_after_power_cycle_offline"}:
            try:
                recovery_records.append(
                    toggle_case_hotspot_state(
                        execution_dir,
                        session_dir,
                        device_mac=device_mac,
                        enable=True,
                        wait_s=float(rules.get("reconnect_wait_s", 60.0)),
                        phase_root="recovery",
                        label="01_hotspot_online",
                    )
                )
            except Exception as recovery_exc:
                recovery_records.append(
                    {
                        "action": "hotspot_on",
                        "artifact_dir": "",
                        "success": False,
                        "error": str(recovery_exc),
                    }
                )
        if not enable:
            try:
                recovery_records.append(
                    ensure_cloud_mic_on_baseline(
                        execution_dir,
                        session_dir,
                        phase_root="recovery",
                        label="02_restore_mic_on" if recovery_records else "01_restore_mic_on",
                        apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 6.0))),
                    )
                )
            except Exception as recovery_exc:
                recovery_records.append(
                    {
                        "action": "cloud_mic_switch",
                        "artifact_dir": "",
                        "success": False,
                        "error": str(recovery_exc),
                        "enable": True,
                    }
                )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def build_dialog_persist_probe_case(case, dialog_mode: str):
    if dialog_mode == "half":
        tokens = [
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="1200"),
            StepToken(kind="online_Asr", channel="talk", value=TEXT_CMD_AC_OFF),
            StepToken(kind="Action", channel="sleep", value="15000"),
            StepToken(kind="online_UnAsr", channel="talk", value=TEXT_CMD_AC_ON),
            StepToken(kind="Action", channel="sleep", value="5000"),
        ]
    elif dialog_mode == "full":
        tokens = [
            StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
            StepToken(kind="Action", channel="sleep", value="1200"),
            StepToken(kind="online_Asr", channel="talk", value=TEXT_CMD_AC_OFF),
            StepToken(kind="Action", channel="sleep", value="8000"),
            StepToken(kind="online_Asr", channel="talk", value=TEXT_CMD_AC_ON),
            StepToken(kind="Action", channel="sleep", value="8000"),
            StepToken(kind="online_Asr", channel="talk", value=TEXT_MODE_COOL),
            StepToken(kind="Action", channel="sleep", value="8000"),
        ]
    else:
        raise ValueError(f"unsupported dialog_mode for persist probe: {dialog_mode}")
    return replace(case, tokens=tokens)


def apply_voice_dialog_switch(
    execution_dir: Path,
    session_dir: Path,
    *,
    dialog_mode: str,
    device_key: str,
    tone_catalog: dict,
    phase_root: str,
    label: str,
) -> dict:
    bundle = dialog_mode_bundle(dialog_mode)
    phase_root_dir = execution_dir / phase_root
    phase_root_dir.mkdir(parents=True, exist_ok=True)
    phase = {
        "id": label,
        "label": label,
        "sequence": [{"type": "tts", "text": bundle["switch_text"]}],
        "observe_after_ms": 8000,
        "required_keywords": [bundle["switch_keyword"]],
        "min_cp_wake": 1,
        "min_cp_command": 1,
        "min_ap_wake": 1,
    }
    phase_result = execute_dialog_phase(
        phase=phase,
        index=1,
        device_key=device_key,
        execution_dir=phase_root_dir,
        session_dir=session_dir,
        tone_catalog=tone_catalog,
    )
    payload = {
        "action": "voice_dialog_switch",
        "dialog_mode": dialog_mode,
        "artifact_dir": str(phase_root_dir / f"01_{label}"),
        "success": phase_result["result"] == "PASS",
        "phase_result": phase_result,
    }
    if phase_result["result"] != "PASS":
        raise RuntimeError(f"语音切换自然对话到 {dialog_mode} 失败: {phase_result['reason']}")
    return payload


def apply_voice_command_phrase(
    execution_dir: Path,
    session_dir: Path,
    *,
    text: str,
    required_keywords: List[str],
    device_key: str,
    tone_catalog: dict,
    phase_root: str,
    label: str,
    observe_after_ms: int = 8000,
) -> dict:
    phase_root_dir = execution_dir / phase_root
    phase_root_dir.mkdir(parents=True, exist_ok=True)
    phase = {
        "id": label,
        "label": label,
        "sequence": [{"type": "tts", "text": text}],
        "observe_after_ms": observe_after_ms,
        "required_keywords": required_keywords,
        "min_cp_wake": 1,
        "min_cp_command": 1,
    }
    phase_result = execute_dialog_phase(
        phase=phase,
        index=1,
        device_key=device_key,
        execution_dir=phase_root_dir,
        session_dir=session_dir,
        tone_catalog=tone_catalog,
    )
    payload = {
        "action": "voice_command_phrase",
        "text": text,
        "artifact_dir": str(phase_root_dir / f"01_{label}"),
        "success": phase_result["result"] == "PASS",
        "phase_result": phase_result,
    }
    if phase_result["result"] != "PASS":
        raise RuntimeError(f"语音预设命令失败: {phase_result['reason']}")
    return payload


def run_app_dialog_config_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    execution_rules = dict(rules)
    enable = bool(rules.get("full_duplex_enable", True))
    timeout_seconds = int(rules.get("timeout_seconds", 15))
    target_wakeup_word = str(rules.get("target_wakeup_word", "")).strip()
    recovery_wakeup_word = str(rules.get("recovery_wakeup_word", "小美小美")).strip()
    execution_rules["observe_after_ms"] = dialog_observe_after_ms(case, rules)

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        setup_prefix = 1
        if target_wakeup_word:
            setup_records.append(
                apply_cloud_wakeup_word(
                    execution_dir,
                    session_dir,
                    wakeup_word=target_wakeup_word,
                    phase_root="setup",
                    label=f"{setup_prefix:02d}_set_wakeup_word",
                    apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
                )
            )
            setup_prefix += 1
        setup_records.append(
            apply_cloud_full_duplex_setting(
                execution_dir,
                session_dir,
                enable=enable,
                timeout_seconds=timeout_seconds,
                phase_root="setup",
                label=f"{setup_prefix:02d}_set_dialog_config",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
            )
        )
        prepare_command_text = str(rules.get("prepare_command_text", "")).strip()
        if prepare_command_text:
            prepare_keywords = [str(item) for item in rules.get("prepare_command_keywords", [])]
            if not prepare_keywords:
                raise RuntimeError("prepare_command_text 已配置，但 prepare_command_keywords 为空。")
            setup_records.append(
                apply_voice_command_phrase(
                    execution_dir,
                    session_dir,
                    text=prepare_command_text,
                    required_keywords=prepare_keywords,
                    device_key=device_key,
                    tone_catalog=tone_catalog,
                    phase_root="setup",
                    label=f"{setup_prefix + 1:02d}_prepare_voice_command",
                    observe_after_ms=int(rules.get("prepare_observe_after_ms", 8000)),
                )
            )
        payload = execute_standard_audio_case(case, execution_rules, execution_dir, device_key, session_dir, tone_catalog)
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        recovery_index = 1
        try:
            recovery_records.append(
                apply_cloud_full_duplex_setting(
                    execution_dir,
                    session_dir,
                    enable=False,
                    timeout_seconds=int(rules.get("recovery_timeout_seconds", 15)),
                    phase_root="recovery",
                    label=f"{recovery_index:02d}_restore_half_duplex",
                    apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                )
            )
            recovery_index += 1
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_full_duplex",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "enable": False,
                }
            )
        if target_wakeup_word:
            try:
                recovery_records.append(
                    apply_cloud_wakeup_word(
                        execution_dir,
                        session_dir,
                        wakeup_word=recovery_wakeup_word,
                        phase_root="recovery",
                        label=f"{recovery_index:02d}_restore_wakeup_word",
                        apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                    )
                )
            except Exception as recovery_exc:
                recovery_records.append(
                    {
                        "action": "cloud_wakeup_word",
                        "artifact_dir": "",
                        "success": False,
                        "error": str(recovery_exc),
                        "wakeup_word": recovery_wakeup_word,
                    }
                )
    assert payload is not None
    if payload["diagnosis"]["result"] != "BLOCKED":
        try:
            clean_logs = read_clean_logs_from_execution(execution_dir)
            diagnosis, dialog_info = evaluate_dialog_behavior_case(
                case,
                case,
                rules,
                payload,
                clean_logs,
                setup_records=setup_records,
            )
            payload["diagnosis"] = diagnosis
            payload["judge_payload"] = {
                "case_id": case.case_id,
                "name": case.name,
                "result": diagnosis["result"],
                "confidence": diagnosis["confidence"],
                "reason": diagnosis["reason"],
                "checks": diagnosis["checks"],
                "metrics": payload["metrics"],
                "dialog_behavior": dialog_info,
                "tone_names": {
                    str(tone_id): tone_catalog.get(tone_id, "unknown")
                    for tone_id in payload["metrics"]["tone_ids"]
                },
            }
            fingerprint = build_fingerprint(case, payload["metrics"], diagnosis)
            fingerprint["dialog_behavior"] = dialog_info
            payload["fingerprint"] = fingerprint
            payload["failure_excerpt"] = build_dialog_behavior_excerpt(
                case,
                diagnosis,
                payload,
                dialog_info,
                clean_logs,
            )
        except Exception as exc:
            payload["diagnosis"] = {
                "result": "BLOCKED",
                "confidence": rules.get("confidence", "medium"),
                "reason": f"自然对话断言复核失败: {exc}",
                "checks": [],
            }
            payload["judge_payload"]["result"] = "BLOCKED"
            payload["judge_payload"]["reason"] = payload["diagnosis"]["reason"]
            payload["judge_payload"]["checks"] = []
            payload["fingerprint"]["result"] = "BLOCKED"
            payload["failure_excerpt"] += f"\n## Re-evaluate Error\n\n- `{exc}`\n"
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def read_text_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return read_serial_log_lines(path, errors="ignore")


def run_app_dialog_announce_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    del device_key, tone_catalog
    state_dir = execution_dir / "state"
    before_state = snapshot("before", state_dir, session_dir)
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    enable = bool(rules.get("full_duplex_enable", True))
    timeout_seconds = int(rules.get("timeout_seconds", 15))

    setup_error = ""
    after_state = before_state
    state_diff_path = state_dir / "state_diff.json"

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        if bool(rules.get("prepare_enable_full_duplex", False)):
            setup_records.append(
                apply_cloud_full_duplex_setting(
                    execution_dir,
                    session_dir,
                    enable=True,
                    timeout_seconds=int(rules.get("prepare_timeout_seconds", timeout_seconds)),
                    phase_root="setup",
                    label="00_prepare_full_duplex",
                    apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
                )
            )
        setup_records.append(
            apply_cloud_full_duplex_setting(
                execution_dir,
                session_dir,
                enable=enable,
                timeout_seconds=timeout_seconds,
                phase_root="setup",
                label="01_set_dialog_config",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
            )
        )
    except Exception as exc:
        setup_error = str(exc)

    if enable:
        try:
            recovery_records.append(
                apply_cloud_full_duplex_setting(
                    execution_dir,
                    session_dir,
                    enable=False,
                    timeout_seconds=int(rules.get("recovery_timeout_seconds", 15)),
                    phase_root="recovery",
                    label="01_restore_half_duplex",
                    apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_full_duplex",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "enable": False,
                }
            )

    after_state = snapshot("after", state_dir, session_dir)
    state_diff = diff_states(before_state, after_state, state_diff_path)

    target_setup_record = next(
        (
            record
            for record in reversed(setup_records)
            if record.get("action") == "cloud_full_duplex" and bool(record.get("enable")) == enable
        ),
        (setup_records[-1] if setup_records else {}),
    )
    setup_artifact_dir = Path(target_setup_record["artifact_dir"]) if target_setup_record.get("artifact_dir") else execution_dir / "setup" / "01_set_dialog_config"
    ap_lines = read_text_lines(setup_artifact_dir / "COM14_window.log")
    wb_lines = read_text_lines(setup_artifact_dir / "COM13_window.log")
    expected_flag = 1 if enable else 0
    expected_wb_fullduplex = int(rules.get("expected_wb_fullduplex", 2 if enable else 1))

    ap_config_count = sum(1 for line in ap_lines if f"set fullduplex to {'on' if enable else 'off'}" in line.lower())
    ap_ai_flag_count = sum(1 for line in ap_lines if f"set fullduplex is {expected_flag}" in line.lower())
    if enable:
        ap_dialog_broadcast_count = sum(
            1
            for line in ap_lines
            if "cloud.speech.broadcast" in line.lower() or 'skilltype":"fullduplex"' in line.lower()
        )
    else:
        ap_dialog_broadcast_count = sum(
            1
            for line in ap_lines
            if "cloud.speech.broadcast" in line.lower()
            or "cloud.instructions.audiobroadcast" in line.lower()
            or '"asr":"关闭自然对话"' in line
        )
    ap_player_play_count = sum(
        1
        for line in ap_lines
        if "ttsplayer report state: play 2" in line.lower()
        or "ttsplayer play:" in line.lower()
        or "play audio http" in line.lower()
    )
    ap_player_msg_count = sum(1 for line in ap_lines if "player, cmd: 0x4009" in line.lower())
    wb_expected_fullduplex_count = sum(1 for line in wb_lines if f"fullduplex: {expected_wb_fullduplex}" in line.lower())
    wb_process_05_count = sum(1 for line in wb_lines if "process cmd 0x05" in line.lower())
    wb_process_04_count = sum(1 for line in wb_lines if "process cmd 0x04" in line.lower())
    wb_process_03_count = sum(1 for line in wb_lines if "process cmd 0x03" in line.lower())
    wb_event_22_count = sum(1 for line in wb_lines if "msmart_callback event 22" in line.lower())
    wb_event_24_count = sum(1 for line in wb_lines if "msmart_callback event 24" in line.lower())
    wb_player_cmd_count = sum(1 for line in wb_lines if "player, cmd: 0x4009" in line.lower())

    checks: List[dict] = []

    def add_check(name: str, actual, expected, passed: bool) -> None:
        checks.append({"name": name, "actual": actual, "expected": expected, "passed": passed})

    add_check("cloud_apply_success", bool(target_setup_record.get("success")), True, bool(target_setup_record.get("success")))
    add_check(
        "ap_textual_config_reference",
        {
            "ap_config_line_count": ap_config_count,
            "ap_ai_flag_line_count": ap_ai_flag_count,
            "ap_dialog_broadcast_line_count": ap_dialog_broadcast_count,
        },
        "reference-only",
        True,
    )
    add_check(
        "wb_protocol_reference",
        {
            "wb_expected_fullduplex_count": wb_expected_fullduplex_count,
            "wb_process_05_count": wb_process_05_count,
            "wb_process_04_count": wb_process_04_count,
            "wb_process_03_count": wb_process_03_count,
            "wb_event_24_count": wb_event_24_count,
        },
        "reference-only",
        True,
    )
    add_check(
        "dialog_apply_signal",
        {
            "ap_config_line_count": ap_config_count,
            "ap_ai_flag_line_count": ap_ai_flag_count,
            "wb_expected_fullduplex_count": wb_expected_fullduplex_count,
            "wb_process_05_count": wb_process_05_count,
            "wb_process_04_count": wb_process_04_count,
        },
        ">=1 apply evidence",
        (ap_config_count + ap_ai_flag_count + wb_expected_fullduplex_count + wb_process_05_count + wb_process_04_count) > 0,
    )
    add_check(
        "announce_playback_signal",
        {
            "ap_dialog_broadcast_line_count": ap_dialog_broadcast_count,
            "ap_player_play_count": ap_player_play_count,
            "ap_player_msg_count": ap_player_msg_count,
            "wb_process_03_count": wb_process_03_count,
            "wb_player_cmd_count": wb_player_cmd_count,
            "wb_event_22_count": wb_event_22_count,
        },
        ">=1 playback evidence",
        (ap_dialog_broadcast_count + ap_player_play_count + ap_player_msg_count + wb_process_03_count + wb_player_cmd_count + wb_event_22_count) > 0,
    )

    all_passed = all(item["passed"] for item in checks) and not setup_error
    diagnosis = {
        "result": "PASS" if all_passed else ("BLOCKED" if setup_error else "FAIL"),
        "confidence": rules.get("confidence", "medium"),
        "reason": (
            rules["notes"]
            if all_passed
            else (setup_error or f"自然对话配置播报链路未满足 {next((item['name'] for item in checks if not item['passed']), 'unknown')} 自动判定条件。")
        ),
        "checks": checks,
    }

    metrics = {
        "ap_window_line_count": len(ap_lines),
        "wb_window_line_count": len(wb_lines),
        "ap_config_line_count": ap_config_count,
        "ap_ai_flag_line_count": ap_ai_flag_count,
        "ap_dialog_broadcast_count": ap_dialog_broadcast_count,
        "ap_player_play_count": ap_player_play_count,
        "ap_player_msg_count": ap_player_msg_count,
        "wb_expected_fullduplex_count": wb_expected_fullduplex_count,
        "wb_process_05_count": wb_process_05_count,
        "wb_process_04_count": wb_process_04_count,
        "wb_process_03_count": wb_process_03_count,
        "wb_event_22_count": wb_event_22_count,
        "wb_event_24_count": wb_event_24_count,
        "wb_player_cmd_count": wb_player_cmd_count,
    }
    judge_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "reason": diagnosis["reason"],
        "checks": checks,
        "metrics": metrics,
    }
    fingerprint = {
        "case_id": case.case_id,
        "result": diagnosis["result"],
        "full_duplex_enable": enable,
        "timeout_seconds": timeout_seconds,
        "expected_wb_fullduplex": expected_wb_fullduplex,
        "metrics": metrics,
    }
    excerpt_lines = [
        f"# {case.case_id}",
        "",
        f"- Name: `{case.name}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Confidence: `{diagnosis['confidence']}`",
        f"- Reason: {diagnosis['reason']}",
        "",
        "## Checks",
        "",
    ]
    for item in checks:
        excerpt_lines.append(f"- `{item['name']}` -> `{'PASS' if item['passed'] else 'MISS'}` | actual=`{item['actual']}` expected=`{item['expected']}`")
    excerpt_lines += [
        "",
        "## AP key lines",
        "",
    ]
    ap_key_lines = [
        line
        for line in ap_lines
        if any(
            token in line.lower()
            for token in [
                "set fullduplex",
                "cloud.speech.broadcast",
                "cloud.instructions.audiobroadcast",
                "ttsplayer report state: play 2",
                "ttsplayer play:",
                "play audio http",
                "player, cmd: 0x4009",
                "cloud.speech.reply",
            ]
        )
    ]
    if ap_key_lines:
        for line in ap_key_lines[:30]:
            excerpt_lines.append(f"- `{line}`")
    else:
        excerpt_lines.append("- <none>")
    excerpt_lines += [
        "",
        "## WB key lines",
        "",
    ]
    wb_key_lines = [line for line in wb_lines if "fullduplex:" in line.lower()]
    wb_key_lines.extend(
        line
        for line in wb_lines
        if any(
            token in line.lower()
            for token in [
                "process cmd 0x05",
                "process cmd 0x04",
                "process cmd 0x03",
                "msmart_callback event 22",
                "msmart_callback event 24",
                "player, cmd: 0x4009",
            ]
        )
    )
    if wb_key_lines:
        for line in wb_key_lines[:30]:
            excerpt_lines.append(f"- `{line}`")
    else:
        excerpt_lines.append("- <none>")
    failure_excerpt = "\n".join(excerpt_lines) + "\n"

    (execution_dir / "judge.json").write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "failure_excerpt.md").write_text(failure_excerpt, encoding="utf-8")

    result_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "execution_dir": str(execution_dir),
        "started_at": setup_records[0]["started_at"] if setup_records else datetime.now().isoformat(timespec="milliseconds"),
        "ended_at": (recovery_records[-1].get("ended_at") if recovery_records else (setup_records[0]["ended_at"] if setup_records else datetime.now().isoformat(timespec="milliseconds"))),
        "diagnosis": diagnosis,
        "metrics": metrics,
        "states": {
            "before": str(before_state),
            "after": str(after_state),
            "diff": str(state_diff),
        },
        "setup": setup_records,
        "recovery": recovery_records,
        "artifacts": {
            "judge": str(execution_dir / "judge.json"),
            "fingerprint": str(execution_dir / "fingerprint.json"),
            "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
        },
    }
    if setup_error:
        result_payload["setup_error"] = setup_error

    result_path = execution_dir / "doc_case_result.json"
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return result_path


def run_app_dialog_persist_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    dialog_mode = str(rules["dialog_mode"])
    precondition_method = str(rules.get("precondition_method", "cloud"))
    timeout_seconds = int(rules.get("timeout_seconds", 15))
    probe_case = build_dialog_persist_probe_case(case, dialog_mode)
    execution_rules = dict(rules)
    execution_rules["observe_after_ms"] = dialog_observe_after_ms(probe_case, rules)

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        if precondition_method == "voice":
            setup_records.append(
                apply_voice_dialog_switch(
                    execution_dir,
                    session_dir,
                    dialog_mode=dialog_mode,
                    device_key=device_key,
                    tone_catalog=tone_catalog,
                    phase_root="setup",
                    label="01_voice_dialog_switch",
                )
            )
        else:
            setup_records.append(
                apply_cloud_full_duplex_setting(
                    execution_dir,
                    session_dir,
                    enable=dialog_mode == "full",
                    timeout_seconds=timeout_seconds,
                    phase_root="setup",
                    label="01_set_dialog_config",
                    apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
                )
            )
        setup_records.append(
            cycle_case_power_target(
                execution_dir,
                session_dir,
                target="wb01",
                phase_root="setup",
                label="02_power_cycle_wb01",
                off_wait_s=float(rules.get("power_off_wait_s", 2.0)),
                observe_s=float(rules.get("power_observe_s", 20.0)),
            )
        )
        payload = execute_standard_audio_case(probe_case, rules, execution_dir, device_key, session_dir, tone_catalog)
    except Exception as exc:
        payload = build_blocked_case_payload(probe_case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        try:
            recovery_records.append(
                apply_cloud_full_duplex_setting(
                    execution_dir,
                    session_dir,
                    enable=False,
                    timeout_seconds=int(rules.get("recovery_timeout_seconds", 15)),
                    phase_root="recovery",
                    label="01_restore_half_duplex",
                    apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_full_duplex",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "enable": False,
                }
            )
    assert payload is not None
    if payload["diagnosis"]["result"] != "BLOCKED":
        try:
            clean_logs = read_clean_logs_from_execution(execution_dir)
            diagnosis, dialog_info = evaluate_dialog_behavior_case(
                case,
                probe_case,
                rules,
                payload,
                clean_logs,
                setup_records=setup_records,
            )
            payload["diagnosis"] = diagnosis
            payload["judge_payload"] = {
                "case_id": case.case_id,
                "name": case.name,
                "result": diagnosis["result"],
                "confidence": diagnosis["confidence"],
                "reason": diagnosis["reason"],
                "checks": diagnosis["checks"],
                "metrics": payload["metrics"],
                "dialog_behavior": dialog_info,
                "tone_names": {
                    str(tone_id): tone_catalog.get(tone_id, "unknown")
                    for tone_id in payload["metrics"]["tone_ids"]
                },
            }
            fingerprint = build_fingerprint(case, payload["metrics"], diagnosis)
            fingerprint["dialog_behavior"] = dialog_info
            payload["fingerprint"] = fingerprint
            payload["failure_excerpt"] = build_dialog_behavior_excerpt(
                case,
                diagnosis,
                payload,
                dialog_info,
                clean_logs,
            )
        except Exception as exc:
            payload["diagnosis"] = {
                "result": "BLOCKED",
                "confidence": rules.get("confidence", "medium"),
                "reason": f"自然对话掉电断言复核失败: {exc}",
                "checks": [],
            }
            payload["judge_payload"]["result"] = "BLOCKED"
            payload["judge_payload"]["reason"] = payload["diagnosis"]["reason"]
            payload["judge_payload"]["checks"] = []
            payload["fingerprint"]["result"] = "BLOCKED"
            payload["failure_excerpt"] += f"\n## Re-evaluate Error\n\n- `{exc}`\n"
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def build_accent_phase_plan(rules: dict) -> List[dict]:
    phrase = str(rules.get("oneshot_text", f"{WAKE_WORD_TEXT}{TEXT_CMD_AC_ON}"))
    observe_after_ms = int(rules.get("observe_after_ms", 12000))
    scenario = str(rules["scenario"])
    phases: List[dict] = []

    if scenario == "accent_blocks_oneshot":
        for item in rules.get("accent_plan", []):
            accent_id = str(item["accent_id"])
            accent_label = str(item["label"])
            phases.append(
                {
                    "id": f"accent_{accent_id}",
                    "label": f"{accent_label}_oneshot_degraded",
                    "sequence": [{"type": "tts", "text": phrase}],
                    "observe_after_ms": observe_after_ms,
                    "min_cp_wake": 1,
                    "min_ap_wake": 1,
                    "forbidden_online_asr_texts": [TEXT_CMD_AC_ON, phrase],
                    "metadata": {
                        "accent_id": accent_id,
                        "accent_label": accent_label,
                        "expectation": "oneshot should not keep the full command text when accent is enabled",
                    },
                }
            )
        return phases

    if scenario == "accent_off_supports_oneshot":
        return [
            {
                "id": "accent_off_oneshot",
                "label": "普通话_oneshot_restored",
                "sequence": [{"type": "tts", "text": phrase}],
                "observe_after_ms": observe_after_ms,
                "min_cp_wake": 1,
                "min_ap_wake": 1,
                "min_ap_online_asr": 1,
                "required_online_asr_texts": [TEXT_CMD_AC_ON],
                "forbidden_online_asr_texts": [phrase],
                "min_ap_cloud_tts_play": 1,
                "metadata": {
                    "accent_enabled": False,
                    "expectation": "oneshot should recover to full command recognition after accent is disabled",
                },
            }
        ]

    raise ValueError(f"unsupported accent scenario: {scenario}")


def run_app_accent_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    state_dir = execution_dir / "state"
    before_state = snapshot("before", state_dir, session_dir)

    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    phase_results: List[dict] = []
    scenario = str(rules["scenario"])
    timeout_seconds = int(rules.get("timeout_seconds", 15))
    apply_wait_s = float(rules.get("cloud_apply_wait_s", 6.0))
    recovery_wait_s = float(rules.get("cloud_recovery_wait_s", apply_wait_s))
    restore_accent_id = str(rules.get("restore_accent_id", "cantonese"))

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        setup_records.append(
            apply_cloud_full_duplex_setting(
                execution_dir,
                session_dir,
                enable=bool(rules.get("full_duplex_enable", False)),
                timeout_seconds=timeout_seconds,
                phase_root="setup",
                label="01_set_dialog_mode",
                apply_wait_s=float(rules.get("dialog_apply_wait_s", 12.0)),
            )
        )
        if scenario == "accent_off_supports_oneshot":
            pre_accent = str(rules.get("pre_enable_accent_id", "cantonese"))
            setup_records.append(
                apply_cloud_accent_switch(
                    execution_dir,
                    session_dir,
                    accent_id=pre_accent,
                    enable_accent=True,
                    mixed_res_enable=int(rules.get("mixed_res_enable", 0)),
                    phase_root="setup",
                    label="02_enable_accent_before_restore",
                    apply_wait_s=apply_wait_s,
                )
            )
            setup_records.append(
                apply_cloud_accent_switch(
                    execution_dir,
                    session_dir,
                    accent_id=pre_accent,
                    enable_accent=False,
                    mixed_res_enable=int(rules.get("mixed_res_enable", 0)),
                    phase_root="setup",
                    label="03_disable_accent_restore_mandarin",
                    apply_wait_s=apply_wait_s,
                )
            )
        phase_plan = build_accent_phase_plan(rules)
        for index, phase in enumerate(phase_plan, start=1):
            metadata = phase.get("metadata", {})
            accent_id = str(metadata.get("accent_id", "")).strip()
            accent_label = str(metadata.get("accent_label", accent_id)).strip()
            if accent_id:
                setup_summary = apply_cloud_accent_switch(
                    execution_dir,
                    session_dir,
                    accent_id=accent_id,
                    enable_accent=True,
                    mixed_res_enable=int(rules.get("mixed_res_enable", 0)),
                    phase_root="setup",
                    label=f"{index + 10:02d}_set_accent_{accent_id}",
                    apply_wait_s=apply_wait_s,
                )
                phase["metadata"] = {
                    **metadata,
                    "accent_setup_artifact": setup_summary["artifact_dir"],
                }
                setup_records.append(setup_summary)
            phase_result = execute_dialog_phase(
                phase=phase,
                index=index,
                device_key=device_key,
                execution_dir=execution_dir,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
            phase_results.append(phase_result)
            time.sleep(1.0)
    except Exception as exc:
        diagnosis = {
            "result": "BLOCKED",
            "confidence": rules.get("confidence", "medium"),
            "reason": str(exc),
        }
        judge_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "phases": phase_results,
        }
        fingerprint = {
            "case_id": case.case_id,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "phase_results": {phase["phase_id"]: phase["result"] for phase in phase_results},
        }
        excerpt = build_dialog_case_excerpt(case, diagnosis, phase_results, tone_catalog)
        (execution_dir / "judge.json").write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (execution_dir / "fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
        (execution_dir / "failure_excerpt.md").write_text(excerpt, encoding="utf-8")
        result_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "execution_dir": str(execution_dir),
            "diagnosis": diagnosis,
            "phases": phase_results,
            "setup": setup_records,
            "recovery": recovery_records,
            "artifacts": {
                "judge": str(execution_dir / "judge.json"),
                "fingerprint": str(execution_dir / "fingerprint.json"),
                "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
            },
        }
        result_path = execution_dir / "doc_case_result.json"
        result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return result_path
    finally:
        try:
            recovery_records.append(
                apply_cloud_accent_switch(
                    execution_dir,
                    session_dir,
                    accent_id=restore_accent_id,
                    enable_accent=False,
                    mixed_res_enable=int(rules.get("mixed_res_enable", 0)),
                    phase_root="recovery",
                    label="01_restore_mandarin",
                    apply_wait_s=recovery_wait_s,
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_accent_switch",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "accent_id": restore_accent_id,
                    "enable_accent": False,
                }
            )

    after_state = snapshot("after", state_dir, session_dir)
    state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")

    blocked_phases = [phase for phase in phase_results if phase["result"] == "BLOCKED"]
    failed_phases = [phase for phase in phase_results if phase["result"] == "FAIL"]
    if blocked_phases:
        diagnosis = {
            "result": "BLOCKED",
            "confidence": rules.get("confidence", "medium"),
            "reason": blocked_phases[0]["reason"],
        }
    elif failed_phases:
        diagnosis = {
            "result": "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": failed_phases[0]["reason"],
        }
    else:
        diagnosis = {
            "result": "PASS",
            "confidence": rules.get("confidence", "medium"),
            "reason": rules["notes"],
        }

    fingerprint = {
        "case_id": case.case_id,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "phase_results": {phase["phase_id"]: phase["result"] for phase in phase_results},
        "phase_online_asr": {phase["phase_id"]: phase["metrics"]["ap_online_asr_texts"] for phase in phase_results},
    }
    judge_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "reason": diagnosis["reason"],
        "phases": phase_results,
    }
    excerpt = build_dialog_case_excerpt(case, diagnosis, phase_results, tone_catalog)

    (execution_dir / "judge.json").write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "failure_excerpt.md").write_text(excerpt, encoding="utf-8")

    result_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "execution_dir": str(execution_dir),
        "diagnosis": diagnosis,
        "phases": phase_results,
        "setup": setup_records,
        "recovery": recovery_records,
        "states": {
            "before": str(before_state),
            "after": str(after_state),
            "diff": str(state_diff),
        },
        "artifacts": {
            "judge": str(execution_dir / "judge.json"),
            "fingerprint": str(execution_dir / "fingerprint.json"),
            "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
        },
    }
    result_path = execution_dir / "doc_case_result.json"
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# {case.case_id}",
        "",
        f"- Name: `{case.name}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Confidence: `{diagnosis['confidence']}`",
        f"- Reason: {diagnosis['reason']}",
        "",
        "## Phase checks",
        "",
    ]
    for phase in phase_results:
        lines.append(f"- `{phase['phase_id']}` -> `{phase['result']}` | {phase['reason']}")
        for check in phase["checks"]:
            lines.append(f"  - `{check['name']}` -> `{'PASS' if check['passed'] else 'MISS'}` | actual=`{check['actual']}` expected=`{check['expected']}`")
    lines += [
        "",
        "## Setup",
        "",
    ]
    for item in setup_records:
        lines.append(f"- `{item.get('action', 'setup')}` -> `{'PASS' if item.get('success', False) else 'MISS'}` | artifact=`{item.get('artifact_dir', '')}`")
    lines += [
        "",
        "## Recovery",
        "",
    ]
    for item in recovery_records:
        lines.append(f"- `{item.get('action', 'recovery')}` -> `{'PASS' if item.get('success', False) else 'MISS'}` | artifact=`{item.get('artifact_dir', '')}`")
    (execution_dir / "doc_case_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result_path


def run_app_accent_persist_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    state_dir = execution_dir / "state"
    before_state = snapshot("before", state_dir, session_dir)

    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    phase_results: List[dict] = []
    timeout_seconds = int(rules.get("timeout_seconds", 15))
    apply_wait_s = float(rules.get("cloud_apply_wait_s", 6.0))
    recovery_wait_s = float(rules.get("cloud_recovery_wait_s", apply_wait_s))
    restore_accent_id = str(rules.get("restore_accent_id", "cantonese"))
    phrase = str(rules.get("oneshot_text", f"{WAKE_WORD_TEXT}{TEXT_CMD_AC_ON}"))
    observe_after_ms = int(rules.get("observe_after_ms", 12000))
    power_off_wait_s = float(rules.get("power_off_wait_s", 2.0))
    power_observe_s = float(rules.get("power_observe_s", 25.0))
    accent_plan = list(rules.get("accent_plan", []))

    if not accent_plan:
        raise RuntimeError("accent_plan is empty; cannot validate accent persistence.")

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        setup_records.append(
            apply_cloud_full_duplex_setting(
                execution_dir,
                session_dir,
                enable=bool(rules.get("full_duplex_enable", False)),
                timeout_seconds=timeout_seconds,
                phase_root="setup",
                label="01_set_dialog_mode",
                apply_wait_s=float(rules.get("dialog_apply_wait_s", 12.0)),
            )
        )

        for index, item in enumerate(accent_plan, start=1):
            accent_id = str(item["accent_id"]).strip()
            accent_label = str(item.get("label", accent_id)).strip()
            setup_summary = apply_cloud_accent_switch(
                execution_dir,
                session_dir,
                accent_id=accent_id,
                enable_accent=True,
                mixed_res_enable=int(rules.get("mixed_res_enable", 0)),
                phase_root="setup",
                label=f"{index + 10:02d}_set_accent_{accent_id}",
                apply_wait_s=apply_wait_s,
            )
            setup_records.append(setup_summary)

            power_summary = cycle_case_power_target(
                execution_dir,
                session_dir,
                target="wb01",
                phase_root="setup",
                label=f"{index + 20:02d}_power_cycle_{accent_id}",
                off_wait_s=power_off_wait_s,
                observe_s=power_observe_s,
            )
            setup_records.append(power_summary)

            power_logs = read_clean_logs_from_artifact_dir(Path(power_summary["artifact_dir"]))
            config_records = extract_config_query_payloads(power_logs.get("COM14", []))
            accent_uploads = extract_accent_uploads(power_logs.get("COM14", []))
            latest_config = config_records[-1]["data"] if config_records else {}
            accent_cfg = latest_config.get("accent") or {}
            latest_upload = accent_uploads[-1]["payload"] if accent_uploads else {}
            actual_accent_id = str(accent_cfg.get("accentId", "")).strip()
            actual_enable_accent = str(accent_cfg.get("enableAccent", "")).strip()
            upload_accent = str(latest_upload.get("accent", "")).strip()

            phase = {
                "id": f"persist_{accent_id}",
                "label": f"{accent_label}_掉电后仍保持方言配置",
                "sequence": [{"type": "tts", "text": phrase}],
                "observe_after_ms": observe_after_ms,
                "min_cp_wake": 1,
                "min_ap_wake": 1,
                "forbidden_online_asr_texts": [TEXT_CMD_AC_ON, phrase],
                "metadata": {
                    "accent_id": accent_id,
                    "accent_label": accent_label,
                    "expectation": "after hard reboot, accent config should persist and oneshot should remain degraded",
                    "accent_setup_artifact": setup_summary["artifact_dir"],
                    "power_cycle_artifact": power_summary["artifact_dir"],
                },
            }
            phase_result = execute_dialog_phase(
                phase=phase,
                index=index,
                device_key=device_key,
                execution_dir=execution_dir,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )

            persist_checks = [
                {
                    "name": "config_query_count",
                    "actual": len(config_records),
                    "expected": ">=1",
                    "passed": len(config_records) >= 1,
                },
                {
                    "name": "accent_id_after_reboot",
                    "actual": actual_accent_id,
                    "expected": accent_id,
                    "passed": actual_accent_id == accent_id,
                },
                {
                    "name": "enable_accent_after_reboot",
                    "actual": actual_enable_accent,
                    "expected": "1",
                    "passed": actual_enable_accent == "1",
                },
                {
                    "name": "accent_upload_after_reboot",
                    "actual": upload_accent,
                    "expected": accent_id,
                    "passed": upload_accent == accent_id,
                },
            ]
            phase_result["persist_checks"] = persist_checks
            phase_result["config_query"] = {
                "count": len(config_records),
                "latest": latest_config,
                "accent_upload_count": len(accent_uploads),
                "latest_accent_upload": latest_upload,
            }
            metadata = dict(phase_result.get("metadata") or {})
            metadata["config_after_reboot"] = {
                "accent_id": actual_accent_id,
                "enable_accent": actual_enable_accent,
                "accent_upload": upload_accent,
            }
            phase_result["metadata"] = metadata

            persist_failures = [item for item in persist_checks if not item["passed"]]
            if phase_result["result"] != "BLOCKED" and persist_failures:
                head = persist_failures[0]
                phase_result["result"] = "FAIL"
                phase_result["reason"] = (
                    f"{phase['id']} 重启后未满足 {head['name']}，actual={head['actual']} expected={head['expected']}"
                )

            phase_result_path = execution_dir / f"{index:02d}_{phase['id']}" / "phase_result.json"
            phase_result_path.write_text(json.dumps(phase_result, ensure_ascii=False, indent=2), encoding="utf-8")
            phase_results.append(phase_result)
            time.sleep(1.0)
    except Exception as exc:
        diagnosis = {
            "result": "BLOCKED",
            "confidence": rules.get("confidence", "medium"),
            "reason": str(exc),
        }
        judge_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "phases": phase_results,
        }
        fingerprint = {
            "case_id": case.case_id,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "phase_results": {phase["phase_id"]: phase["result"] for phase in phase_results},
        }
        excerpt = build_dialog_case_excerpt(case, diagnosis, phase_results, tone_catalog)
        (execution_dir / "judge.json").write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (execution_dir / "fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
        (execution_dir / "failure_excerpt.md").write_text(excerpt, encoding="utf-8")
        result_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "execution_dir": str(execution_dir),
            "diagnosis": diagnosis,
            "phases": phase_results,
            "setup": setup_records,
            "recovery": recovery_records,
            "artifacts": {
                "judge": str(execution_dir / "judge.json"),
                "fingerprint": str(execution_dir / "fingerprint.json"),
                "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
            },
        }
        result_path = execution_dir / "doc_case_result.json"
        result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return result_path
    finally:
        try:
            recovery_records.append(
                apply_cloud_accent_switch(
                    execution_dir,
                    session_dir,
                    accent_id=restore_accent_id,
                    enable_accent=False,
                    mixed_res_enable=int(rules.get("mixed_res_enable", 0)),
                    phase_root="recovery",
                    label="01_restore_mandarin",
                    apply_wait_s=recovery_wait_s,
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_accent_switch",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "accent_id": restore_accent_id,
                    "enable_accent": False,
                }
            )

    after_state = snapshot("after", state_dir, session_dir)
    state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")

    blocked_phases = [phase for phase in phase_results if phase["result"] == "BLOCKED"]
    failed_phases = [phase for phase in phase_results if phase["result"] == "FAIL"]
    if blocked_phases:
        diagnosis = {
            "result": "BLOCKED",
            "confidence": rules.get("confidence", "medium"),
            "reason": blocked_phases[0]["reason"],
        }
    elif failed_phases:
        diagnosis = {
            "result": "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": failed_phases[0]["reason"],
        }
    else:
        diagnosis = {
            "result": "PASS",
            "confidence": rules.get("confidence", "medium"),
            "reason": rules["notes"],
        }

    fingerprint = {
        "case_id": case.case_id,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "phase_results": {phase["phase_id"]: phase["result"] for phase in phase_results},
        "phase_online_asr": {phase["phase_id"]: phase["metrics"]["ap_online_asr_texts"] for phase in phase_results},
        "phase_accents": {
            phase["phase_id"]: (phase.get("metadata") or {}).get("config_after_reboot", {})
            for phase in phase_results
        },
    }
    judge_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "reason": diagnosis["reason"],
        "phases": phase_results,
    }
    excerpt = build_dialog_case_excerpt(case, diagnosis, phase_results, tone_catalog)

    (execution_dir / "judge.json").write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "failure_excerpt.md").write_text(excerpt, encoding="utf-8")

    result_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "execution_dir": str(execution_dir),
        "diagnosis": diagnosis,
        "phases": phase_results,
        "setup": setup_records,
        "recovery": recovery_records,
        "states": {
            "before": str(before_state),
            "after": str(after_state),
            "diff": str(state_diff),
        },
        "artifacts": {
            "judge": str(execution_dir / "judge.json"),
            "fingerprint": str(execution_dir / "fingerprint.json"),
            "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
            "state_diff": str(state_diff),
        },
    }
    result_path = execution_dir / "doc_case_result.json"
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# {case.case_id}",
        "",
        f"- Name: `{case.name}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Confidence: `{diagnosis['confidence']}`",
        f"- Reason: {diagnosis['reason']}",
        "",
        "## Phase checks",
        "",
    ]
    for phase in phase_results:
        lines.append(f"- `{phase['phase_id']}` -> `{phase['result']}` | {phase['reason']}")
        for check in phase.get("checks", []):
            lines.append(
                f"  - `{check['name']}` -> `{'PASS' if check['passed'] else 'MISS'}` | actual=`{check['actual']}` expected=`{check['expected']}`"
            )
        for check in phase.get("persist_checks", []):
            lines.append(
                f"  - `{check['name']}` -> `{'PASS' if check['passed'] else 'MISS'}` | actual=`{check['actual']}` expected=`{check['expected']}`"
            )
    lines += [
        "",
        "## Setup",
        "",
    ]
    for item in setup_records:
        lines.append(f"- `{item.get('action', 'setup')}` -> `{'PASS' if item.get('success', False) else 'MISS'}` | artifact=`{item.get('artifact_dir', '')}`")
    lines += [
        "",
        "## Recovery",
        "",
    ]
    for item in recovery_records:
        lines.append(f"- `{item.get('action', 'recovery')}` -> `{'PASS' if item.get('success', False) else 'MISS'}` | artifact=`{item.get('artifact_dir', '')}`")
    (execution_dir / "doc_case_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result_path


def run_wake_info_upload_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    state_dir = execution_dir / "state"
    logs_dir = execution_dir / "window_logs"
    audio_dir = execution_dir / "audio"
    logs_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    before_state = snapshot("before", state_dir, session_dir)
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    wake_text = str(rules.get("wake_text", WAKE_WORD_TEXT))
    compare_keys = [
        "keyword",
        "intent",
        "ncmThreshold",
        "nDelayFrame",
        "nThrowFrame",
        "decId",
        "branch",
        "wakeUpType",
        "VadGap",
        "nE2eIntervalFrame",
        "nE2eNodeFrame",
        "bMain",
        "bAbsorb",
    ]

    try:
        setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "01_wait_online"))

        audio_file = audio_dir / f"{case.case_id}.wav"
        audio_manifest = build_sequence([{"type": "tts", "text": wake_text}], audio_file)

        start_dt = datetime.now()
        completed = run_playback(audio_file, device_key, execution_dir, log_prefix="main_play")
        time.sleep(int(rules.get("observe_after_ms", 10000)) / 1000.0)
        end_dt = datetime.now()

        after_state = snapshot("after", state_dir, session_dir)
        state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")

        raw_logs: Dict[str, List[str]] = {}
        for port in ["COM12", "COM13", "COM14"]:
            lines = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
            raw_logs[port] = lines
            (logs_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        clean_logs = sanitize_logs(raw_logs)
        for port, lines in clean_logs.items():
            (logs_dir / f"{port}.clean.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        window_summary = summarize_window(clean_logs)
        metrics = collect_metrics(clean_logs, window_summary)

        local_algo_infos = [
            item
            for item in extract_algo_info_payloads(clean_logs.get("COM14", []))
            if "xiao mei xiao mei" in normalize_keyword(str(item.get("record", {}).get("keyword", "")))
        ]
        upload_wake_infos = [
            item
            for item in extract_wake_info_uploads(clean_logs.get("COM14", []))
            if "xiao mei xiao mei" in normalize_keyword(str(item.get("record", {}).get("keyword", "")))
        ]
        response0_records = [item for item in upload_wake_infos if str(item.get("payload", {}).get("params", {}).get("response", "")) == "0"]
        response1_records = [item for item in upload_wake_infos if str(item.get("payload", {}).get("params", {}).get("response", "")) == "1"]
        latest_local = local_algo_infos[-1] if local_algo_infos else None
        latest_response0 = response0_records[-1] if response0_records else None
        latest_response1 = response1_records[-1] if response1_records else None

        matched_fields: List[str] = []
        mismatched_fields: List[dict] = []
        if latest_local and latest_response1:
            local_record = latest_local["record"]
            upload_record = latest_response1["record"]
            for key in compare_keys:
                local_value = local_record.get(key)
                upload_value = upload_record.get(key)
                if local_value == upload_value:
                    matched_fields.append(key)
                else:
                    mismatched_fields.append({"field": key, "local": local_value, "upload": upload_value})

        env = load_env_config()
        expected_device_id = str(env.get("current_deviceinfo", {}).get("iot_id", "")).strip()
        response0_params = latest_response0.get("payload", {}).get("params", {}) if latest_response0 else {}
        response1_params = latest_response1.get("payload", {}).get("params", {}) if latest_response1 else {}
        response0_session_ok = bool(latest_response0) and str(response0_params.get("sessionId", "")) == "0" and int(response0_params.get("isPreWakeUp", -1)) == 1
        response1_session_ok = (
            bool(latest_response1)
            and str(response1_params.get("sessionId", "")) not in {"", "0"}
            and int(response1_params.get("isPreWakeUp", -1)) == 0
        )
        upload_device_id = str(response1_params.get("deviceId", "")).strip() if latest_response1 else ""
        current_wakeup_word = str(response1_params.get("currentWakeUpWord", "")).strip() if latest_response1 else ""

        metrics.update(
            {
                "local_algo_info_count": len(local_algo_infos),
                "wake_info_upload_total_count": len(upload_wake_infos),
                "wake_info_upload_response0_count": len(response0_records),
                "wake_info_upload_response1_count": len(response1_records),
                "wake_info_matched_fields": matched_fields,
                "wake_info_mismatched_fields": mismatched_fields,
                "wake_info_compare_field_count": len(compare_keys),
                "wake_info_upload_device_id": upload_device_id,
                "wake_info_current_wakeup_word": current_wakeup_word,
            }
        )

        checks = [
            {
                "name": "playback_returncode",
                "actual": completed.returncode,
                "expected": 0,
                "passed": completed.returncode == 0,
            },
            {
                "name": "local_algo_info_count",
                "actual": len(local_algo_infos),
                "expected": ">=1",
                "passed": len(local_algo_infos) >= 1,
            },
            {
                "name": "wake_info_upload_response0_count",
                "actual": len(response0_records),
                "expected": ">=1",
                "passed": len(response0_records) >= 1,
            },
            {
                "name": "wake_info_upload_response1_count",
                "actual": len(response1_records),
                "expected": ">=1",
                "passed": len(response1_records) >= 1,
            },
            {
                "name": "response0_session_prewake",
                "actual": response0_session_ok,
                "expected": True,
                "passed": response0_session_ok,
            },
            {
                "name": "response1_session_active",
                "actual": response1_session_ok,
                "expected": True,
                "passed": response1_session_ok,
            },
            {
                "name": "wake_info_upload_device_id",
                "actual": upload_device_id,
                "expected": expected_device_id,
                "passed": bool(upload_device_id) and upload_device_id == expected_device_id,
            },
            {
                "name": "wake_info_current_wakeup_word",
                "actual": current_wakeup_word,
                "expected": wake_text,
                "passed": bool(current_wakeup_word) and current_wakeup_word == wake_text,
            },
            {
                "name": "wake_info_core_fields_match",
                "actual": len(matched_fields),
                "expected": len(compare_keys),
                "passed": len(mismatched_fields) == 0 and len(matched_fields) == len(compare_keys),
            },
        ]

        if completed.returncode != 0:
            diagnosis = {
                "result": "BLOCKED",
                "confidence": rules.get("confidence", "medium"),
                "reason": "播放唤醒音频阶段失败，未进入 wakeInfo 上传判定。",
                "checks": checks,
            }
        else:
            all_passed = all(item["passed"] for item in checks)
            if all_passed:
                reason = rules["notes"]
            elif len(local_algo_infos) <= 0:
                reason = "唤醒窗口内未在 AP 日志捕获本地 algo info，无法与上报 wakeInfo 做对比。"
            elif len(response1_records) <= 0:
                reason = "唤醒窗口内未在 AP 日志捕获 response=1 的 device.report.wakeInfo 上报。"
            elif mismatched_fields:
                reason = f"本地 algo info 与上传 wakeInfo 核心字段不一致：{mismatched_fields}。"
            else:
                reason = "wakeInfo 上传链路未满足会话/sessionId/设备号/唤醒词的完整判定条件。"
            diagnosis = {
                "result": "PASS" if all_passed else "FAIL",
                "confidence": rules.get("confidence", "medium"),
                "reason": reason,
                "checks": checks,
            }

        key_lines: List[str] = []
        for line in clean_logs.get("COM14", []):
            if "algo info:" in line.lower() or '"topic":"device.report.wakeInfo"' in line:
                key_lines.append(line)

        judge_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "checks": diagnosis["checks"],
            "metrics": metrics,
            "tone_names": {str(tone_id): tone_catalog.get(tone_id, "unknown") for tone_id in metrics["tone_ids"]},
            "latest_local_algo_info": latest_local["record"] if latest_local else {},
            "latest_uploaded_wake_info": latest_response1["payload"] if latest_response1 else {},
        }
        excerpt_lines = [
            f"# {case.case_id}",
            "",
            f"- Name: `{case.name}`",
            f"- Result: `{diagnosis['result']}`",
            f"- Confidence: `{diagnosis['confidence']}`",
            f"- Reason: {diagnosis['reason']}",
            "",
            "## Checks",
            "",
        ]
        for item in checks:
            excerpt_lines.append(
                f"- `{item['name']}` -> `{'PASS' if item['passed'] else 'MISS'}` | actual=`{item['actual']}` expected=`{item['expected']}`"
            )
        excerpt_lines += [
            "",
            "## Matched Fields",
            "",
            f"- `{matched_fields}`",
            "",
            "## Mismatched Fields",
            "",
            f"- `{mismatched_fields}`",
            "",
            "## Key Lines",
            "",
        ]
        for line in key_lines[:40]:
            excerpt_lines.append(f"- `{line}`")
        if not key_lines:
            excerpt_lines.append("- <none>")

        payload = {
            "started_at": start_dt.isoformat(timespec="milliseconds"),
            "ended_at": end_dt.isoformat(timespec="milliseconds"),
            "playback": {
                "audio_file": str(audio_file),
                "manifest": audio_manifest,
                "returncode": completed.returncode,
                "commands": [],
                "segments": [
                    {
                        "name": "wake_probe",
                        "audio_file": str(audio_file),
                        "manifest": audio_manifest,
                        "returncode": completed.returncode,
                    }
                ],
            },
            "states": {
                "before": str(before_state),
                "after": str(after_state),
                "diff": str(state_diff),
            },
            "window_summary": window_summary,
            "metrics": metrics,
            "diagnosis": diagnosis,
            "judge_payload": judge_payload,
            "fingerprint": build_fingerprint(case, metrics, diagnosis),
            "failure_excerpt": "\n".join(excerpt_lines) + "\n",
            "setup": setup_records,
            "recovery": recovery_records,
        }
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup"] = setup_records
        payload["recovery"] = recovery_records
        payload["setup_error"] = str(exc)

    return persist_standard_audio_case(case, execution_dir, payload)


def run_algo_version_upload_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    del device_key
    state_dir = execution_dir / "state"
    logs_dir = execution_dir / "window_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    before_state = snapshot("before", state_dir, session_dir)
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []

    try:
        setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "01_wait_online"))
        power_summary = cycle_case_power_target(
            execution_dir,
            session_dir,
            target="wb01",
            phase_root="setup",
            label="02_power_cycle_wb01",
            off_wait_s=float(rules.get("power_off_wait_s", 2.0)),
            observe_s=float(rules.get("power_observe_s", 25.0)),
        )
        setup_records.append(power_summary)
        setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "03_wait_online_after_power"))

        query_start = datetime.now()
        queue_command("COM14", "version", session_dir=session_dir)
        time.sleep(2.5)
        query_end = datetime.now()
        version_lines = read_lines_between("COM14", query_start, query_end, session_dir=session_dir)
        version_dir = execution_dir / "setup" / "04_query_version_after_power"
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "COM14.log").write_text("\n".join(version_lines) + ("\n" if version_lines else ""), encoding="utf-8")
        setup_records.append(
            {
                "action": "query_version",
                "artifact_dir": str(version_dir),
                "success": any(ALGO_VERSION_LINE_RE.search(line) for line in version_lines),
                "port": "COM14",
                "command": "version",
                "started_at": query_start.isoformat(timespec="milliseconds"),
                "ended_at": query_end.isoformat(timespec="milliseconds"),
                "line_count": len(version_lines),
            }
        )

        after_state = snapshot("after", state_dir, session_dir)
        state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")

        power_window_dir = Path(power_summary["artifact_dir"]) / "window_logs"
        raw_logs = {
            port: read_serial_log_lines(power_window_dir / f"{port}.log", errors="replace")
            for port in ["COM12", "COM13", "COM14"]
        }
        raw_logs["COM14"].extend(version_lines)
        for port, lines in raw_logs.items():
            (logs_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        clean_logs = sanitize_logs(raw_logs)
        for port, lines in clean_logs.items():
            (logs_dir / f"{port}.clean.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        window_summary = summarize_window(clean_logs)
        metrics = collect_metrics(clean_logs, window_summary)

        local_algo_versions = extract_algo_version_lines(clean_logs.get("COM14", []))
        uploaded_algo_versions = extract_algo_version_uploads(clean_logs.get("COM14", []))
        uploaded_esr_versions = extract_uploaded_esr_versions(clean_logs.get("COM14", []))
        latest_local = local_algo_versions[-1] if local_algo_versions else ""
        latest_uploaded_algo = uploaded_algo_versions[-1] if uploaded_algo_versions else None
        latest_uploaded_esr = uploaded_esr_versions[-1] if uploaded_esr_versions else None

        uploaded_algo_text = latest_uploaded_algo["content"] if latest_uploaded_algo else ""
        uploaded_esr_text = latest_uploaded_esr["esr_version"] if latest_uploaded_esr else ""
        upload_device_id = latest_uploaded_algo["device_id"] if latest_uploaded_algo else ""
        esr_device_id = latest_uploaded_esr["device_id"] if latest_uploaded_esr else ""
        env = load_env_config()
        expected_device_id = str(env.get("current_deviceinfo", {}).get("iot_id", "")).strip()

        metrics.update(
            {
                "local_algo_version_count": len(local_algo_versions),
                "uploaded_algo_version_count": len(uploaded_algo_versions),
                "uploaded_esr_version_count": len(uploaded_esr_versions),
                "local_algo_version_text": latest_local,
                "uploaded_algo_version_text": uploaded_algo_text,
                "uploaded_esr_version_text": uploaded_esr_text,
                "algo_version_upload_device_id": upload_device_id,
                "esr_version_upload_device_id": esr_device_id,
            }
        )

        algo_version_match = bool(latest_local) and latest_local == uploaded_algo_text
        esr_version_match = bool(latest_local) and bool(uploaded_esr_text) and uploaded_esr_text in latest_local
        upload_device_match = bool(upload_device_id) and upload_device_id == expected_device_id
        esr_device_match = bool(esr_device_id) and esr_device_id == expected_device_id

        checks = [
            {
                "name": "local_algo_version_count",
                "actual": len(local_algo_versions),
                "expected": ">=1",
                "passed": len(local_algo_versions) >= 1,
            },
            {
                "name": "uploaded_algo_version_count",
                "actual": len(uploaded_algo_versions),
                "expected": ">=1",
                "passed": len(uploaded_algo_versions) >= 1,
            },
            {
                "name": "uploaded_esr_version_count",
                "actual": len(uploaded_esr_versions),
                "expected": ">=1",
                "passed": len(uploaded_esr_versions) >= 1,
            },
            {
                "name": "algo_version_upload_matches_local",
                "actual": algo_version_match,
                "expected": True,
                "passed": algo_version_match,
            },
            {
                "name": "uploaded_esr_version_in_local",
                "actual": esr_version_match,
                "expected": True,
                "passed": esr_version_match,
            },
            {
                "name": "algo_version_upload_device_id",
                "actual": upload_device_id,
                "expected": expected_device_id,
                "passed": upload_device_match,
            },
            {
                "name": "uploaded_esr_device_id",
                "actual": esr_device_id,
                "expected": expected_device_id,
                "passed": esr_device_match,
            },
        ]

        all_passed = all(item["passed"] for item in checks)
        if all_passed:
            reason = rules["notes"]
        elif len(local_algo_versions) <= 0:
            reason = "重启后未通过 AP `version` 命令捕获到本地 Algo Version 行。"
        elif len(uploaded_algo_versions) <= 0:
            reason = "重启窗口内未在 AP 日志捕获 device.report.sdkException / algo_version 上传。"
        elif len(uploaded_esr_versions) <= 0:
            reason = "重启窗口内未在 AP 日志捕获 Upload ESR Version JSON 上报。"
        elif not algo_version_match:
            reason = f"本地 Algo Version 与上传 algo_version 内容不一致：local={latest_local}, upload={uploaded_algo_text}。"
        else:
            reason = f"上传版本链路的设备号或 ESR 版本未与本地版本保持一致：esr={uploaded_esr_text}。"

        diagnosis = {
            "result": "PASS" if all_passed else "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": reason,
            "checks": checks,
        }

        key_lines: List[str] = []
        for line in clean_logs.get("COM14", []):
            if "Algo Version," in line or "Upload ESR Version JSON:" in line or '"topic":"device.report.sdkException"' in line:
                key_lines.append(line)

        judge_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "checks": diagnosis["checks"],
            "metrics": metrics,
            "tone_names": {str(tone_id): tone_catalog.get(tone_id, "unknown") for tone_id in metrics["tone_ids"]},
            "latest_local_algo_version": latest_local,
            "latest_uploaded_algo_version": uploaded_algo_text,
            "latest_uploaded_esr_version": uploaded_esr_text,
        }
        excerpt_lines = [
            f"# {case.case_id}",
            "",
            f"- Name: `{case.name}`",
            f"- Result: `{diagnosis['result']}`",
            f"- Confidence: `{diagnosis['confidence']}`",
            f"- Reason: {diagnosis['reason']}",
            "",
            "## Checks",
            "",
        ]
        for item in checks:
            excerpt_lines.append(
                f"- `{item['name']}` -> `{'PASS' if item['passed'] else 'MISS'}` | actual=`{item['actual']}` expected=`{item['expected']}`"
            )
        excerpt_lines += [
            "",
            "## Version Values",
            "",
            f"- local: `{latest_local}`",
            f"- uploaded_algo: `{uploaded_algo_text}`",
            f"- uploaded_esr: `{uploaded_esr_text}`",
            "",
            "## Key Lines",
            "",
        ]
        for line in key_lines[:40]:
            excerpt_lines.append(f"- `{line}`")
        if not key_lines:
            excerpt_lines.append("- <none>")

        payload = {
            "started_at": power_summary["started_at"],
            "ended_at": query_end.isoformat(timespec="milliseconds"),
            "playback": {
                "audio_file": "",
                "manifest": None,
                "returncode": 0,
                "commands": ["version"],
                "segments": [],
            },
            "states": {
                "before": str(before_state),
                "after": str(after_state),
                "diff": str(state_diff),
            },
            "window_summary": window_summary,
            "metrics": metrics,
            "diagnosis": diagnosis,
            "judge_payload": judge_payload,
            "fingerprint": build_fingerprint(case, metrics, diagnosis),
            "failure_excerpt": "\n".join(excerpt_lines) + "\n",
            "setup": setup_records,
            "recovery": recovery_records,
        }
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup"] = setup_records
        payload["recovery"] = recovery_records
        payload["setup_error"] = str(exc)

    return persist_standard_audio_case(case, execution_dir, payload)


def run_power_broadcast_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    del device_key
    env = load_env_config()
    device_mac = str(env.get("current_deviceinfo", {}).get("mac", "")).strip()
    current_model = str(env.get("current_device_model", "") or env.get("device_model", "")).strip().upper()
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    state_dir = execution_dir / "state"
    before_state = snapshot("before", state_dir, session_dir)

    try:
        if str(rules.get("network_state", "online")) == "offline":
            setup_records.append(prepare_local_hotspot_attachment(execution_dir, session_dir, device_mac=device_mac))
            setup_records.append(
                toggle_case_hotspot_state(
                    execution_dir,
                    session_dir,
                    device_mac=device_mac,
                    enable=False,
                    wait_s=float(rules.get("disconnect_wait_s", 20.0)),
                    phase_root="setup",
                    label="02_hotspot_offline",
                )
            )
            command_index = 3
        else:
            setup_records.append(prepare_local_hotspot_attachment(execution_dir, session_dir, device_mac=device_mac))
            setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "02_wait_online"))
            command_index = 3

        command_records: List[dict] = []
        for offset, command in enumerate(shell_commands(case), start=command_index):
            start_dt = datetime.now()
            queue_command(route_command(command), command, session_dir=session_dir)
            time.sleep(float(rules.get("shell_settle_s", 1.5)))
            end_dt = datetime.now()
            lines = read_lines_between(route_command(command), start_dt, end_dt, session_dir=session_dir)
            artifact_dir = execution_dir / "setup" / f"{offset:02d}_shell_{command.replace(' ', '_').replace('.', '_')}"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            port = route_command(command)
            (artifact_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            command_records.append(
                {
                    "action": "shell_command",
                    "artifact_dir": str(artifact_dir),
                    "success": True,
                    "port": port,
                    "command": command,
                    "started_at": start_dt.isoformat(timespec="milliseconds"),
                    "ended_at": end_dt.isoformat(timespec="milliseconds"),
                    "line_count": len(lines),
                }
            )
        setup_records.extend(command_records)

        power_summary = cycle_case_power_target(
            execution_dir,
            session_dir,
            target="wb01",
            phase_root="setup",
            label=f"{len(setup_records) + 1:02d}_power_cycle_wb01",
            off_wait_s=float(rules.get("power_off_wait_s", 2.0)),
            observe_s=float(rules.get("power_observe_s", 25.0)),
        )
        setup_records.append(power_summary)

        window_dir = Path(power_summary["artifact_dir"]) / "window_logs"
        raw_logs = {
            port: read_serial_log_lines(window_dir / f"{port}.log", errors="replace")
            for port in ["COM12", "COM13", "COM14"]
        }
        clean_logs = sanitize_logs(raw_logs)
        logs_dir = execution_dir / "window_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        for port, lines in raw_logs.items():
            (logs_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        for port, lines in clean_logs.items():
            (logs_dir / f"{port}.clean.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        window_summary = summarize_window(clean_logs)
        metrics = collect_metrics(clean_logs, window_summary)
        evaluation_rules = dict(rules)
        if case.case_id == "美的空调_1" and "CA3X" in current_model:
            # learnCase 历史 CA3X/T6 结果与当前备注一致：该机型离线欢迎播报长期落在 102 + 290 组合，
            # 不能再按通用 406 口径误判。
            evaluation_rules["required_tones"] = [102, 290]
            evaluation_rules.pop("forbidden_tones", None)
            evaluation_rules["notes"] = "热点离线后执行 WB01 硬重启，当前 CA3X/T6 机型按备注口径验证离线上电欢迎播报链路（102 + 290）。"
        diagnosis = evaluate_case_with_rules(case, metrics, evaluation_rules)
        after_state = snapshot("after", state_dir, session_dir)
        state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")
        judge_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "checks": diagnosis["checks"],
            "metrics": metrics,
            "tone_names": {str(tone_id): tone_catalog.get(tone_id, "unknown") for tone_id in metrics["tone_ids"]},
        }
        payload = {
            "started_at": power_summary["started_at"],
            "ended_at": power_summary["ended_at"],
            "playback": {
                "audio_file": "",
                "manifest": None,
                "returncode": 0,
                "commands": shell_commands(case),
                "segments": [],
            },
            "states": {
                "before": str(before_state),
                "after": str(after_state),
                "diff": str(state_diff),
            },
            "window_summary": window_summary,
            "metrics": metrics,
            "diagnosis": diagnosis,
            "judge_payload": judge_payload,
            "fingerprint": build_fingerprint(case, metrics, diagnosis),
            "failure_excerpt": build_excerpt(case, diagnosis, metrics, tone_catalog, clean_logs),
        }
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        if str(rules.get("network_state", "online")) == "offline":
            try:
                recovery_records.append(
                    toggle_case_hotspot_state(
                        execution_dir,
                        session_dir,
                        device_mac=device_mac,
                        enable=True,
                        wait_s=float(rules.get("reconnect_wait_s", 70.0)),
                        phase_root="recovery",
                        label="01_hotspot_online",
                    )
                )
            except Exception as recovery_exc:
                recovery_records.append(
                    {
                        "action": "hotspot_on",
                        "artifact_dir": "",
                        "success": False,
                        "error": str(recovery_exc),
                    }
                )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def run_network_disconnect_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    del device_key
    env = load_env_config()
    device_mac = str(env.get("current_deviceinfo", {}).get("mac", "")).strip()
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    state_dir = execution_dir / "state"
    before_state = snapshot("before", state_dir, session_dir)

    try:
        setup_records.append(prepare_local_hotspot_attachment(execution_dir, session_dir, device_mac=device_mac))
        setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "02_wait_online"))
        disconnect_summary = toggle_case_hotspot_state(
            execution_dir,
            session_dir,
            device_mac=device_mac,
            enable=False,
            wait_s=float(rules.get("disconnect_wait_s", 15.0)),
            phase_root="setup",
            label="03_hotspot_offline",
        )
        setup_records.append(disconnect_summary)

        artifact_dir = Path(disconnect_summary["artifact_dir"])
        raw_logs = {
            port: read_serial_log_lines(artifact_dir / f"window_{port}.log", errors="replace")
            for port in ["COM12", "COM13", "COM14"]
        }
        clean_logs = sanitize_logs(raw_logs)
        logs_dir = execution_dir / "window_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        for port, lines in raw_logs.items():
            (logs_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        for port, lines in clean_logs.items():
            (logs_dir / f"{port}.clean.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        window_summary = summarize_window(clean_logs)
        metrics = collect_metrics(clean_logs, window_summary)
        metrics["ap_ai_disconnected_count"] = sum(1 for line in clean_logs.get("COM14", []) if AI_DISCONNECT_RE.search(line))
        metrics["wb_ai_state4_count"] = sum(1 for line in clean_logs.get("COM13", []) if WB_AI_STATE4_RE.search(line))
        checks = [
            {
                "name": "ap_ai_disconnected_count",
                "actual": metrics["ap_ai_disconnected_count"],
                "expected": ">=1",
                "passed": metrics["ap_ai_disconnected_count"] >= 1,
            },
            {
                "name": "wb_ai_state4_count",
                "actual": metrics["wb_ai_state4_count"],
                "expected": ">=1",
                "passed": metrics["wb_ai_state4_count"] >= 1,
            },
        ]
        all_passed = all(item["passed"] for item in checks)
        diagnosis = {
            "result": "PASS" if all_passed else "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": rules["notes"] if all_passed else build_failure_reason(case.case_id, metrics),
            "checks": checks,
        }
        after_state = snapshot("after", state_dir, session_dir)
        state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")
        judge_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "checks": diagnosis["checks"],
            "metrics": metrics,
            "tone_names": {},
        }
        payload = {
            "started_at": disconnect_summary["window"]["start"],
            "ended_at": disconnect_summary["window"]["end"],
            "playback": {
                "audio_file": "",
                "manifest": None,
                "returncode": 0,
                "commands": [],
                "segments": [],
            },
            "states": {
                "before": str(before_state),
                "after": str(after_state),
                "diff": str(state_diff),
            },
            "window_summary": window_summary,
            "metrics": metrics,
            "diagnosis": diagnosis,
            "judge_payload": judge_payload,
            "fingerprint": build_fingerprint(case, metrics, diagnosis),
            "failure_excerpt": build_excerpt(case, diagnosis, metrics, tone_catalog, clean_logs),
        }
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        try:
            recovery_records.append(
                toggle_case_hotspot_state(
                    execution_dir,
                    session_dir,
                    device_mac=device_mac,
                    enable=True,
                    wait_s=float(rules.get("reconnect_wait_s", 70.0)),
                    phase_root="recovery",
                    label="01_hotspot_online",
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "hotspot_on",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                }
            )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def build_network_reconnect_phase_plan(rules: dict) -> List[dict]:
    offline_observe_after_ms = int(rules.get("offline_observe_after_ms", 12000))
    online_observe_after_ms = int(rules.get("online_observe_after_ms", 12000))
    return [
        {
            "id": "offline_online_skill_blocked",
            "label": "断网后在线技能应失效",
            "sequence": [{"type": "tts", "text": f"{WAKE_WORD_TEXT}，{TEXT_TIME_QUERY}"}],
            "observe_after_ms": offline_observe_after_ms,
            "min_cp_wake": 1,
            "min_ap_wake": 1,
            "min_wb_wake": 1,
            "max_ap_online_asr": 0,
            "max_ap_cloud_tts_play": 0,
            "max_unique_command_keywords": 0,
            "metadata": {
                "network_state": "offline",
                "expectation": "online skill should not enter online ASR or cloud TTS while offline",
            },
        },
        {
            "id": "offline_local_command_ok",
            "label": "断网后离线空调命令仍可用",
            "sequence": [{"type": "tts", "text": f"{WAKE_WORD_TEXT}，{TEXT_CMD_AC_ON}"}],
            "observe_after_ms": offline_observe_after_ms,
            "min_cp_wake": 1,
            "min_cp_command": 1,
            "min_ap_wake": 1,
            "min_ap_asr": 1,
            "min_wb_wake": 1,
            "min_wb_asr": 1,
            "min_wb_playback_end": 1,
            "required_tones": [3],
            "required_keywords": ["kong tiao kai ji"],
            "metadata": {
                "network_state": "offline",
                "expectation": "offline air-conditioner command should still work while hotspot is off",
            },
        },
        {
            "id": "online_skill_restored",
            "label": "联网恢复后在线技能应恢复",
            "sequence": [{"type": "tts", "text": f"{WAKE_WORD_TEXT}，{TEXT_TIME_QUERY}"}],
            "observe_after_ms": online_observe_after_ms,
            "min_cp_wake": 1,
            "min_ap_wake": 1,
            "min_wb_online_wake": 1,
            "min_ap_online_asr": 1,
            "min_ap_cloud_tts_play": 1,
            "max_unique_command_keywords": 0,
            "metadata": {
                "network_state": "online",
                "expectation": "online skill should regain online ASR and cloud TTS after hotspot recovery",
            },
        },
        {
            "id": "online_local_command_restored",
            "label": "联网恢复后空调控制也应正常",
            "sequence": [{"type": "tts", "text": f"{WAKE_WORD_TEXT}，{TEXT_CMD_AC_ON}"}],
            "observe_after_ms": online_observe_after_ms,
            "min_cp_wake": 1,
            "min_cp_command": 1,
            "min_ap_wake": 1,
            "min_wb_online_wake": 1,
            "min_ap_cloud_tts_play": 1,
            "required_keywords": ["kong tiao kai ji"],
            "metadata": {
                "network_state": "online",
                "expectation": "after reconnect, both online wake path and air-conditioner command path should be available",
            },
        },
    ]


def run_network_reconnect_voice_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    env = load_env_config()
    device_mac = str(env.get("current_deviceinfo", {}).get("mac", "")).strip()
    state_dir = execution_dir / "state"
    before_state = snapshot("before", state_dir, session_dir)
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    phase_results: List[dict] = []

    try:
        setup_records.append(prepare_local_hotspot_attachment(execution_dir, session_dir, device_mac=device_mac))
        setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "02_wait_online"))
        setup_records.append(
            toggle_case_hotspot_state(
                execution_dir,
                session_dir,
                device_mac=device_mac,
                enable=False,
                wait_s=float(rules.get("disconnect_wait_s", 25.0)),
                phase_root="setup",
                label="03_hotspot_offline",
            )
        )
        phase_plan = build_network_reconnect_phase_plan(rules)
        for index, phase in enumerate(phase_plan[:2], start=1):
            phase_results.append(
                execute_dialog_phase(
                    phase=phase,
                    index=index,
                    device_key=device_key,
                    execution_dir=execution_dir,
                    session_dir=session_dir,
                    tone_catalog=tone_catalog,
                )
            )
            time.sleep(1.0)
        recovery_records.append(
            toggle_case_hotspot_state(
                execution_dir,
                session_dir,
                device_mac=device_mac,
                enable=True,
                wait_s=float(rules.get("reconnect_wait_s", 70.0)),
                phase_root="recovery",
                label="01_hotspot_online",
            )
        )
        recovery_records.append(wait_for_device_online(session_dir, execution_dir / "recovery" / "02_wait_online"))
        for offset, phase in enumerate(phase_plan[2:], start=3):
            phase_results.append(
                execute_dialog_phase(
                    phase=phase,
                    index=offset,
                    device_key=device_key,
                    execution_dir=execution_dir,
                    session_dir=session_dir,
                    tone_catalog=tone_catalog,
                )
            )
            time.sleep(1.0)
    except Exception as exc:
        diagnosis = {
            "result": "BLOCKED",
            "confidence": rules.get("confidence", "medium"),
            "reason": str(exc),
        }
        judge_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "phases": phase_results,
        }
        fingerprint = {
            "case_id": case.case_id,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "phase_results": {phase["phase_id"]: phase["result"] for phase in phase_results},
        }
        excerpt = build_dialog_case_excerpt(case, diagnosis, phase_results, tone_catalog)
        (execution_dir / "judge.json").write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (execution_dir / "fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
        (execution_dir / "failure_excerpt.md").write_text(excerpt, encoding="utf-8")
        result_payload = {
            "case_id": case.case_id,
            "name": case.name,
            "execution_dir": str(execution_dir),
            "diagnosis": diagnosis,
            "phases": phase_results,
            "setup": setup_records,
            "recovery": recovery_records,
            "artifacts": {
                "judge": str(execution_dir / "judge.json"),
                "fingerprint": str(execution_dir / "fingerprint.json"),
                "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
            },
        }
        result_path = execution_dir / "doc_case_result.json"
        result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return result_path
    finally:
        if not any(item.get("action") == "hotspot_on" or item.get("action") == "hotspot_on" for item in recovery_records):
            try:
                recovery_records.append(
                    toggle_case_hotspot_state(
                        execution_dir,
                        session_dir,
                        device_mac=device_mac,
                        enable=True,
                        wait_s=float(rules.get("reconnect_wait_s", 70.0)),
                        phase_root="recovery",
                        label="99_hotspot_online_final",
                    )
                )
            except Exception as recovery_exc:
                recovery_records.append(
                    {
                        "action": "hotspot_on",
                        "artifact_dir": "",
                        "success": False,
                        "error": str(recovery_exc),
                    }
                )

    after_state = snapshot("after", state_dir, session_dir)
    state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")
    blocked_phases = [phase for phase in phase_results if phase["result"] == "BLOCKED"]
    failed_phases = [phase for phase in phase_results if phase["result"] == "FAIL"]
    if blocked_phases:
        diagnosis = {
            "result": "BLOCKED",
            "confidence": rules.get("confidence", "medium"),
            "reason": blocked_phases[0]["reason"],
        }
    elif failed_phases:
        diagnosis = {
            "result": "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": failed_phases[0]["reason"],
        }
    else:
        diagnosis = {
            "result": "PASS",
            "confidence": rules.get("confidence", "medium"),
            "reason": rules["notes"],
        }

    fingerprint = {
        "case_id": case.case_id,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "phase_results": {phase["phase_id"]: phase["result"] for phase in phase_results},
        "phase_online_asr": {phase["phase_id"]: phase["metrics"]["ap_online_asr_texts"] for phase in phase_results},
        "phase_keywords": {phase["phase_id"]: phase["metrics"]["recognized_command_keywords"] for phase in phase_results},
    }
    judge_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "reason": diagnosis["reason"],
        "phases": phase_results,
    }
    excerpt = build_dialog_case_excerpt(case, diagnosis, phase_results, tone_catalog)
    (execution_dir / "judge.json").write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "failure_excerpt.md").write_text(excerpt, encoding="utf-8")

    result_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "execution_dir": str(execution_dir),
        "diagnosis": diagnosis,
        "phases": phase_results,
        "setup": setup_records,
        "recovery": recovery_records,
        "states": {
            "before": str(before_state),
            "after": str(after_state),
            "diff": str(state_diff),
        },
        "artifacts": {
            "judge": str(execution_dir / "judge.json"),
            "fingerprint": str(execution_dir / "fingerprint.json"),
            "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
        },
    }
    result_path = execution_dir / "doc_case_result.json"
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# {case.case_id}",
        "",
        f"- Name: `{case.name}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Confidence: `{diagnosis['confidence']}`",
        f"- Reason: {diagnosis['reason']}",
        "",
        "## Phase checks",
        "",
    ]
    for phase in phase_results:
        lines.append(f"- `{phase['phase_id']}` -> `{phase['result']}` | {phase['reason']}")
        for check in phase["checks"]:
            lines.append(f"  - `{check['name']}` -> `{'PASS' if check['passed'] else 'MISS'}` | actual=`{check['actual']}` expected=`{check['expected']}`")
    lines += [
        "",
        "## Setup",
        "",
    ]
    for item in setup_records:
        lines.append(f"- `{item.get('action', 'setup')}` -> `{'PASS' if item.get('success', False) else 'MISS'}` | artifact=`{item.get('artifact_dir', '')}`")
    lines += [
        "",
        "## Recovery",
        "",
    ]
    for item in recovery_records:
        lines.append(f"- `{item.get('action', 'recovery')}` -> `{'PASS' if item.get('success', False) else 'MISS'}` | artifact=`{item.get('artifact_dir', '')}`")
    (execution_dir / "doc_case_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result_path


def build_online_empty_nlu_probe_case(case):
    tokens: List[StepToken] = []
    for _ in range(4):
        tokens.extend(
            [
                StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
                StepToken(kind="Action", channel="sleep", value="1200"),
                StepToken(kind="online_Asr", channel="talk", value="度"),
                StepToken(kind="Action", channel="sleep", value="7000"),
            ]
        )
    return replace(case, tokens=tokens)


def build_cloud_log_probe_case(case, rounds: int = 2):
    tokens: List[StepToken] = []
    for index in range(rounds):
        tokens.extend(
            [
                StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT),
                StepToken(kind="Action", channel="sleep", value="1200"),
                StepToken(kind="online_Asr", channel="talk", value=TEXT_TIME_QUERY),
            ]
        )
        if index < rounds - 1:
            tokens.append(StepToken(kind="Action", channel="sleep", value="6000"))
    return replace(case, tokens=tokens)


def build_wakeup_audio_upload_probe_case(case, rounds: int = 10, gap_ms: int = 1800, tail_silence_ms: int = 0):
    tokens: List[StepToken] = []
    for index in range(rounds):
        tokens.append(StepToken(kind="Wakeup", channel="talk", value=WAKE_WORD_TEXT))
        if index < rounds - 1:
            tokens.append(StepToken(kind="Action", channel="sleep", value=str(gap_ms)))
    if tail_silence_ms > 0:
        tokens.append(StepToken(kind="Action", channel="sleep", value=str(tail_silence_ms)))
    return replace(case, tokens=tokens)


def run_online_empty_nlu_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    probe_case = build_online_empty_nlu_probe_case(case)
    execution_rules = dict(rules)
    try:
        setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "01_wait_online"))
        payload = execute_standard_audio_case(probe_case, execution_rules, execution_dir, device_key, session_dir, tone_catalog)
        clean_logs = read_clean_logs_from_execution(execution_dir)
        dialog_metrics = collect_dialog_behavior_metrics(clean_logs)
        asr_invalid_records = dialog_metrics["asr_invalid_records"]
        asr_invalid_records_json = [
            {
                "mid": item.get("mid", ""),
                "skill_id": item.get("skill_id", ""),
                "stream": bool(item.get("stream")),
                "end_session": bool(item.get("end_session")),
                "texts": list(item.get("texts", [])),
                "urls": list(item.get("urls", [])),
                "timestamp": item.get("timestamp").isoformat(timespec="milliseconds") if item.get("timestamp") else "",
                "line": str(item.get("line", "")),
            }
            for item in asr_invalid_records
        ]
        expected_rounds = int(rules.get("min_cp_wake", 1))
        required_texts = [normalize_online_asr_text(item).strip().lower() for item in rules.get("required_online_asr_texts", [])]
        actual_online_asr = [normalize_online_asr_text(item).strip().lower() for item in payload["metrics"]["ap_online_asr_texts"]]
        ignored_command_keywords = {"wen du shu zi fan ji"}
        effective_command_keywords = [
            item
            for item in payload["metrics"]["recognized_command_keywords"]
            if normalize_keyword(item) not in ignored_command_keywords
        ]

        payload["metrics"].update(
            {
                "asr_invalid_broadcast_count": len(asr_invalid_records),
                "asr_invalid_end_session_count": sum(1 for item in asr_invalid_records if item.get("end_session")),
                "online_empty_nlu_effective_command_keywords": effective_command_keywords,
            }
        )

        checks = [
            {
                "name": "cp_wake_count",
                "actual": payload["metrics"]["cp_wake_count"],
                "expected": f">={rules['min_cp_wake']}",
                "passed": payload["metrics"]["cp_wake_count"] >= rules["min_cp_wake"],
            },
            {
                "name": "ap_wake_count",
                "actual": payload["metrics"]["ap_wake_count"],
                "expected": f">={rules['min_ap_wake']}",
                "passed": payload["metrics"]["ap_wake_count"] >= rules["min_ap_wake"],
            },
            {
                "name": "wb_online_wake_count",
                "actual": payload["metrics"]["wb_online_wake_count"],
                "expected": f">={rules['min_wb_online_wake']}",
                "passed": payload["metrics"]["wb_online_wake_count"] >= rules["min_wb_online_wake"],
            },
            {
                "name": "unique_command_keyword_count_max",
                "actual": len(effective_command_keywords),
                "expected": f"<={rules['max_unique_command_keywords']}",
                "passed": len(effective_command_keywords) <= rules["max_unique_command_keywords"],
            },
            {
                "name": "required_online_asr_texts",
                "actual": actual_online_asr,
                "expected": required_texts,
                "passed": set(required_texts).issubset(set(actual_online_asr)),
            },
            {
                "name": "asr_invalid_broadcast_count",
                "actual": len(asr_invalid_records),
                "expected": f">={expected_rounds}",
                "passed": len(asr_invalid_records) >= expected_rounds,
            },
            {
                "name": "asr_invalid_end_session_count",
                "actual": sum(1 for item in asr_invalid_records if item.get("end_session")),
                "expected": f">={expected_rounds}",
                "passed": sum(1 for item in asr_invalid_records if item.get("end_session")) >= expected_rounds,
            },
        ]
        all_passed = all(item["passed"] for item in checks)
        if all_passed:
            reason = rules["notes"]
        elif payload["metrics"]["cp_wake_count"] == 0 and payload["metrics"]["ap_wake_count"] == 0:
            reason = "在线 empty NLU 探针未形成稳定唤醒链路，当前更像设备/音频链路问题。"
        elif not set(required_texts).issubset(set(actual_online_asr)):
            reason = f"未稳定形成文档要求的在线 ASR 文本 {required_texts}，当前仅观测到 {actual_online_asr}。"
        else:
            reason = (
                "在线 ASR 已形成 empty-NLU 文本，但 `cloud.instructions.audioBroadcast` "
                "里 asrInvalid 兜底播报次数不足，未达到文档要求的连续闭环次数。"
            )

        diagnosis = {
            "result": "PASS" if all_passed else "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": reason,
            "checks": checks,
        }
        payload["diagnosis"] = diagnosis
        payload["judge_payload"] = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "checks": diagnosis["checks"],
            "metrics": payload["metrics"],
            "asr_invalid_records": asr_invalid_records_json,
            "tone_names": {str(tone_id): tone_catalog.get(tone_id, "unknown") for tone_id in payload["metrics"]["tone_ids"]},
        }
        fingerprint = build_fingerprint(case, payload["metrics"], diagnosis)
        fingerprint["asr_invalid_broadcast_count"] = len(asr_invalid_records)
        fingerprint["asr_invalid_end_session_count"] = sum(1 for item in asr_invalid_records if item.get("end_session"))
        payload["fingerprint"] = fingerprint
        payload["failure_excerpt"] = build_excerpt(case, diagnosis, payload["metrics"], tone_catalog, clean_logs)
    except Exception as exc:
        payload = build_blocked_case_payload(probe_case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = []
    return persist_standard_audio_case(case, execution_dir, payload)


def run_serial_only_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    env = load_env_config()
    device_mac = str(env.get("current_deviceinfo", {}).get("mac", "")).strip()
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    try:
        setup_records.append(prepare_local_hotspot_attachment(execution_dir, session_dir, device_mac=device_mac))
        setup_records.append(
            toggle_case_hotspot_state(
                execution_dir,
                session_dir,
                device_mac=device_mac,
                enable=False,
                wait_s=float(rules.get("disconnect_wait_s", 15.0)),
                phase_root="setup",
                label="02_hotspot_offline",
            )
        )
        payload = execute_standard_audio_case(case, rules, execution_dir, device_key, session_dir, tone_catalog)
        clean_logs = read_clean_logs_from_execution(execution_dir)
        command_echo = bool(payload["metrics"]["command_lines"])
        has_playback_trace = any(
            [
                payload["metrics"]["wb_playback_start_count"] > 0,
                payload["metrics"]["wb_playback_end_count"] > 0,
                len(payload["metrics"]["wb_tts_callback_ids"]) > 0,
                len(payload["metrics"]["tone_ids"]) > 0,
            ]
        )
        if command_echo and not has_playback_trace:
            diagnosis = {
                "result": "BLOCKED",
                "confidence": rules.get("confidence", "medium"),
                "reason": (
                    "串口命令已成功回显，但当前设备日志没有稳定输出 playback start/end 或 tts callback；"
                    "结合 learnCase 历史执行器，该项本质上仍是串口示例/人工听音闭环，用自动日志暂无法客观判 PASS/FAIL。"
                ),
                "checks": [
                    {
                        "name": "command_echo",
                        "actual": command_echo,
                        "expected": True,
                        "passed": True,
                    },
                    {
                        "name": "playback_trace_present",
                        "actual": has_playback_trace,
                        "expected": True,
                        "passed": False,
                    },
                ],
            }
            payload["diagnosis"] = diagnosis
            payload["judge_payload"] = {
                "case_id": case.case_id,
                "name": case.name,
                "result": diagnosis["result"],
                "confidence": diagnosis["confidence"],
                "reason": diagnosis["reason"],
                "checks": diagnosis["checks"],
                "metrics": payload["metrics"],
                "tone_names": {str(tone_id): tone_catalog.get(tone_id, "unknown") for tone_id in payload["metrics"]["tone_ids"]},
            }
            payload["fingerprint"] = build_fingerprint(case, payload["metrics"], diagnosis)
            payload["failure_excerpt"] = build_excerpt(case, diagnosis, payload["metrics"], tone_catalog, clean_logs)
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        try:
            recovery_records.append(
                toggle_case_hotspot_state(
                    execution_dir,
                    session_dir,
                    device_mac=device_mac,
                    enable=True,
                    wait_s=float(rules.get("reconnect_wait_s", 60.0)),
                    phase_root="recovery",
                    label="01_hotspot_online",
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "hotspot_on",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                }
            )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def execute_shell_setup_commands(
    execution_dir: Path,
    session_dir: Path,
    *,
    commands: List[str],
    phase_root: str,
    label: str,
    settle_s: float = 0.8,
    observe_s: float = 2.0,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)

    start_dt = datetime.now()
    for command in commands:
        queue_command(route_command(command), command, session_dir=session_dir)
        time.sleep(settle_s)
    time.sleep(observe_s)
    end_dt = datetime.now()

    raw_logs: Dict[str, List[str]] = {}
    for port in ["COM12", "COM13", "COM14"]:
        raw_logs[port] = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
    clean_logs = sanitize_logs(raw_logs)
    write_phase_logs(artifact_dir, raw_logs, clean_logs)

    summary = {
        "action": "shell_setup_commands",
        "artifact_dir": str(artifact_dir),
        "success": True,
        "commands": commands,
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
    }
    save_json(artifact_dir / "summary.json", summary)
    return summary


def run_cloud_log_upload_probe_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    probe_case = build_cloud_log_probe_case(case, rounds=int(rules.get("probe_rounds", 2)))
    probe_rules = dict(rules)
    probe_rules.update(
        {
            "observe_after_ms": int(rules.get("observe_after_ms", 12000)),
            "min_cp_wake": int(rules.get("min_probe_cp_wake", 1)),
            "min_ap_wake": int(rules.get("min_probe_ap_wake", 1)),
            "min_ap_cloud_tts_play": int(rules.get("min_probe_cloud_tts_play", 1)),
            "required_online_asr_texts": [TEXT_TIME_QUERY],
        }
    )
    try:
        setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "01_wait_online"))
        clear_commands = list(rules.get("clear_commands", []))
        if clear_commands:
            setup_records.append(
                execute_shell_setup_commands(
                    execution_dir,
                    session_dir,
                    commands=clear_commands,
                    phase_root="setup",
                    label="02_clear_local_log_setting",
                )
            )
        setup_records.append(
            apply_cloud_log_setting(
                execution_dir,
                session_dir,
                status=int(rules["log_status"]),
                level=int(rules["log_level"]),
                phase_root="setup",
                label="03_set_cloud_log_upload",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 8.0)),
            )
        )
        payload = execute_standard_audio_case(probe_case, probe_rules, execution_dir, device_key, session_dir, tone_catalog)
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        if rules.get("restore_default_after", True):
            try:
                recovery_records.append(
                    apply_cloud_log_setting(
                        execution_dir,
                        session_dir,
                        status=0,
                        level=7,
                        phase_root="recovery",
                        label="01_restore_default_log_upload",
                        apply_wait_s=float(rules.get("recovery_apply_wait_s", 8.0)),
                    )
                )
            except Exception as recovery_exc:
                recovery_records.append(
                    {
                        "action": "cloud_log_setting_restore",
                        "artifact_dir": "",
                        "success": False,
                        "error": str(recovery_exc),
                    }
                )

    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records

    apply_summary = next((item for item in setup_records if item.get("action") == "cloud_log_setting"), None)
    apply_logs = read_clean_logs_from_artifact_dir(Path(apply_summary["artifact_dir"])) if apply_summary else {"COM12": [], "COM13": [], "COM14": []}
    change_records = extract_cloud_log_level_changes(apply_logs.get("COM14", []))
    matched_levels = [item["level"] for item in change_records if item["level"] == int(rules["expected_device_loglev"])]
    response_ok = bool(apply_summary) and cloud_response_ok(apply_summary.get("response", {}))
    interaction_metrics = payload["metrics"]

    payload["metrics"].update(
        {
            "cloud_log_change_count": len(change_records),
            "cloud_log_change_levels": [item["level"] for item in change_records],
            "cloud_log_change_sources": [item.get("source", "") for item in change_records],
            "cloud_log_expected_level_hits": len(matched_levels),
        }
    )

    checks = [
        {
            "name": "cloud_response_ok",
            "actual": response_ok,
            "expected": True,
            "passed": response_ok,
        },
        {
            "name": "expected_device_loglev",
            "actual": [item["level"] for item in change_records],
            "expected": int(rules["expected_device_loglev"]),
            "passed": len(matched_levels) >= 1,
        },
        {
            "name": "probe_cp_wake_count",
            "actual": interaction_metrics["cp_wake_count"],
            "expected": f">={probe_rules['min_cp_wake']}",
            "passed": interaction_metrics["cp_wake_count"] >= probe_rules["min_cp_wake"],
        },
        {
            "name": "probe_ap_cloud_tts_play_count",
            "actual": interaction_metrics["ap_cloud_tts_play_count"],
            "expected": f">={probe_rules['min_ap_cloud_tts_play']}",
            "passed": interaction_metrics["ap_cloud_tts_play_count"] >= probe_rules["min_ap_cloud_tts_play"],
        },
        {
            "name": "probe_online_asr_has_time_query",
            "actual": interaction_metrics["ap_online_asr_texts"],
            "expected": TEXT_TIME_QUERY,
            "passed": TEXT_TIME_QUERY in interaction_metrics["ap_online_asr_texts"],
        },
    ]

    if payload.get("setup_error"):
        diagnosis = payload["diagnosis"]
        failure_reason = diagnosis["reason"]
    elif not all(item["passed"] for item in checks):
        if response_ok and len(matched_levels) >= 1 and interaction_metrics["cp_wake_count"] == 0 and interaction_metrics["ap_cloud_tts_play_count"] == 0:
            failure_reason = (
                f"云端日志等级切换已生效：levels={payload['metrics']['cloud_log_change_levels']}，"
                f"sources={payload['metrics']['cloud_log_change_sources']}；"
                "但后续语音探测零唤醒、零在线 ASR、零云端播报，更像当前音频注入/设备听音链路问题，而不是日志等级断言问题。"
            )
        else:
            failure_reason = (
                f"本地日志上传切换未形成完整客观证据：cloud_change={payload['metrics']['cloud_log_change_levels']}，"
                f"sources={payload['metrics']['cloud_log_change_sources']}，"
                f"online_asr={interaction_metrics['ap_online_asr_texts']}，"
                f"cp_wake={interaction_metrics['cp_wake_count']}，ap_tts={interaction_metrics['ap_cloud_tts_play_count']}。"
            )
        diagnosis = {
            "result": "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": failure_reason,
            "checks": checks,
        }
    else:
        failure_reason = "本地已确认云端日志等级切换与交互日志生成，但最终仍缺少云端日志回捞/一致性比对闭环。"
        diagnosis = {
            "result": "BLOCKED",
            "confidence": rules.get("confidence", "medium"),
            "reason": failure_reason,
            "checks": checks,
        }

    diagnosis["checks"] = checks
    payload["diagnosis"] = diagnosis
    payload["judge_payload"] = {
        "case_id": case.case_id,
        "name": case.name,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "reason": diagnosis["reason"],
        "checks": checks,
        "metrics": payload["metrics"],
        "tone_names": {},
    }
    payload["fingerprint"] = {
        "case_id": case.case_id,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "cloud_log_change_levels": payload["metrics"]["cloud_log_change_levels"],
        "probe_online_asr_texts": interaction_metrics["ap_online_asr_texts"],
    }
    payload["failure_excerpt"] = (
        f"# {case.case_id}\n\n"
        f"- Result: `{diagnosis['result']}`\n"
        f"- Reason: {failure_reason}\n"
        f"- Cloud change levels: `{payload['metrics']['cloud_log_change_levels']}`\n"
        f"- AP online ASR: `{interaction_metrics['ap_online_asr_texts']}`\n"
        f"- AP cloud TTS count: `{interaction_metrics['ap_cloud_tts_play_count']}`\n"
    )
    return persist_standard_audio_case(case, execution_dir, payload)


def run_wakeup_audio_upload_probe_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    probe_case = build_wakeup_audio_upload_probe_case(
        case,
        rounds=int(rules.get("probe_rounds", 10)),
        gap_ms=int(rules.get("probe_gap_ms", 1800)),
        tail_silence_ms=int(rules.get("tail_silence_ms", 0)),
    )
    probe_rules = dict(rules)
    probe_rules.update(
        {
            "observe_after_ms": int(rules.get("observe_after_ms", 20000)),
            "min_cp_wake": int(rules.get("min_probe_cp_wake", 1)),
            "min_ap_wake": int(rules.get("min_probe_ap_wake", 1)),
        }
    )
    try:
        setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "01_wait_online"))
        setup_records.append(
            apply_cloud_wakeup_audio_upload_setting(
                execution_dir,
                session_dir,
                enable=bool(rules.get("enable_upload", True)),
                phase_root="setup",
                label="02_enable_wakeup_audio_upload",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 8.0)),
            )
        )
        payload = execute_standard_audio_case(probe_case, probe_rules, execution_dir, device_key, session_dir, tone_catalog)
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)

    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records

    clean_logs = read_clean_logs_from_artifact_dir(execution_dir)
    upload_events = extract_wakeup_upload_events(clean_logs.get("COM14", []))
    upload_responses = [item for item in upload_events if item.get("kind") == "response"]
    upload_sessions = [item for item in upload_events if item.get("kind") == "session"]
    uploading_records = [
        item
        for item in extract_wake_info_uploads(clean_logs.get("COM14", []))
        if int(item.get("payload", {}).get("params", {}).get("isUploadingFile", 0) or 0) == 1
    ]
    success_responses = [
        item
        for item in upload_responses
        if str(item.get("payload", {}).get("code", "")) == "200"
        and str(item.get("payload", {}).get("msg", "")).strip().lower() == "success"
    ]
    response_ok = any(
        cloud_response_ok(item.get("response", {}))
        for item in setup_records
        if item.get("action") == "cloud_wakeup_audio_upload"
    )

    payload["metrics"].update(
        {
            "wakeup_upload_event_count": len(upload_events),
            "wakeup_upload_session_count": len(upload_sessions),
            "wakeup_upload_response_count": len(upload_responses),
            "wakeup_upload_success_count": len(success_responses),
            "wake_info_is_uploading_count": len(uploading_records),
        }
    )

    checks = [
        {
            "name": "cloud_response_ok",
            "actual": response_ok,
            "expected": True,
            "passed": response_ok,
        },
        {
            "name": "probe_cp_wake_count",
            "actual": payload["metrics"]["cp_wake_count"],
            "expected": f">={probe_rules['min_cp_wake']}",
            "passed": payload["metrics"]["cp_wake_count"] >= probe_rules["min_cp_wake"],
        },
        {
            "name": "probe_ap_wake_count",
            "actual": payload["metrics"]["ap_wake_count"],
            "expected": f">={probe_rules['min_ap_wake']}",
            "passed": payload["metrics"]["ap_wake_count"] >= probe_rules["min_ap_wake"],
        },
        {
            "name": "wake_info_is_uploading_count",
            "actual": len(uploading_records),
            "expected": ">=1",
            "passed": len(uploading_records) >= 1,
        },
        {
            "name": "wakeup_upload_success_count",
            "actual": len(success_responses),
            "expected": ">=1",
            "passed": len(success_responses) >= 1,
        },
    ]

    if payload.get("setup_error"):
        diagnosis = payload["diagnosis"]
        failure_reason = diagnosis["reason"]
    elif not all(item["passed"] for item in checks):
        if payload["metrics"]["cp_wake_count"] == 0 and payload["metrics"]["ap_wake_count"] == 0:
            failure_reason = (
                f"唤醒音频上传开关已下发，但整段观测内没有任何 wake 证据：cp_wake={payload['metrics']['cp_wake_count']}，"
                f"ap_wake={payload['metrics']['ap_wake_count']}，sessions={len(upload_sessions)}。"
                " 当前更像音频注入/设备听音链路问题，上传逻辑尚未真正被触发。"
            )
        else:
            failure_reason = (
                f"本地唤醒音频上传链路未形成完整客观证据：sessions={len(upload_sessions)}，"
                f"success_responses={len(success_responses)}，isUploadingFile={len(uploading_records)}。"
            )
        diagnosis = {
            "result": "FAIL",
            "confidence": rules.get("confidence", "medium"),
            "reason": failure_reason,
            "checks": checks,
        }
    else:
        failure_reason = "本地已确认唤醒音频上传开关生效且设备产生上传成功日志，但最终仍缺少云端音频回捞与格式/内容校验闭环。"
        diagnosis = {
            "result": "BLOCKED",
            "confidence": rules.get("confidence", "medium"),
            "reason": failure_reason,
            "checks": checks,
        }

    diagnosis["checks"] = checks
    payload["diagnosis"] = diagnosis
    payload["judge_payload"] = {
        "case_id": case.case_id,
        "name": case.name,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "reason": diagnosis["reason"],
        "checks": checks,
        "metrics": payload["metrics"],
        "tone_names": {},
    }
    payload["fingerprint"] = {
        "case_id": case.case_id,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "wakeup_upload_success_count": len(success_responses),
        "wake_info_is_uploading_count": len(uploading_records),
    }
    payload["failure_excerpt"] = (
        f"# {case.case_id}\n\n"
        f"- Result: `{diagnosis['result']}`\n"
        f"- Reason: {failure_reason}\n"
        f"- Wakeup upload sessions: `{len(upload_sessions)}`\n"
        f"- Wakeup upload success count: `{len(success_responses)}`\n"
        f"- Wake info isUploadingFile count: `{len(uploading_records)}`\n"
    )
    return persist_standard_audio_case(case, execution_dir, payload)


def execute_proactive_phase(
    execution_dir: Path,
    session_dir: Path,
    *,
    phase_root: str,
    label: str,
    interrupt: bool,
    end_session: bool,
    observe_s: float,
) -> dict:
    artifact_dir = execution_dir / phase_root / label
    artifact_dir.mkdir(parents=True, exist_ok=True)
    wait_for_device_online(session_dir, artifact_dir / "00_wait_online")

    deviceinfo_capture = capture_cloud_deviceinfo(session_dir)
    deviceinfo = deviceinfo_capture["parsed"]
    (artifact_dir / "deviceinfo.log").write_text("\n".join(deviceinfo_capture["lines"]) + "\n", encoding="utf-8")
    save_json(artifact_dir / "deviceinfo.json", deviceinfo)

    start_dt = datetime.now()
    request = build_cloud_request(deviceinfo)
    response = request.Proactive_interaction(
        interrupt="True" if interrupt else "False",
        endssion="Ture" if end_session else "False",
        tts_long="False",
    )
    time.sleep(observe_s)
    end_dt = datetime.now()

    raw_logs: Dict[str, List[str]] = {}
    for port in ["COM12", "COM13", "COM14"]:
        raw_logs[port] = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
    clean_logs = sanitize_logs(raw_logs)
    write_phase_logs(artifact_dir, raw_logs, clean_logs)

    window_summary = summarize_window(clean_logs)
    metrics = collect_metrics(clean_logs, window_summary)
    response_dict = cloud_response_to_dict(response)
    save_json(artifact_dir / "response.json", response_dict)
    payload = {
        "phase_id": label,
        "artifact_dir": str(artifact_dir),
        "interrupt": bool(interrupt),
        "end_session": bool(end_session),
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "ended_at": end_dt.isoformat(timespec="milliseconds"),
        "response": response_dict,
        "response_ok": cloud_response_ok(response_dict),
        "window_summary": window_summary,
        "metrics": metrics,
    }
    save_json(artifact_dir / "phase_result.json", payload)
    return payload


def run_app_proactive_mic_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    del device_key, tone_catalog
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    phase_results: List[dict] = []
    phase_gap_s = float(rules.get("phase_gap_s", 1.0))
    observe_s = float(rules.get("observe_per_phase_s", 6.0))
    combos = [(False, False), (True, False), (False, True), (True, True)]

    try:
        setup_records.append(
            apply_cloud_mic_switch(
                execution_dir,
                session_dir,
                enable=False,
                phase_root="setup",
                label="01_set_mic_off",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        for index, (interrupt, end_session) in enumerate(combos, start=1):
            phase = execute_proactive_phase(
                execution_dir,
                session_dir,
                phase_root="phases",
                label=f"{index:02d}_mic_off_interrupt_{int(interrupt)}_end_{int(end_session)}",
                interrupt=interrupt,
                end_session=end_session,
                observe_s=observe_s,
            )
            checks = [
                {
                    "name": "cloud_response_ok",
                    "actual": phase["response"],
                    "expected": "business success",
                    "passed": bool(phase["response_ok"]),
                },
                {
                    "name": "ap_cloud_tts_play_count",
                    "actual": phase["metrics"]["ap_cloud_tts_play_count"],
                    "expected": "<=0",
                    "passed": phase["metrics"]["ap_cloud_tts_play_count"] <= 0,
                },
                {
                    "name": "ap_ignore_broadcast_count",
                    "actual": phase["metrics"]["ap_ignore_broadcast_count"],
                    "expected": ">=1",
                    "passed": phase["metrics"]["ap_ignore_broadcast_count"] >= 1,
                },
            ]
            phase["checks"] = checks
            phase["result"] = "PASS" if all(item["passed"] for item in checks) else "FAIL"
            phase["reason"] = "mic_off proactive should stay silent"
            phase_results.append(phase)
            time.sleep(phase_gap_s)

        setup_records.append(
            apply_cloud_mic_switch(
                execution_dir,
                session_dir,
                enable=True,
                phase_root="setup",
                label="02_set_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        for offset, (interrupt, end_session) in enumerate(combos, start=1):
            phase = execute_proactive_phase(
                execution_dir,
                session_dir,
                phase_root="phases",
                label=f"{offset + len(combos):02d}_mic_on_interrupt_{int(interrupt)}_end_{int(end_session)}",
                interrupt=interrupt,
                end_session=end_session,
                observe_s=observe_s,
            )
            checks = [
                {
                    "name": "cloud_response_ok",
                    "actual": phase["response"],
                    "expected": "business success",
                    "passed": bool(phase["response_ok"]),
                },
                {
                    "name": "ap_cloud_tts_play_count",
                    "actual": phase["metrics"]["ap_cloud_tts_play_count"],
                    "expected": ">=1",
                    "passed": phase["metrics"]["ap_cloud_tts_play_count"] >= 1,
                },
            ]
            phase["checks"] = checks
            phase["result"] = "PASS" if all(item["passed"] for item in checks) else "FAIL"
            phase["reason"] = "mic_on proactive should play"
            phase_results.append(phase)
            time.sleep(phase_gap_s)
    finally:
        try:
            recovery_records.append(
                apply_cloud_mic_switch(
                    execution_dir,
                    session_dir,
                    enable=True,
                    phase_root="recovery",
                    label="01_restore_mic_on",
                    apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 6.0))),
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_mic_switch",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "enable": True,
                }
            )

    failed_phases = [phase for phase in phase_results if phase["result"] != "PASS"]
    diagnosis = {
        "result": "FAIL" if failed_phases else "PASS",
        "confidence": rules.get("confidence", "medium"),
        "reason": failed_phases[0]["phase_id"] if failed_phases else rules["notes"],
    }
    fingerprint = {
        "case_id": case.case_id,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "phase_results": {phase["phase_id"]: phase["result"] for phase in phase_results},
    }
    judge_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "result": diagnosis["result"],
        "confidence": diagnosis["confidence"],
        "reason": diagnosis["reason"],
        "phases": phase_results,
    }
    excerpt = build_dialog_case_excerpt(case, diagnosis, phase_results, {})

    (execution_dir / "judge.json").write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "failure_excerpt.md").write_text(excerpt, encoding="utf-8")
    result_payload = {
        "case_id": case.case_id,
        "name": case.name,
        "execution_dir": str(execution_dir),
        "diagnosis": diagnosis,
        "phases": phase_results,
        "setup": setup_records,
        "recovery": recovery_records,
        "artifacts": {
            "judge": str(execution_dir / "judge.json"),
            "fingerprint": str(execution_dir / "fingerprint.json"),
            "failure_excerpt": str(execution_dir / "failure_excerpt.md"),
        },
    }
    result_path = execution_dir / "doc_case_result.json"
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (execution_dir / "doc_case_summary.md").write_text(excerpt, encoding="utf-8")
    return result_path


def run_app_wakeup_word_persist_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    probe_case = replace(case, tokens=[StepToken(kind="Wakeup", channel="talk", value=str(rules["probe_text"]))])

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        setup_records.append(
            apply_cloud_wakeup_word(
                execution_dir,
                session_dir,
                wakeup_word=str(rules["target_wakeup_word"]),
                phase_root="setup",
                label="01_set_wakeup_word",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
            )
        )
        setup_records.append(
            cycle_case_power_target(
                execution_dir,
                session_dir,
                target="wb01",
                phase_root="setup",
                label="02_power_cycle_wb01",
                off_wait_s=float(rules.get("power_off_wait_s", 2.0)),
                observe_s=float(rules.get("power_observe_s", 20.0)),
            )
        )
        payload = execute_standard_audio_case(probe_case, rules, execution_dir, device_key, session_dir, tone_catalog)
    except Exception as exc:
        payload = build_blocked_case_payload(probe_case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        recovery_word = str(rules.get("recovery_wakeup_word", "小美小美")).strip()
        try:
            recovery_records.append(
                apply_cloud_wakeup_word(
                    execution_dir,
                    session_dir,
                    wakeup_word=recovery_word,
                    phase_root="recovery",
                    label="01_restore_wakeup_word",
                    apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_wakeup_word",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "wakeup_word": recovery_word,
                }
            )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def run_app_threshold_persist_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    gap_ms = int(rules.get("double_wake_gap_ms", 1500))
    probe_case = replace(
        case,
        tokens=[
            StepToken(kind="Wakeup", channel="talk", value=str(rules["probe_text"])),
            StepToken(kind="Action", channel="sleep", value=str(gap_ms)),
            StepToken(kind="Wakeup", channel="talk", value=str(rules["probe_text"])),
        ],
    )

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        setup_records.append(
            apply_cloud_wakeup_threshold(
                execution_dir,
                session_dir,
                threshold=int(rules["pre_threshold_high"]),
                phase_root="setup",
                label="01_set_threshold_high",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
            )
        )
        setup_records.append(
            apply_cloud_wakeup_threshold(
                execution_dir,
                session_dir,
                threshold=int(rules["pre_threshold_low"]),
                phase_root="setup",
                label="02_set_threshold_low",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
            )
        )
        setup_records.append(
            cycle_case_power_target(
                execution_dir,
                session_dir,
                target="wb01",
                phase_root="setup",
                label="03_power_cycle_wb01",
                off_wait_s=float(rules.get("power_off_wait_s", 2.0)),
                observe_s=float(rules.get("power_observe_s", 20.0)),
            )
        )
        payload = execute_standard_audio_case(probe_case, rules, execution_dir, device_key, session_dir, tone_catalog)
        clean_logs = read_clean_logs_from_execution(execution_dir)
        diagnosis, threshold_info = evaluate_threshold_case(
            case,
            rules,
            payload["metrics"],
            clean_logs,
            setup_records=setup_records,
        )
        payload["diagnosis"] = diagnosis
        payload["judge_payload"] = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "checks": diagnosis["checks"],
            "metrics": payload["metrics"],
            "threshold_hits": threshold_info["threshold_hits"],
            "target_thresholds": threshold_info["target_thresholds"],
            "primary_thresholds": threshold_info["primary_thresholds"],
            "setup_info": threshold_info["setup_info"],
            "tone_names": {str(tone_id): tone_catalog.get(tone_id, "unknown") for tone_id in payload["metrics"]["tone_ids"]},
        }
        fingerprint = build_fingerprint(case, payload["metrics"], diagnosis)
        fingerprint["thresholds"] = threshold_info["target_thresholds"]
        fingerprint["primary_thresholds"] = threshold_info["primary_thresholds"]
        fingerprint["threshold_request_value"] = threshold_info["threshold_request_value"]
        fingerprint["threshold_setup_values"] = threshold_info["setup_info"].get("get_threshold_values", [])
        payload["fingerprint"] = fingerprint
        payload["failure_excerpt"] = build_threshold_case_excerpt(
            case,
            diagnosis,
            payload["metrics"],
            tone_catalog,
            clean_logs,
            threshold_info,
        )
    except Exception as exc:
        payload = build_blocked_case_payload(probe_case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        recovery_threshold = int(rules.get("recovery_threshold", 50))
        try:
            recovery_records.append(
                apply_cloud_wakeup_threshold(
                    execution_dir,
                    session_dir,
                    threshold=recovery_threshold,
                    phase_root="recovery",
                    label="01_restore_wakeup_threshold",
                    apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_wakeup_threshold",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "threshold": recovery_threshold,
                }
            )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def run_app_offline_timeout_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    env = load_env_config()
    device_mac = str(env.get("current_deviceinfo", {}).get("mac", "")).strip()
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    target_wakeup_word = str(rules.get("target_wakeup_word", "")).strip()
    recovery_wakeup_word = str(rules.get("recovery_wakeup_word", "小美小美")).strip()
    try:
        setup_records.append(prepare_local_hotspot_attachment(execution_dir, session_dir, device_mac=device_mac))
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="01_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        if target_wakeup_word:
            setup_records.append(
                apply_cloud_wakeup_word(
                    execution_dir,
                    session_dir,
                    wakeup_word=target_wakeup_word,
                    phase_root="setup",
                    label="02_set_wakeup_word",
                    apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
                )
            )
        setup_records.append(
            apply_cloud_full_duplex(
                execution_dir,
                session_dir,
                timeout_seconds=int(rules["timeout_seconds"]),
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
            )
        )
        setup_records.append(
            toggle_case_hotspot_state(
                execution_dir,
                session_dir,
                device_mac=device_mac,
                enable=False,
                wait_s=float(rules.get("disconnect_wait_s", 15.0)),
                phase_root="setup",
                label="03_hotspot_offline",
            )
        )
        payload = execute_standard_audio_case(case, rules, execution_dir, device_key, session_dir, tone_catalog)
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        try:
            recovery_records.append(
                toggle_case_hotspot_state(
                    execution_dir,
                    session_dir,
                    device_mac=device_mac,
                    enable=True,
                    wait_s=float(rules.get("reconnect_wait_s", 60.0)),
                    phase_root="recovery",
                    label="01_hotspot_online",
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "hotspot_on",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                }
            )
        if target_wakeup_word:
            try:
                recovery_records.append(
                    apply_cloud_wakeup_word(
                        execution_dir,
                        session_dir,
                        wakeup_word=recovery_wakeup_word,
                        phase_root="recovery",
                        label="02_restore_wakeup_word",
                        apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                    )
                )
            except Exception as recovery_exc:
                recovery_records.append(
                    {
                        "action": "cloud_wakeup_word",
                        "artifact_dir": "",
                        "success": False,
                        "error": str(recovery_exc),
                        "wakeup_word": recovery_wakeup_word,
                    }
                )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def run_app_wakeup_word_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    probe_case = replace(case, tokens=[StepToken(kind="Wakeup", channel="talk", value=str(rules["probe_text"]))])

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        setup_records.append(
            apply_cloud_wakeup_word(
                execution_dir,
                session_dir,
                wakeup_word=str(rules["target_wakeup_word"]),
                phase_root="setup",
                label="01_set_wakeup_word",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
            )
        )
        payload = execute_standard_audio_case(probe_case, rules, execution_dir, device_key, session_dir, tone_catalog)
    except Exception as exc:
        payload = build_blocked_case_payload(probe_case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        recovery_word = str(rules.get("recovery_wakeup_word", "小美小美")).strip()
        try:
            recovery_records.append(
                apply_cloud_wakeup_word(
                    execution_dir,
                    session_dir,
                    wakeup_word=recovery_word,
                    phase_root="recovery",
                    label="01_restore_wakeup_word",
                    apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_wakeup_word",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "wakeup_word": recovery_word,
                }
            )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def run_app_threshold_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    gap_ms = int(rules.get("double_wake_gap_ms", 1500))
    probe_text = str(rules["probe_text"])
    probe_case = replace(
        case,
        tokens=[
            StepToken(kind="Wakeup", channel="talk", value=probe_text),
            StepToken(kind="Action", channel="sleep", value=str(gap_ms)),
            StepToken(kind="Wakeup", channel="talk", value=probe_text),
        ],
    )

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        setup_records.append(
            apply_cloud_wakeup_word(
                execution_dir,
                session_dir,
                wakeup_word=str(rules["target_wakeup_word"]),
                phase_root="setup",
                label="01_set_wakeup_word",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
            )
        )
        setup_records.append(
            apply_cloud_wakeup_threshold(
                execution_dir,
                session_dir,
                threshold=int(rules["threshold_request"]),
                phase_root="setup",
                label="02_set_wakeup_threshold",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 12.0)),
            )
        )
        payload = execute_standard_audio_case(probe_case, rules, execution_dir, device_key, session_dir, tone_catalog)
        clean_logs = read_clean_logs_from_execution(execution_dir)
        diagnosis, threshold_info = evaluate_threshold_case(
            case,
            rules,
            payload["metrics"],
            clean_logs,
            setup_records=setup_records,
        )
        payload["diagnosis"] = diagnosis
        payload["judge_payload"] = {
            "case_id": case.case_id,
            "name": case.name,
            "result": diagnosis["result"],
            "confidence": diagnosis["confidence"],
            "reason": diagnosis["reason"],
            "checks": diagnosis["checks"],
            "metrics": payload["metrics"],
            "threshold_hits": threshold_info["threshold_hits"],
            "target_thresholds": threshold_info["target_thresholds"],
            "primary_thresholds": threshold_info["primary_thresholds"],
            "setup_info": threshold_info["setup_info"],
            "tone_names": {str(tone_id): tone_catalog.get(tone_id, "unknown") for tone_id in payload["metrics"]["tone_ids"]},
        }
        fingerprint = build_fingerprint(case, payload["metrics"], diagnosis)
        fingerprint["thresholds"] = threshold_info["target_thresholds"]
        fingerprint["primary_thresholds"] = threshold_info["primary_thresholds"]
        fingerprint["threshold_request_value"] = threshold_info["threshold_request_value"]
        fingerprint["threshold_setup_values"] = threshold_info["setup_info"].get("get_threshold_values", [])
        payload["fingerprint"] = fingerprint
        payload["failure_excerpt"] = build_threshold_case_excerpt(
            case,
            diagnosis,
            payload["metrics"],
            tone_catalog,
            clean_logs,
            threshold_info,
        )
    except Exception as exc:
        payload = build_blocked_case_payload(probe_case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        recovery_threshold = int(rules.get("recovery_threshold", 50))
        recovery_word = str(rules.get("recovery_wakeup_word", "小美小美")).strip()
        try:
            recovery_records.append(
                apply_cloud_wakeup_threshold(
                    execution_dir,
                    session_dir,
                    threshold=recovery_threshold,
                    phase_root="recovery",
                    label="01_restore_wakeup_threshold",
                    apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_wakeup_threshold",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "threshold": recovery_threshold,
                }
            )
        try:
            recovery_records.append(
                apply_cloud_wakeup_word(
                    execution_dir,
                    session_dir,
                    wakeup_word=recovery_word,
                    phase_root="recovery",
                    label="02_restore_wakeup_word",
                    apply_wait_s=float(rules.get("cloud_recovery_wait_s", rules.get("cloud_apply_wait_s", 12.0))),
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "cloud_wakeup_word",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                    "wakeup_word": recovery_word,
                }
            )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def run_online_stress_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    probe_case, stress_metadata = build_online_stress_probe_case(case, rules)
    (execution_dir / "stress_plan.json").write_text(json.dumps(stress_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    effective_rules = dict(rules)
    default_cycles = stress_metadata.get("default_cycles")
    effective_cycles = int(stress_metadata["cycles"])
    if default_cycles and int(default_cycles) > 0 and effective_cycles != int(default_cycles):
        ratio = effective_cycles / int(default_cycles)
        for key in [
            "min_cp_wake",
            "min_cp_command",
            "min_ap_wake",
            "min_wb_online_wake",
            "min_ap_cloud_tts_play",
            "min_ap_instruction_broadcast",
            "min_interrupt_reset_count",
        ]:
            if key not in effective_rules:
                continue
            effective_rules[key] = max(1, int(round(float(effective_rules[key]) * ratio)))

    try:
        setup_records.append(
            ensure_cloud_mic_on_baseline(
                execution_dir,
                session_dir,
                phase_root="setup",
                label="00_ensure_mic_on",
                apply_wait_s=float(rules.get("cloud_apply_wait_s", 6.0)),
            )
        )
        setup_records.append(wait_for_device_online(session_dir, execution_dir / "setup" / "01_wait_online_before_stress"))
        payload = execute_standard_audio_case(probe_case, effective_rules, execution_dir, device_key, session_dir, tone_catalog)
        diagnosis = evaluate_case_with_rules(probe_case, payload["metrics"], effective_rules)
        payload["diagnosis"] = diagnosis
        payload["judge_payload"]["result"] = diagnosis["result"]
        payload["judge_payload"]["confidence"] = diagnosis["confidence"]
        payload["judge_payload"]["reason"] = diagnosis["reason"]
        payload["judge_payload"]["checks"] = diagnosis["checks"]
        payload["fingerprint"] = build_fingerprint(probe_case, payload["metrics"], diagnosis)
        payload["failure_excerpt"] = build_excerpt(
            probe_case,
            diagnosis,
            payload["metrics"],
            tone_catalog,
            read_clean_logs_from_execution(execution_dir),
        )
        payload["judge_payload"]["stress"] = {
            "scenario": stress_metadata["scenario"],
            "cycles": stress_metadata["cycles"],
            "default_cycles": stress_metadata["default_cycles"],
            "seed": stress_metadata["seed"],
            "override_env": stress_metadata["override_env"],
        }
        payload["fingerprint"]["stress"] = {
            "scenario": stress_metadata["scenario"],
            "cycles": stress_metadata["cycles"],
            "seed": stress_metadata["seed"],
        }
        payload["failure_excerpt"] += (
            "\n## Stress Plan\n\n"
            f"- scenario: `{stress_metadata['scenario']}`\n"
            f"- cycles: `{stress_metadata['cycles']}`\n"
            f"- seed: `{stress_metadata['seed']}`\n"
            f"- override_env: `{stress_metadata['override_env'] or '<none>'}`\n"
        )
    except Exception as exc:
        payload = build_blocked_case_payload(probe_case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)

    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)


def run_offline_audio_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    env = load_env_config()
    device_mac = str(env.get("current_deviceinfo", {}).get("mac", "")).strip()
    payload: Optional[dict] = None
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    try:
        setup_records.append(prepare_local_hotspot_attachment(execution_dir, session_dir, device_mac=device_mac))
        setup_records.append(
            toggle_case_hotspot_state(
                execution_dir,
                session_dir,
                device_mac=device_mac,
                enable=False,
                wait_s=float(rules.get("disconnect_wait_s", 15.0)),
                phase_root="setup",
                label="02_hotspot_offline",
            )
        )
        payload = execute_standard_audio_case(case, rules, execution_dir, device_key, session_dir, tone_catalog)
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
    finally:
        try:
            recovery_records.append(
                toggle_case_hotspot_state(
                    execution_dir,
                    session_dir,
                    device_mac=device_mac,
                    enable=True,
                    wait_s=float(rules.get("reconnect_wait_s", 60.0)),
                    phase_root="recovery",
                    label="01_hotspot_online",
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "hotspot_on",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                }
            )
    assert payload is not None
    payload["setup"] = setup_records
    payload["recovery"] = recovery_records
    return persist_standard_audio_case(case, execution_dir, payload)



def run_offline_dialog_phase_case(case, rules: dict, execution_dir: Path, device_key: str, session_dir: Path, tone_catalog: dict) -> Path:
    env = load_env_config()
    device_mac = str(env.get("current_deviceinfo", {}).get("mac", "")).strip()
    setup_records: List[dict] = []
    recovery_records: List[dict] = []
    result_path: Optional[Path] = None
    try:
        setup_records.append(prepare_local_hotspot_attachment(execution_dir, session_dir, device_mac=device_mac))
        setup_records.append(
            toggle_case_hotspot_state(
                execution_dir,
                session_dir,
                device_mac=device_mac,
                enable=False,
                wait_s=float(rules.get("disconnect_wait_s", 15.0)),
                phase_root="setup",
                label="02_hotspot_offline",
            )
        )
        result_path = run_dialog_phase_case(
            case=case,
            rules=rules,
            execution_dir=execution_dir,
            device_key=device_key,
            session_dir=session_dir,
            tone_catalog=tone_catalog,
        )
    except Exception as exc:
        payload = build_blocked_case_payload(case, rules, str(exc), tone_catalog)
        payload["setup_error"] = str(exc)
        result_path = persist_standard_audio_case(case, execution_dir, payload)
    finally:
        try:
            recovery_records.append(
                toggle_case_hotspot_state(
                    execution_dir,
                    session_dir,
                    device_mac=device_mac,
                    enable=True,
                    wait_s=float(rules.get("reconnect_wait_s", 60.0)),
                    phase_root="recovery",
                    label="01_hotspot_online",
                )
            )
        except Exception as recovery_exc:
            recovery_records.append(
                {
                    "action": "hotspot_on",
                    "artifact_dir": "",
                    "success": False,
                    "error": str(recovery_exc),
                }
            )
    assert result_path is not None
    result_payload = json.loads(result_path.read_text(encoding="utf-8"))
    result_payload["setup"] = setup_records
    result_payload["recovery"] = recovery_records
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return result_path



def run_doc_case(case_id: str, device_key: str = "") -> Path:
    case = load_doc_case(case_id)
    rules = SUPPORTED_DOC_CASES[case.case_id]
    session_dir = current_session_dir()
    lock_path = session_dir / ".case_runner.lock"
    lock_fd = None
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode("ascii", errors="ignore"))
    except FileExistsError as exc:
        raise RuntimeError(f"another case runner is already active: {lock_path}") from exc

    try:
        execution_dir = new_artifact_dir(f"doc_case_run_{case.case_id}", session_dir)
        tone_catalog = parse_tone_catalog()
        shutil.copy2(default_doc_xlsx(), execution_dir / "doc_cases.xlsx")
        (execution_dir / "doc_case.json").write_text(json.dumps(case.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        resolved_device_key = str(device_key or default_playback_device_key(current_runtime_env_config())).strip()
        if rules["runner_kind"] == "dialog_phase_case" and case.level3 == MODE_OFFLINE:
            return run_offline_dialog_phase_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "dialog_phase_case":
            return run_dialog_phase_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_offline_timeout_case":
            return run_app_offline_timeout_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_mic_case":
            return run_app_mic_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_dialog_config_case":
            return run_app_dialog_config_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_dialog_announce_case":
            return run_app_dialog_announce_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_dialog_persist_case":
            return run_app_dialog_persist_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_accent_case":
            return run_app_accent_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_accent_persist_case":
            return run_app_accent_persist_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_proactive_mic_case":
            return run_app_proactive_mic_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_wakeup_word_case":
            return run_app_wakeup_word_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_wakeup_word_persist_case":
            return run_app_wakeup_word_persist_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_threshold_case":
            return run_app_threshold_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "app_threshold_persist_case":
            return run_app_threshold_persist_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "wake_info_upload_case":
            return run_wake_info_upload_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "algo_version_upload_case":
            return run_algo_version_upload_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "power_broadcast_case":
            return run_power_broadcast_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "network_disconnect_case":
            return run_network_disconnect_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "network_reconnect_voice_case":
            return run_network_reconnect_voice_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "online_empty_nlu_case":
            return run_online_empty_nlu_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "cloud_log_upload_probe_case":
            return run_cloud_log_upload_probe_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "wakeup_audio_upload_probe_case":
            return run_wakeup_audio_upload_probe_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "online_stress_case":
            return run_online_stress_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] == "serial_only" and case.level3 == MODE_OFFLINE:
            return run_serial_only_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )
        if rules["runner_kind"] in {"offline_voice", "offline_interrupt_voice", "serial_only"} and case.level3 == MODE_OFFLINE:
            return run_offline_audio_case(
                case=case,
                rules=rules,
                execution_dir=execution_dir,
                device_key=resolved_device_key,
                session_dir=session_dir,
                tone_catalog=tone_catalog,
            )

        payload = execute_standard_audio_case(
            case=case,
            rules=rules,
            execution_dir=execution_dir,
            device_key=resolved_device_key,
            session_dir=session_dir,
            tone_catalog=tone_catalog,
        )
        return persist_standard_audio_case(case, execution_dir, payload)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if lock_path.exists():
            lock_path.unlink(missing_ok=True)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one doc case directly from doc xlsx")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--device-key", default="")
    return parser



def main() -> None:
    args = build_parser().parse_args()
    result_path = run_doc_case(args.case_id, device_key=args.device_key)
    print(result_path)


if __name__ == "__main__":
    main()
