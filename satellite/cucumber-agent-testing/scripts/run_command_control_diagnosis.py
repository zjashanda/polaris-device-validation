#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a control-variable diagnosis matrix for one command failure.

The runner is intentionally deterministic: each case changes one variable
only, records serial/audio/cloud evidence, and leaves attribution to the
generated summary instead of forcing an expected failure to look like PASS.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from polaris_env import load_env_payload, resolve_env_path  # noqa: E402
from runtime.snapshots import (  # noqa: E402
    build_snapshot,
    diff_snapshots,
    write_coverage as write_snapshot_coverage,
    write_diff as write_snapshot_diff,
    write_snapshot,
)
from tools.audio.polaris_audio_builder import build_sequence  # noqa: E402
from tools.core.polaris_adapter_bridge import (  # noqa: E402
    action_result_to_step,
    run_adapter_action_capture,
    run_audio_playback_adapter,
    start_serial_logger_adapter,
    stop_serial_logger_adapter,
)
from tools.core.polaris_runtime import read_lines_between  # noqa: E402
from tools.logs.polaris_interaction_trace import extract_interaction_trace  # noqa: E402

try:  # noqa: E402
    from pypinyin import lazy_pinyin  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    lazy_pinyin = None  # type: ignore


WAKE_RE = re.compile(r"(online_wakeup|offline_wakeup|wakeup_callback|Pre Wakeup|mark has wakeup)", re.I)
ASR_RE = re.compile(r"(cloud asr with|Recv .* ASR|online_asr_callbak|offline_asr_callbak|cloud\.speech\.trans\.ack|MSpeech Cloud 3 evt|recognizer start)", re.I)
ASR_TEXT_RE = re.compile(r"(?:online|offline)_asr_callbak,\s*(?:text|keyword):\s*(.+)$", re.I)
CLOUD_ASR_TEXT_RE = re.compile(r'"asr"\s*:\s*"([^"]*)"', re.I)
COMMAND_KEYWORD_RE = re.compile(r"(?:WAKE\(0\).*?KEY=\d+\(([^)]*)\)|\"keyword\"\s*:\s*\"([^\"]*)\")", re.I)
COMMAND_RESPONSE_RE = re.compile(
    r"(cloud\.speech\.reply|cloud\.instructions\.audioBroadcast|TTS (?:recv|playing) with https?://|ttsplayer play:|play audio https?://|\"status\":\"play\")",
    re.I,
)
DEVICE_CONTROL_RESPONSE_RE = re.compile(
    r"(cloud\.speech\.reply|cloud\.instructions\.audioBroadcast|MSpeech Cloud (?:4|32) evt|"
    r"mideaSkillId:\s*DeviceControl|\"mideaSkillId\"\s*:\s*\"DeviceControl\"|\"skillId\"\s*:\s*\"11042\")",
    re.I,
)
EMPTY_TTS_URL_RE = re.compile(r"empty\d*\.mp3", re.I)
NULL_TTS_RE = re.compile(r"(TTS url is null|TTS recv with\s*(?:\"|$)|TTS play error with\s*$)", re.I)
NO_VALID_TTS_RE = re.compile(r"no valid tts url", re.I)
VALID_TTS_URL_RE = re.compile(r"(?:https?://|\"url\"\s*:\s*\"https?://|TTS (?:recv|playing) with https?://|play audio https?://)", re.I)
RESPONSE_TEXT_RE = re.compile(r'"text"\s*:\s*"([^"]+)"', re.I)
MEDIA_STATE_RE = re.compile(r"device state recv, class: media\(6\), state:\s*1", re.I)
WAKE_TTS_RE = re.compile(r"(wakeup_tts_callback|shortplayer|tone player)", re.I)
EXPLICIT_BEEP_RE = re.compile(r"(beep|buzzer|蜂鸣)", re.I)
ACTUATOR_WEAK_RE = re.compile(r"(actuator|appliance|device\s+ack|control\s+ack)", re.I)
BEEP_OR_ACTUATOR_RE = re.compile(r"(beep|buzzer|蜂鸣|actuator|appliance|device\s+ack|control\s+ack)", re.I)
MEDIA_ERROR_RE = re.compile(
    r"(\[E\].*# http.*(recv timeout|retry|fail|error)|"
    r"\[HTTPC\]\[ERR\]|"
    r"\[W\].*http_retry.*(read_failed|retry)|"
    r"\b(http|https).*(download|demux|play).*(fail|error|timeout)|"
    r"\b(demux|download|decoder|player).*(fail|error|timeout))",
    re.I,
)
BOOT_RE = re.compile(r"(Boot Reason|boot reason|ASSERT|panic|fatal|watchdog|hard fault|exception|reboot_reason)", re.I)
BOOT_IGNORE_RE = re.compile(r"ignore exception", re.I)
MOJIBAKE_HINT_RE = re.compile(r"[\u00e5\u00e6\u00e7\u00e4\u00e9\u00e8\u00e3\u00ef\ufffd\u6c13\u5fd9\u83bd\u76f2\u8305\u732b\u832b\u8302\u951f\u7d94]" r"|(\u677c\u626e\u77c6|\u5a11\u65bf\u724a|\u59b2\u6401\u5d37|\u9361\u6b91|\u93cd\u3127\u6443|\u8f70\u7c88|\u6d94\u581f|\u69f8\u9366|\u55d9\u6b91|\u6828\u74d5)")
PUNCT_OR_SPACE_RE = re.compile(r"[\s，。！？、,.!?:：;；\"'“”‘’（）()\[\]{}<>《》]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LINE_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)")
ALIAS_CONFIG_PATH = BDD_ROOT / "references" / "command_aliases" / "fa2_command_aliases.json"
_ALIAS_CONFIG: Optional[Dict[str, Any]] = None
LATENCY_FIELDS = [
    "wake_to_recognition_ms",
    "wake_to_cloud_request_ms",
    "wake_to_first_cloud_response_ms",
    "wake_to_tts_start_ms",
    "wake_to_media_start_ms",
    "cloud_request_to_recognition_ms",
    "recognition_to_cloud_request_ms",
    "cloud_request_to_first_cloud_response_ms",
    "cloud_request_to_audio_broadcast_ms",
    "cloud_request_to_speech_reply_ms",
    "cloud_request_to_tts_start_ms",
    "cloud_request_to_media_start_ms",
    "recognition_to_first_cloud_response_ms",
    "recognition_to_audio_broadcast_ms",
    "recognition_to_speech_reply_ms",
    "recognition_to_tts_start_ms",
    "recognition_to_media_start_ms",
    "first_cloud_response_to_tts_start_ms",
    "first_cloud_response_to_media_start_ms",
    "audio_broadcast_to_tts_start_ms",
    "audio_broadcast_to_media_start_ms",
    "tts_start_to_media_start_ms",
    "tts_or_media_play_duration_ms",
]


@dataclass
class DiagnosisCase:
    case_id: str
    mode: str
    command: str
    delay_ms: int
    observe_ms: int
    variable: str
    expected: str = ""
    timeout_s: int = 15
    sequence_mode: str = "split"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def current_session_marker() -> Path:
    return WORKSPACE_ROOT / ".current_result_dir"


def install_session_marker(session_dir: Path) -> Dict[str, Any]:
    marker = current_session_marker()
    previous = marker.read_text(encoding="utf-8") if marker.exists() else None
    marker.write_text(str(session_dir.resolve()), encoding="utf-8")
    return {"path": marker, "previous": previous}


def restore_session_marker(state: Optional[Dict[str, Any]]) -> None:
    if not state:
        return
    marker = Path(state["path"])
    previous = state.get("previous")
    if previous is None:
        marker.unlink(missing_ok=True)
    else:
        marker.write_text(str(previous), encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def text_value(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def nested(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def first_latency_value(metrics: Dict[str, Any], key: str) -> Any:
    for sample in metrics.get("latency_samples") or []:
        latency = sample.get("latency", {}) if isinstance(sample, dict) else {}
        if isinstance(latency, dict) and latency.get(key) not in (None, ""):
            return latency.get(key)
    return ""


def configured_ports(env_payload: Dict[str, Any]) -> Dict[str, str]:
    ports = nested(env_payload, "serial", "ports")
    ports = ports if isinstance(ports, dict) else {}
    result: Dict[str, str] = {}
    for role in ("ap", "cp", "asr", "upper"):
        value = text_value(ports.get(role))
        if value and value not in result.values():
            result[role] = value
    return result


def configured_log_port_specs(env_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return configured log ports, preserving role aliases for duplicated ports."""
    ports = nested(env_payload, "serial", "ports")
    ports = ports if isinstance(ports, dict) else {}
    by_port: Dict[str, Dict[str, Any]] = {}
    for role in ("cp", "asr", "upper", "ap"):
        port = text_value(ports.get(role))
        if not port:
            continue
        spec = by_port.setdefault(port, {"port": port, "roles": []})
        if role not in spec["roles"]:
            spec["roles"].append(role)
    return list(by_port.values())


def parse_required_roles(raw: str, env_payload: Dict[str, Any]) -> List[str]:
    value = raw.strip()
    if value:
        return [item.strip().lower() for item in re.split(r"[,;，；\s]+", value) if item.strip()]
    configured = nested(env_payload, "serial", "required_log_roles")
    if isinstance(configured, list):
        return [str(item).strip().lower() for item in configured if str(item).strip()]
    if isinstance(configured, str):
        return [item.strip().lower() for item in re.split(r"[,;，；\s]+", configured) if item.strip()]
    return []


def read_heartbeat(session_dir: Path) -> Dict[str, Any]:
    heartbeat = session_dir / "logs" / "live" / "heartbeat.json"
    if not heartbeat.exists():
        return {}
    try:
        return json.loads(heartbeat.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def evaluate_serial_coverage(
    env_payload: Dict[str, Any],
    session_dir: Path,
    *,
    required_roles: List[str],
    wait_s: float = 4.0,
) -> Dict[str, Any]:
    """Classify serial evidence as full, degraded, or blocked from heartbeat state."""
    specs = configured_log_port_specs(env_payload)
    deadline = time.time() + max(0.0, wait_s)
    heartbeat: Dict[str, Any] = {}
    while time.time() <= deadline:
        heartbeat = read_heartbeat(session_dir)
        hb_ports = heartbeat.get("ports", {}) if isinstance(heartbeat.get("ports"), dict) else {}
        if hb_ports and all(str(spec.get("port")) in hb_ports for spec in specs):
            if all((hb_ports.get(str(spec.get("port")), {}) or {}).get("is_open") for spec in specs):
                break
            if all((hb_ports.get(str(spec.get("port")), {}) or {}).get("last_error") for spec in specs):
                break
        time.sleep(0.25)

    hb_ports = heartbeat.get("ports", {}) if isinstance(heartbeat.get("ports"), dict) else {}
    available: List[Dict[str, Any]] = []
    unavailable: List[Dict[str, Any]] = []
    for spec in specs:
        port = str(spec.get("port") or "")
        state = hb_ports.get(port, {}) if isinstance(hb_ports.get(port, {}), dict) else {}
        entry = {
            "port": port,
            "roles": spec.get("roles", []),
            "heartbeat_role": state.get("role", ""),
            "is_open": bool(state.get("is_open")),
            "last_error": state.get("last_error"),
            "bytes_read": state.get("bytes_read", 0),
            "lines_written": state.get("lines_written", 0),
        }
        if entry["is_open"]:
            available.append(entry)
        else:
            unavailable.append(entry)

    missing_required = [
        item
        for item in unavailable
        if any(str(role).lower() in required_roles for role in item.get("roles", []))
    ]
    if not specs:
        status = "BLOCKED"
        reason = "未配置任何日志串口，不能采集串口证据。"
    elif not available:
        status = "BLOCKED"
        reason = "所有已配置日志串口都未打开，不能进入功能分母。"
    elif missing_required:
        status = "BLOCKED"
        reason = "必需日志串口未打开，按 BLOCKED 处理，避免把降级证据当完整覆盖。"
    elif unavailable:
        status = "COVERAGE_DEGRADED"
        reason = "部分已配置日志串口未打开，本轮仅能基于可用串口给出降级结论。"
    else:
        status = "FULL"
        reason = "所有已配置日志串口均已打开。"
    return {
        "status": status,
        "reason": reason,
        "expected_ports": specs,
        "available_ports": available,
        "unavailable_ports": unavailable,
        "missing_required_ports": missing_required,
        "required_roles": required_roles,
        "heartbeat_path": str(session_dir / "logs" / "live" / "heartbeat.json"),
        "heartbeat_ts": heartbeat.get("ts", ""),
    }


def mojibake_variants(text: str) -> List[str]:
    raw = str(text or "")
    variants = [raw]
    for encoding in ("latin1", "cp1252"):
        try:
            recovered = raw.encode(encoding).decode("utf-8")
        except Exception:
            continue
        if recovered and recovered not in variants:
            variants.append(recovered)
    return variants


def normalize_text(text: str) -> str:
    return PUNCT_OR_SPACE_RE.sub("", str(text or "").lower())


def comparable_variants(text: str) -> List[str]:
    variants = list(mojibake_variants(text))
    if lazy_pinyin is not None:
        for item in list(variants):
            if CJK_RE.search(item):
                try:
                    pinyin = " ".join(lazy_pinyin(item))  # type: ignore[misc]
                except Exception:
                    pinyin = ""
                if pinyin and pinyin not in variants:
                    variants.append(pinyin)
                if "调" in item and pinyin:
                    tiao_pinyin = re.sub(r"\bdiao\b", "tiao", pinyin)
                    if tiao_pinyin and tiao_pinyin not in variants:
                        variants.append(tiao_pinyin)
                if "除湿" in item and pinyin:
                    chou_pinyin = pinyin.replace("chu shi", "chou shi")
                    if chou_pinyin and chou_pinyin not in variants:
                        variants.append(chou_pinyin)
    return [normalize_text(item) for item in variants if normalize_text(item)]


def text_matches(observed: str, expected_values: Iterable[str]) -> bool:
    observed_norms = comparable_variants(observed)
    if not observed_norms:
        return False
    for expected in expected_values:
        expected_norms = comparable_variants(expected)
        for observed_norm in observed_norms:
            for expected_norm in expected_norms:
                if observed_norm == expected_norm or observed_norm in expected_norm or expected_norm in observed_norm:
                    return True
    return False


def looks_like_mojibake(text: str) -> bool:
    return bool(MOJIBAKE_HINT_RE.search(str(text or "")))


def collect_regex(lines: Iterable[str], regex: re.Pattern[str], limit: int = 12) -> Tuple[int, List[str]]:
    count = 0
    samples: List[str] = []
    for line in lines:
        if regex.search(line):
            count += 1
            if len(samples) < limit:
                samples.append(line)
    return count, samples


def line_timestamp(line: str) -> Optional[datetime]:
    match = LINE_TS_RE.match(str(line or ""))
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group("ts"))
    except ValueError:
        return None


def lines_from_boundary(lines: List[str], boundary: Optional[datetime]) -> List[str]:
    if boundary is None:
        return lines
    result: List[str] = []
    for line in lines:
        ts = line_timestamp(line)
        if ts is None or ts >= boundary:
            result.append(line)
    return result


def is_media_error(line: str) -> bool:
    if "PA_MGR" in line and "Refresh PA to OFF" in line:
        return False
    return bool(MEDIA_ERROR_RE.search(line))


def is_boot_line(line: str) -> bool:
    return bool(BOOT_RE.search(line)) and not BOOT_IGNORE_RE.search(line)


def is_device_control_expected(text: str) -> bool:
    normalized = normalize_text(text)
    return any(token in normalized for token in ("空调", "制冷", "制热", "开机", "关机", "打开", "关闭", "模式"))


def classify_command_kind(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return "unknown"
    if normalized.startswith("查询"):
        return "query"
    if "语音配网" in normalized or "联网状态" in normalized:
        return "network_query_or_setup"
    if "音量" in normalized:
        return "volume_control"
    if "定时" in normalized:
        return "timer_control"
    if "温度" in normalized or "度" in normalized or normalized.startswith(("调高", "调低")):
        return "temperature_control"
    if "风速" in normalized or "风道" in normalized or "摆风" in normalized or "风向" in normalized or normalized.endswith("风"):
        return "airflow_control"
    if "模式" in normalized or normalized in {"制冷模式", "制热模式", "自动模式", "送风模式", "抽湿模式", "关闭模式"}:
        return "mode_control"
    if normalized in {"空调开机", "空调关机", "打开空调", "关闭空调"}:
        return "power_control"
    if normalized.startswith(("打开", "关闭")):
        return "feature_toggle"
    return "device_control" if is_device_control_expected(text) else "online_or_general"


def extract_response_texts(lines: Iterable[str]) -> List[str]:
    values: List[str] = []
    for line in lines:
        if not DEVICE_CONTROL_RESPONSE_RE.search(line) and "audioBroadcast" not in line:
            continue
        for match in RESPONSE_TEXT_RE.finditer(line):
            text = match.group(1).strip()
            if text and text not in values:
                values.append(text)
    return values


def infer_control_effect(command: str, response_texts: Iterable[str]) -> str:
    kind = classify_command_kind(command)
    joined = normalize_text(" ".join(response_texts))
    if kind in {"query", "network_query_or_setup"}:
        return "response_only"
    if not joined:
        return "unknown"
    if any(token in joined for token in ("不支持", "暂不", "不能", "无法", "失败", "请先", "没有此功能", "未开机")):
        return "rejected_or_unsupported"
    if any(token in joined for token in ("已经", "已是", "当前", "现在已经", "无需")):
        return "no_state_change"
    if any(token in joined for token in ("好的", "收到", "已开机", "已关机", "已打开", "已关闭", "已切换", "已调", "已设置", "已为")):
        return "state_change_or_accepted"
    return "unknown"


def actuator_expectation(command: str, control_effect: str) -> str:
    kind = classify_command_kind(command)
    if kind in {"query", "network_query_or_setup", "online_or_general"}:
        return "not_required"
    if control_effect in {"response_only", "no_state_change", "rejected_or_unsupported"}:
        return "not_expected"
    if kind in {
        "power_control",
        "mode_control",
        "temperature_control",
        "airflow_control",
        "feature_toggle",
        "timer_control",
        "volume_control",
        "device_control",
    }:
        return "expected_if_state_changes"
    return "unknown"


def load_alias_config() -> Dict[str, Any]:
    global _ALIAS_CONFIG
    if _ALIAS_CONFIG is not None:
        return _ALIAS_CONFIG
    try:
        _ALIAS_CONFIG = json.loads(ALIAS_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        _ALIAS_CONFIG = {}
    return _ALIAS_CONFIG


def _apply_regex_template(match: re.Match[str], template: str) -> str:
    result = template
    for index, value in enumerate(match.groups(), start=1):
        result = result.replace("{" + str(index) + "}", value or "")
    return result


def expand_expected_values(values: Iterable[str]) -> List[str]:
    expanded: List[str] = []

    def add(value: str) -> None:
        text = str(value or "").strip()
        if text and text not in expanded:
            expanded.append(text)

    config = load_alias_config()
    exact_aliases = config.get("exact_aliases", {}) if isinstance(config.get("exact_aliases"), dict) else {}
    replacements = config.get("contains_replacements", []) if isinstance(config.get("contains_replacements"), list) else []
    regex_aliases = config.get("regex_aliases", []) if isinstance(config.get("regex_aliases"), list) else []

    for value in values:
        add(value)
        normalized = normalize_text(value)
        for key, aliases in exact_aliases.items():
            alias_values = aliases if isinstance(aliases, list) else [aliases]
            normalized_aliases = [normalize_text(item) for item in [key] + [str(alias) for alias in alias_values]]
            if normalized in normalized_aliases:
                add(str(key))
                for alias in alias_values:
                    add(str(alias))
        for rule in replacements:
            if not isinstance(rule, dict):
                continue
            contains = str(rule.get("contains", ""))
            replace = str(rule.get("replace", ""))
            if contains and contains in str(value):
                add(str(value).replace(contains, replace))
        for rule in regex_aliases:
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("pattern", ""))
            template = str(rule.get("template", ""))
            if not pattern or not template:
                continue
            try:
                match = re.match(pattern, str(value))
            except re.error:
                match = None
            if match:
                add(_apply_regex_template(match, template))

        # Built-in fallbacks keep older runs stable if the JSON alias file is absent.
        if normalized in {"打开空调", "空调开机"}:
            add("打开空调")
            add("空调开机")
        if normalized in {"关闭空调", "空调关机"}:
            add("关闭空调")
            add("空调关机")
        if normalized == "关闭模式":
            add("取消模式")
        if "风向" in str(value):
            add(str(value).replace("风向", "向"))
        if "除湿" in str(value):
            add(str(value).replace("除湿", "抽湿"))
        if "主动防冷风" in str(value):
            add(str(value).replace("主动防冷风", "自动防冷风"))
        if "全域风" in str(value):
            add(str(value).replace("全域风", "全面风"))
        if "节能省电" in str(value):
            if normalized.startswith("打开"):
                add("打开eco")
            elif normalized.startswith("关闭"):
                add("关闭eco")
            else:
                add("eco")
        match = re.match(r"^(打开|关闭)(左风道|右风道)(.+)$", str(value))
        if match:
            add(f"{match.group(2)}{match.group(1)}{match.group(3)}")
    return expanded


def extract_asr_texts(lines: Iterable[str]) -> List[str]:
    values: List[str] = []
    for line in lines:
        for regex in (ASR_TEXT_RE, CLOUD_ASR_TEXT_RE):
            for match in regex.finditer(line):
                text = match.group(1).strip()
                if text and text not in values:
                    values.append(text)
    return values


def extract_keywords(lines: Iterable[str]) -> List[str]:
    values: List[str] = []
    for line in lines:
        for match in COMMAND_KEYWORD_RE.finditer(line):
            text = text_value(match.group(1), match.group(2))
            if text and "xiao mei xiao mei" not in text.lower() and text not in values:
                values.append(text)
    return values


def summarize_window(lines_by_role: Dict[str, List[str]], expected_text: str, command_text: str = "") -> Dict[str, Any]:
    all_lines: List[str] = []
    for lines in lines_by_role.values():
        all_lines.extend(lines)
    wake_count, wake_samples = collect_regex(all_lines, WAKE_RE)
    wake_times = [line_timestamp(line) for line in all_lines if WAKE_RE.search(line)]
    wake_times = [item for item in wake_times if item is not None]
    response_boundary = min(wake_times) if wake_times else None
    post_wake_lines = lines_from_boundary(all_lines, response_boundary)
    asr_count, asr_samples = collect_regex(post_wake_lines, ASR_RE)
    response_lines = [line for line in post_wake_lines if COMMAND_RESPONSE_RE.search(line) and not WAKE_TTS_RE.search(line)]
    response_count = len(response_lines)
    response_samples = response_lines[:12]
    device_control_samples = [line for line in post_wake_lines if DEVICE_CONTROL_RESPONSE_RE.search(line)]
    empty_tts_url_samples = [line for line in response_lines if EMPTY_TTS_URL_RE.search(line)]
    valid_tts_url_samples = [line for line in response_lines if VALID_TTS_URL_RE.search(line)]
    null_tts_samples = [line for line in post_wake_lines if NULL_TTS_RE.search(line)]
    no_valid_tts_samples = [line for line in post_wake_lines if NO_VALID_TTS_RE.search(line)]
    media_state_samples = [line for line in post_wake_lines if MEDIA_STATE_RE.search(line)]
    actuator_samples = [line for line in post_wake_lines if BEEP_OR_ACTUATOR_RE.search(line)]
    explicit_beep_samples = [line for line in post_wake_lines if EXPLICIT_BEEP_RE.search(line)]
    weak_actuator_samples = [line for line in post_wake_lines if ACTUATOR_WEAK_RE.search(line)]
    media_error_samples = [line for line in post_wake_lines if is_media_error(line)]
    boot_samples = [line for line in post_wake_lines if is_boot_line(line)]
    asr_texts = extract_asr_texts(post_wake_lines)
    keywords = extract_keywords(post_wake_lines)
    response_texts = extract_response_texts(post_wake_lines)
    interaction_trace = extract_interaction_trace(post_wake_lines)
    control_effect = infer_control_effect(command_text or expected_text, response_texts)
    raw_expected_values = []
    for item in (expected_text, command_text):
        if item and item not in raw_expected_values:
            raw_expected_values.append(item)
    expected_values = expand_expected_values(raw_expected_values)
    matched_texts = [text for text in asr_texts if text_matches(text, expected_values)]
    matched_keywords = [text for text in keywords if text_matches(text, expected_values)]
    unexpected_texts = [
        text
        for text in asr_texts
        if expected_values and not text_matches(text, expected_values) and not (matched_texts and looks_like_mojibake(text))
    ]
    return {
        "line_count": len(all_lines),
        "role_line_counts": {role: len(lines) for role, lines in lines_by_role.items()},
        "wake_count": wake_count,
        "response_boundary": response_boundary.isoformat(timespec="milliseconds") if response_boundary else "",
        "asr_event_count": asr_count,
        "command_response_count": response_count,
        "device_control_response_count": len(device_control_samples),
        "empty_tts_url_count": len(empty_tts_url_samples),
        "valid_tts_url_count": len(valid_tts_url_samples),
        "null_tts_count": len(null_tts_samples),
        "no_valid_tts_count": len(no_valid_tts_samples),
        "media_state_start_count": len(media_state_samples),
        "actuator_marker_count": len(actuator_samples),
        "explicit_beep_marker_count": len(explicit_beep_samples),
        "weak_actuator_marker_count": len(weak_actuator_samples),
        "media_error_count": len(media_error_samples),
        "boot_or_crash_count": len(boot_samples),
        "asr_texts": asr_texts,
        "asr_pinyin_texts": [
            str(item.get("asr_pinyin", ""))
            for item in interaction_trace.get("recognition_events", [])
            if str(item.get("asr_pinyin", "")).strip()
        ],
        "matched_asr_texts": matched_texts,
        "unexpected_asr_texts": unexpected_texts,
        "command_keywords": keywords,
        "matched_command_keywords": matched_keywords,
        "response_texts": response_texts,
        "interaction_trace": interaction_trace,
        "online_request_ids": interaction_trace.get("online_request_ids", []),
        "latency_samples": interaction_trace.get("latency_samples", []),
        "command_kind": classify_command_kind(command_text or expected_text),
        "control_effect": control_effect,
        "actuator_expectation": actuator_expectation(command_text or expected_text, control_effect),
        "samples": {
            "wake": wake_samples,
            "asr": asr_samples,
            "command_response": response_samples,
            "device_control_response": device_control_samples[:12],
            "empty_tts_url": empty_tts_url_samples[:12],
            "valid_tts_url": valid_tts_url_samples[:12],
            "null_tts": null_tts_samples[:12],
            "no_valid_tts": no_valid_tts_samples[:12],
            "media_state_start": media_state_samples[:12],
            "actuator_marker": actuator_samples[:12],
            "explicit_beep_marker": explicit_beep_samples[:12],
            "weak_actuator_marker": weak_actuator_samples[:12],
            "media_error": media_error_samples[:12],
            "boot_or_crash": boot_samples[:12],
        },
    }


def build_chain_segments(metrics: Dict[str, Any], playback_returncode: int, expected_text: str) -> Dict[str, Any]:
    """Return a segmented oracle so control success is not confused with TTS/beep success."""
    command_kind = str(metrics.get("command_kind") or classify_command_kind(expected_text))
    is_device_control = is_device_control_expected(expected_text) or command_kind not in {"online_or_general", "unknown"}
    recognition_proof = bool(metrics.get("matched_asr_texts") or metrics.get("matched_command_keywords"))
    if playback_returncode != 0:
        recognition = {
            "result": "BLOCKED",
            "reason": "播放链路失败，无法判断识别。",
            "evidence": [],
        }
    elif metrics.get("wake_count", 0) <= 0:
        recognition = {
            "result": "FAIL",
            "reason": "没有 wake marker。",
            "evidence": metrics.get("samples", {}).get("wake", []),
        }
    elif recognition_proof:
        recognition = {
            "result": "PASS",
            "reason": "命中 ASR 文本或本地命令关键词。",
            "evidence": list(metrics.get("matched_asr_texts", [])) + list(metrics.get("matched_command_keywords", [])),
        }
    elif metrics.get("asr_event_count", 0) > 0:
        recognition = {
            "result": "WARN",
            "reason": "有 ASR 事件但缺少可直接命中本轮命令的文本/关键词，需结合控制回复判断。",
            "evidence": metrics.get("samples", {}).get("asr", []),
        }
    else:
        recognition = {
            "result": "FAIL",
            "reason": "已唤醒但没有 ASR 事件。",
            "evidence": metrics.get("samples", {}).get("asr", []),
        }

    if not is_device_control:
        control_reply = {
            "result": "NOT_REQUIRED",
            "reason": "非设备控制命令，不要求 DeviceControl 控制回复。",
            "evidence": metrics.get("samples", {}).get("command_response", []),
        }
    elif metrics.get("device_control_response_count", 0) > 0:
        control_reply = {
            "result": "PASS",
            "reason": "观察到 DeviceControl/audioBroadcast/cloud.speech.reply 控制回复。",
            "response_texts": metrics.get("response_texts", []),
            "control_effect": metrics.get("control_effect", "unknown"),
            "evidence": metrics.get("samples", {}).get("device_control_response", []),
        }
    elif metrics.get("matched_command_keywords"):
        control_reply = {
            "result": "WARN",
            "reason": "只有本地命令关键词，缺少云端 DeviceControl 回复；可能是离线/本地控制或云端回复不可观测。",
            "response_texts": metrics.get("response_texts", []),
            "control_effect": metrics.get("control_effect", "unknown"),
            "evidence": metrics.get("matched_command_keywords", []),
        }
    else:
        control_reply = {
            "result": "FAIL",
            "reason": "缺少 DeviceControl 控制回复或本地控制关键词，不能证明控制链路生效。",
            "response_texts": metrics.get("response_texts", []),
            "control_effect": metrics.get("control_effect", "unknown"),
            "evidence": [],
        }

    tts_missing = (
        metrics.get("null_tts_count", 0) > 0
        or metrics.get("empty_tts_url_count", 0) > 0
        or metrics.get("no_valid_tts_count", 0) > 0
    )
    if metrics.get("valid_tts_url_count", 0) > 0 or (metrics.get("media_state_start_count", 0) > 0 and not tts_missing):
        tts_response = {
            "result": "PASS",
            "reason": "观察到有效 TTS/媒体 URL 或本轮媒体播放状态。",
            "evidence": metrics.get("samples", {}).get("valid_tts_url", []) + metrics.get("samples", {}).get("media_state_start", []),
        }
    elif metrics.get("response_texts") and tts_missing:
        tts_response = {
            "result": "FAIL",
            "reason": "云端返回响应文本，但 TTS URL 为空或 no valid tts url，播报播放链路未闭环。",
            "response_texts": metrics.get("response_texts", []),
            "evidence": metrics.get("samples", {}).get("null_tts", []) + metrics.get("samples", {}).get("no_valid_tts", []) + metrics.get("samples", {}).get("empty_tts_url", []),
        }
    elif metrics.get("response_texts"):
        tts_response = {
            "result": "WARN",
            "reason": "有响应文本但未观察到有效播放证据；可能需要声学回采或播放器 marker 佐证。",
            "response_texts": metrics.get("response_texts", []),
            "evidence": metrics.get("samples", {}).get("command_response", []),
        }
    elif is_device_control:
        tts_response = {
            "result": "UNKNOWN",
            "reason": "没有可判定的 TTS/播报响应证据。",
            "evidence": [],
        }
    else:
        tts_response = {
            "result": "NOT_REQUIRED",
            "reason": "非播报类控制命令暂不强制 TTS 断言。",
            "evidence": [],
        }

    expectation = str(metrics.get("actuator_expectation") or actuator_expectation(expected_text, str(metrics.get("control_effect", "unknown"))))
    if expectation == "not_required":
        actuator_feedback = {
            "result": "NOT_REQUIRED",
            "reason": "查询/媒体/通用在线命令不要求执行机构或蜂鸣器反馈。",
            "expectation": expectation,
            "evidence_type": "none",
            "evidence": [],
        }
    elif expectation == "not_expected":
        actuator_feedback = {
            "result": "NOT_EXPECTED",
            "reason": "响应语义表明 no-op/查询/不支持/拒绝类结果，蜂鸣器或状态变化不应作为必需断言。",
            "expectation": expectation,
            "evidence_type": "response_semantics",
            "evidence": metrics.get("response_texts", []),
        }
    elif metrics.get("explicit_beep_marker_count", 0) > 0:
        actuator_feedback = {
            "result": "PASS",
            "reason": "观察到明确 beep/buzzer/蜂鸣器 marker。",
            "expectation": expectation,
            "evidence_type": "serial_explicit_beep",
            "evidence": metrics.get("samples", {}).get("explicit_beep_marker", []),
        }
    elif metrics.get("weak_actuator_marker_count", 0) > 0:
        actuator_feedback = {
            "result": "WARN",
            "reason": "仅观察到弱执行/设备 ACK marker，不能等价证明蜂鸣器已响。",
            "expectation": expectation,
            "evidence_type": "serial_weak_actuator",
            "evidence": metrics.get("samples", {}).get("weak_actuator_marker", []),
        }
    else:
        actuator_feedback = {
            "result": "UNKNOWN",
            "reason": "当前自动化没有捕获到明确蜂鸣器/执行机构反馈；若人工确认或接入声学回采，可补 actuator evidence。",
            "expectation": expectation,
            "evidence_type": "missing_oracle",
            "evidence": [],
        }

    return {
        "command_kind": command_kind,
        "recognition": recognition,
        "control_reply": control_reply,
        "tts_response": tts_response,
        "actuator_feedback": actuator_feedback,
    }


def classify_case(metrics: Dict[str, Any], playback_returncode: int, expected_text: str) -> Tuple[str, str, str]:
    if playback_returncode != 0:
        return "BLOCKED", "播放链路失败，不能进入功能分母。", "audio_playback"
    if metrics["line_count"] <= 0:
        return "BLOCKED", "窗口内没有串口日志。", "serial_logger"
    if metrics["boot_or_crash_count"] > 0:
        return "FAIL", "窗口内出现 reboot/crash/watchdog 类标记。", "device_stability"
    if metrics["wake_count"] <= 0:
        return "FAIL", "播放成功但没有 wake marker。", "wake_or_audio"
    if metrics["asr_event_count"] <= 0:
        return "FAIL", "已唤醒但没有 ASR 事件，优先定位全双工监听窗口/注入时序/设备 ASR。", "asr_entry"
    target_proof = bool(metrics.get("matched_asr_texts") or metrics.get("matched_command_keywords"))
    if metrics["unexpected_asr_texts"] and not target_proof:
        return "FAIL", "观察到非本轮语料的 ASR 文本，且未命中目标命令，按误识别处理。", "unexpected_recognition"
    is_device_control = is_device_control_expected(expected_text)
    if is_device_control:
        has_command_proof = bool(
            metrics["matched_asr_texts"]
            or metrics.get("matched_command_keywords")
            or metrics.get("device_control_response_count", 0) > 0
        )
        if has_command_proof:
            if metrics["unexpected_asr_texts"]:
                return "PASS_WITH_WARNINGS", "目标命令已命中，但窗口内还有额外识别结果，需记录为误识别候选并复核时序。", "unexpected_recognition_warning"
            tts_missing = (
                metrics.get("null_tts_count", 0) > 0
                or metrics.get("empty_tts_url_count", 0) > 0
                or metrics.get("no_valid_tts_count", 0) > 0
            )
            if metrics["media_error_count"] > 0 or tts_missing:
                return "PASS_WITH_WARNINGS", "识别和 DeviceControl 控制链路有证据，但 TTS/播报未完整闭环，需要单独归因播报链路。", "tts_response_chain"
            return "PASS", "空调/设备控制有 ASR 文本、命令关键词或 DeviceControl 云端回复证据。", "none"
        if metrics.get("empty_tts_url_count", 0) > 0:
            return "FAIL", "仅观察到 empty TTS URL，缺少 ASR 文本、命令关键词或 DeviceControl 回复，不能证明空调控制生效。", "device_control_oracle_gap"
    has_command_proof = bool(
        metrics["matched_asr_texts"]
        or metrics.get("matched_command_keywords")
        or metrics["command_response_count"] > 0
        or metrics.get("media_state_start_count", 0) > 0
    )
    if has_command_proof:
        if metrics["unexpected_asr_texts"]:
            return "PASS_WITH_WARNINGS", "目标命令已命中，但窗口内还有额外识别结果，需记录为误识别候选并复核时序。", "unexpected_recognition_warning"
        if metrics["media_error_count"] > 0:
            return "PASS_WITH_WARNINGS", "识别/响应链路有证据，但媒体/TTS 出现错误标记。", "media_chain"
        return "PASS", "wake、ASR 事件和命令/响应证据闭环。", "none"
    if metrics.get("null_tts_count", 0) > 0:
        return "FAIL", "仅观察到空 TTS/null TTS，缺少在线 ASR 文本、命令关键词、有效媒体状态或有效响应 URL。", "command_domain_or_oracle"
    if expected_text:
        return "FAIL", "有 ASR 事件但未命中目标文本、命令关键词或响应。", "command_domain_or_cloud_skill"
    return "FAIL", "有 ASR 事件但缺少可判定响应。", "oracle_missing"


def default_matrix() -> List[DiagnosisCase]:
    return [
        DiagnosisCase("M01_half_open_1500", "half", "打开空调", 1500, 12000, "baseline_mode"),
        DiagnosisCase("M02_full_open_1500", "full", "打开空调", 1500, 12000, "mode_full_duplex"),
        DiagnosisCase("M03_full_open_2500", "full", "打开空调", 2500, 12000, "delay_after_wake"),
        DiagnosisCase("M04_full_close_1500", "full", "关闭空调", 1500, 12000, "command_domain"),
        DiagnosisCase("M05_full_music_1500", "full", "播放音乐", 1500, 12000, "online_media_command"),
        DiagnosisCase("M06_full_news_1500", "full", "播报今日新闻", 1500, 14000, "online_news_command"),
    ]


def load_matrix(path: Path) -> List[DiagnosisCase]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = payload.get("matrix", payload if isinstance(payload, list) else [])
    if not isinstance(rows, list):
        raise SystemExit("matrix file must be a list or contain matrix=[...]")
    result: List[DiagnosisCase] = []
    for item in rows:
        result.append(
            DiagnosisCase(
                case_id=str(item["case_id"]),
                mode=str(item.get("mode", "full")),
                command=str(item["command"]),
                delay_ms=int(item.get("delay_ms", 1500)),
                observe_ms=int(item.get("observe_ms", 12000)),
                variable=str(item.get("variable", "")),
                expected=str(item.get("expected", item.get("command", ""))),
                timeout_s=int(item.get("timeout_s", 15)),
                sequence_mode=str(item.get("sequence_mode", "split")),
            )
        )
    return result


def set_mode(mode: str, timeout_s: int, env_file: str, out_dir: Path) -> Dict[str, Any]:
    if mode not in {"full", "half"}:
        return {"name": f"set_mode_{mode}", "result": "SKIPPED", "reason": "mode does not require cloud switch"}
    result = run_adapter_action_capture(
        adapter_id="cloud.api",
        action="set_full_duplex",
        params={"env_file": env_file, "enable": "1" if mode == "full" else "0", "timeout": str(timeout_s)},
        env_file=env_file,
        timeout_s=90,
        execute=True,
        allow_side_effects=True,
        log_path=out_dir / f"set_mode_{mode}.log",
    )
    return action_result_to_step(f"set_mode_{mode}", result)


def run_preflight(env_file: str, device_key: str, out_dir: Path) -> Dict[str, Any]:
    actions: List[Dict[str, Any]] = []
    env_payload = load_env_payload(resolve_env_path(env_file, WORKSPACE_ROOT))
    plan = []
    if text_value(nested(env_payload, "cloud", "device_env_command")):
        plan.append(("switch_device_env", "serial.ap", "set_device_env", {}, 20))
    else:
        actions.append({"name": "switch_device_env", "result": "SKIPPED", "reason": "cloud.device_env_command is empty"})
    plan.extend([
        ("audio_ensure_laid", "audio.playback", "ensure_laid", {}, 60),
        ("audio_probe", "audio.playback", "probe", {}, 60),
    ])
    preconditions = nested(env_payload, "serial", "control_preconditions")
    commands: List[str] = []
    if isinstance(preconditions, list):
        commands = [str(item).strip() for item in preconditions if str(item).strip()]
    elif isinstance(preconditions, str) and preconditions.strip():
        commands = [item.strip() for item in re.split(r"[,;，；\n]+", preconditions) if item.strip()]
    if not commands:
        commands = ["uut-pa.on", "pa-enable.set 0 17 0 1"]
    for index, command in enumerate(commands, start=1):
        plan.append((f"control_precondition_{index:02d}", "control.serial", "send_control", {"command": command}, 20))
    for name, adapter_id, action, params, timeout_s in plan:
        try:
            result = run_adapter_action_capture(
                adapter_id=adapter_id,
                action=action,
                params=params,
                env_file=env_file,
                device_key=device_key,
                timeout_s=timeout_s,
                execute=True,
                allow_side_effects=True,
                log_path=out_dir / f"{name}.log",
            )
            step = action_result_to_step(name, result)
        except Exception as exc:
            step = {"name": name, "result": "BLOCKED", "reason": str(exc)}
        actions.append(step)
        if name == "switch_device_env" and step.get("result") not in {"PASS", "PLAN_OK", "SKIPPED"}:
            # If AP cannot be opened, the run cannot collect valid DUT evidence.
            # Stop early so PA/control retries do not add long, noisy blockers.
            break
    hard_fail = [item for item in actions if item.get("result") not in {"PASS", "PLAN_OK"}]
    return {"result": "PASS" if not hard_fail else "BLOCKED", "actions": actions}


def build_case_audio(case: DiagnosisCase, wake_text: str, audio_file: Path) -> Dict[str, Any]:
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    if case.sequence_mode == "oneshot":
        sequence = [{"type": "tts", "text": f"{wake_text}，{case.command}"}]
    else:
        sequence = [
            {"type": "tts", "text": wake_text},
            {"type": "silence", "duration_ms": case.delay_ms},
            {"type": "tts", "text": case.command},
        ]
    return build_sequence(sequence, audio_file)


def collect_case_logs(ports: Dict[str, str], start_dt: datetime, end_dt: datetime, session_dir: Path, out_dir: Path) -> Dict[str, List[str]]:
    logs_dir = out_dir / "window_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, List[str]] = {}
    for role, port in ports.items():
        lines = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
        result[role] = lines
        (logs_dir / f"{role}_{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return result


def run_case(
    case: DiagnosisCase,
    *,
    wake_text: str,
    device_key: str,
    env_file: str,
    session_dir: Path,
    ports: Dict[str, str],
    out_root: Path,
    serial_coverage: Dict[str, Any],
) -> Dict[str, Any]:
    case_dir = out_root / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    setup = set_mode(case.mode, case.timeout_s, env_file, case_dir)
    if setup.get("result") not in {"PASS", "PLAN_OK", "SKIPPED"}:
        payload = {
            **asdict(case),
            "result": "BLOCKED",
            "reason": "模式切换失败，不能进入功能分母。",
            "attribution": "cloud_or_config",
            "setup": setup,
            "serial_coverage": serial_coverage,
        }
        write_json(case_dir / "result.json", payload)
        return payload
    time.sleep(1.0)
    audio_file = case_dir / "audio" / f"{case.case_id}.wav"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_manifest = build_case_audio(case, wake_text, audio_file)
    start_dt = datetime.now()
    playback = run_audio_playback_adapter(
        audio_file,
        device_key,
        skip_probe=True,
        timeout_s=120,
        env_file=env_file,
        stream_log_path=case_dir / "playback.log",
    )
    time.sleep(case.observe_ms / 1000.0)
    end_dt = datetime.now()
    lines_by_role = collect_case_logs(ports, start_dt, end_dt, session_dir, case_dir)
    expected_text = case.expected or case.command
    metrics = summarize_window(lines_by_role, expected_text, case.command)
    chain_segments = build_chain_segments(metrics, playback.completed.returncode, expected_text)
    result, reason, attribution = classify_case(metrics, playback.completed.returncode, expected_text)
    payload = {
        **asdict(case),
        "expected": expected_text,
        "started_at": start_dt.isoformat(timespec="milliseconds"),
        "finished_at": end_dt.isoformat(timespec="milliseconds"),
        "result": result,
        "reason": reason,
        "attribution": attribution,
        "setup": setup,
        "audio_file": str(audio_file),
        "audio_manifest": audio_manifest,
        "playback": {
            "returncode": playback.completed.returncode,
            "started_at": playback.started_at.isoformat(timespec="milliseconds"),
            "playback_started_at": playback.playback_started_at.isoformat(timespec="milliseconds"),
            "finished_at": playback.finished_at.isoformat(timespec="milliseconds"),
            "stdout_tail": playback.stdout_lines[-20:],
        },
        "serial_coverage": serial_coverage,
        "metrics": metrics,
        "chain_segments": chain_segments,
    }
    write_json(case_dir / "result.json", payload)
    (case_dir / "summary.md").write_text(render_case_md(payload), encoding="utf-8")
    return payload


def render_case_md(payload: Dict[str, Any]) -> str:
    metrics = payload.get("metrics", {})
    serial_coverage = payload.get("serial_coverage", {}) if isinstance(payload.get("serial_coverage"), dict) else {}
    lines = [
        f"# {payload.get('case_id')}",
        "",
        f"- Result: `{payload.get('result')}`",
        f"- Attribution: `{payload.get('attribution')}`",
        f"- Reason: {payload.get('reason')}",
        f"- Serial coverage: `{serial_coverage.get('status', '')}` - {serial_coverage.get('reason', '')}",
        f"- Mode/command/delay: `{payload.get('mode')}` / `{payload.get('command')}` / `{payload.get('delay_ms')}ms`",
        f"- Lines: `{metrics.get('line_count')}`; wake=`{metrics.get('wake_count')}`; asr=`{metrics.get('asr_event_count')}`; response=`{metrics.get('command_response_count')}`; device_control_response=`{metrics.get('device_control_response_count')}`; valid_tts_url=`{metrics.get('valid_tts_url_count')}`; empty_tts_url=`{metrics.get('empty_tts_url_count')}`; null_tts=`{metrics.get('null_tts_count')}`; no_valid_tts=`{metrics.get('no_valid_tts_count')}`; media_state_start=`{metrics.get('media_state_start_count')}`; actuator_marker=`{metrics.get('actuator_marker_count')}`; media_error=`{metrics.get('media_error_count')}`",
        f"- ASR texts: `{metrics.get('asr_texts')}`",
        f"- ASR pinyin: `{metrics.get('asr_pinyin_texts')}`",
        f"- Online request IDs: `{metrics.get('online_request_ids')}`",
        f"- Interaction latency samples: `{metrics.get('latency_samples')}`",
        f"- Command keywords: `{metrics.get('command_keywords')}`",
        f"- Matched command keywords: `{metrics.get('matched_command_keywords')}`",
        f"- Response texts: `{metrics.get('response_texts')}`",
        f"- Command kind/effect/actuator expectation: `{metrics.get('command_kind')}` / `{metrics.get('control_effect')}` / `{metrics.get('actuator_expectation')}`",
        "",
        "## Chain Segments",
        "",
    ]
    segments = payload.get("chain_segments", {}) if isinstance(payload.get("chain_segments"), dict) else {}
    for name in ("recognition", "control_reply", "tts_response", "actuator_feedback"):
        segment = segments.get(name, {}) if isinstance(segments.get(name), dict) else {}
        lines.append(f"- `{name}`: `{segment.get('result', '')}` - {segment.get('reason', '')}")
    lines.extend([
        "",
        "## Key Samples",
        "",
    ])
    samples = metrics.get("samples", {}) if isinstance(metrics.get("samples"), dict) else {}
    for name in ("wake", "asr", "command_response", "device_control_response", "valid_tts_url", "empty_tts_url", "null_tts", "no_valid_tts", "media_state_start", "explicit_beep_marker", "weak_actuator_marker", "media_error", "boot_or_crash"):
        values = samples.get(name, [])
        lines.append(f"### {name}")
        if values:
            lines.extend([f"- `{line}`" for line in values[:10]])
        else:
            lines.append("- <none>")
        lines.append("")
    return "\n".join(lines)


def infer_root_cause(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id = {item.get("case_id"): item for item in results}
    half_open = by_id.get("M01_half_open_1500", {})
    full_open = by_id.get("M02_full_open_1500", {})
    full_open_late = by_id.get("M03_full_open_2500", {})
    full_other = [by_id.get("M04_full_close_1500", {}), by_id.get("M05_full_music_1500", {}), by_id.get("M06_full_news_1500", {})]

    def passed(item: Dict[str, Any]) -> bool:
        return str(item.get("result")) in {"PASS", "PASS_WITH_WARNINGS"}

    def failed_at(item: Dict[str, Any], attribution: str) -> bool:
        return str(item.get("result")) == "FAIL" and str(item.get("attribution")) == attribution

    findings: List[str] = []
    attribution = "needs_more_data"
    open_ac_cases = [item for item in results if "打开空调" in str(item.get("command", ""))]
    non_ac_cases = [item for item in results if "空调" not in str(item.get("command", ""))]
    if (
        open_ac_cases
        and all(str(item.get("result")) == "FAIL" and str(item.get("attribution")) == "command_domain_or_oracle" for item in open_ac_cases)
        and any(passed(item) for item in non_ac_cases)
    ):
        modes = sorted({str(item.get("mode")) for item in open_ac_cases})
        delays = sorted({str(item.get("delay_ms")) for item in open_ac_cases}, key=lambda value: int(value) if value.isdigit() else 0)
        attribution = "ac_command_or_oracle_not_full_duplex"
        findings.append(
            f"打开空调在模式 {modes}、delay {delays} 下均只见空 TTS/null TTS，缺少在线 ASR 文本/命令关键词/有效响应；同矩阵非空调在线命令存在 PASS。"
        )
        findings.append("当前证据不支持“WS63 全双工整体不可识别”或“1s 注入时序单因子”；更像空调命令域在 WS63 日志中的可观测性/断言口径问题，或空调命令域处理未返回可判定成功证据。")
        if any(str(item.get("result")) == "PASS_WITH_WARNINGS" for item in results):
            findings.append("部分用例存在媒体/TTS 告警；识别归因和媒体归因需要分开。")
        return {"attribution": attribution, "findings": findings}

    if passed(half_open) and failed_at(full_open, "asr_entry"):
        if passed(full_open_late):
            attribution = "full_duplex_wake_tts_or_early_timing"
            findings.append("打开空调在半双工可用，全双工 1.5s 失败但 2.5s 可用，优先定位唤醒播报尾部/早注入时序。")
        elif any(passed(item) for item in full_other):
            attribution = "full_duplex_ac_command_domain_or_timing"
            findings.append("打开空调在半双工可用，全双工同间隔失败，但全双工其他命令可用，优先定位空调控制命令域或该命令在全双工下的时序/语义链路。")
        else:
            attribution = "full_duplex_asr_entry_general"
            findings.append("半双工打开空调可用，但全双工多类命令均失败，优先定位全双工 ASR 入口或模式状态。")
    elif not passed(half_open):
        if failed_at(half_open, "command_domain_or_oracle") and any(passed(item) for item in full_other):
            attribution = "ac_command_or_oracle_not_full_duplex"
            findings.append("半双工打开空调基线也未满足严格命令断言，而全双工其他在线命令存在闭环证据；当前问题不应优先归因为全双工 ASR 总体失效。")
            findings.append("打开空调窗口仅见空 TTS/null TTS 或底层 appliance 帧，缺少在线 ASR 文本/命令关键词/有效响应 URL；需在空调命令域、日志可观测性和断言口径之间继续确认。")
        else:
            attribution = "command_or_audio_baseline"
            findings.append("半双工打开空调基线未通过，不能先归因全双工；优先查命令域、音频、云端或设备状态。")
    elif passed(full_open):
        attribution = "not_reproduced"
        findings.append("全双工 1.5s 打开空调本轮通过，历史失败需按偶现/时序稳定性继续扩大轮次。")
    else:
        findings.append("矩阵未形成单一稳定指向，需要补充更多变量。")

    if full_open and full_open_late and not passed(full_open) and not passed(full_open_late):
        findings.append("打开空调在全双工 1.5s 与 2.5s 均失败，单纯早注入解释不足。")
    if any(str(item.get("result")) == "PASS_WITH_WARNINGS" for item in results):
        findings.append("部分用例存在媒体/TTS 告警；识别归因和媒体归因需要分开。")
    return {"attribution": attribution, "findings": findings}


def write_csv(path: Path, results: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id",
        "mode",
        "command",
        "delay_ms",
        "variable",
        "result",
        "attribution",
        "reason",
        "serial_coverage_status",
        "serial_unavailable_ports",
        "line_count",
        "wake_count",
        "asr_event_count",
        "command_response_count",
        "device_control_response_count",
        "valid_tts_url_count",
        "empty_tts_url_count",
        "null_tts_count",
        "no_valid_tts_count",
        "media_state_start_count",
        "actuator_marker_count",
        "explicit_beep_marker_count",
        "weak_actuator_marker_count",
        "media_error_count",
        "command_kind",
        "control_effect",
        "actuator_expectation",
        "response_texts",
        "recognition_segment",
        "control_reply_segment",
        "tts_response_segment",
        "actuator_feedback_segment",
        "asr_texts",
        "asr_pinyin_texts",
        "online_mids",
        "online_session_ids",
        "online_record_ids",
        *LATENCY_FIELDS,
        "latency_samples",
        "command_keywords",
        "matched_command_keywords",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            metrics = item.get("metrics", {}) if isinstance(item.get("metrics"), dict) else {}
            segments = item.get("chain_segments", {}) if isinstance(item.get("chain_segments"), dict) else {}
            coverage = item.get("serial_coverage", {}) if isinstance(item.get("serial_coverage"), dict) else {}
            writer.writerow(
                {
                    "case_id": item.get("case_id"),
                    "mode": item.get("mode"),
                    "command": item.get("command"),
                    "delay_ms": item.get("delay_ms"),
                    "variable": item.get("variable"),
                    "result": item.get("result"),
                    "attribution": item.get("attribution"),
                    "reason": item.get("reason"),
                    "serial_coverage_status": coverage.get("status", ""),
                    "serial_unavailable_ports": "|".join(
                        [
                            f"{entry.get('port')}({','.join(entry.get('roles', []))}):{entry.get('last_error') or 'closed'}"
                            for entry in coverage.get("unavailable_ports", [])
                            if isinstance(entry, dict)
                        ]
                    ),
                    "line_count": metrics.get("line_count"),
                    "wake_count": metrics.get("wake_count"),
                    "asr_event_count": metrics.get("asr_event_count"),
                    "command_response_count": metrics.get("command_response_count"),
                    "device_control_response_count": metrics.get("device_control_response_count"),
                    "valid_tts_url_count": metrics.get("valid_tts_url_count"),
                    "empty_tts_url_count": metrics.get("empty_tts_url_count"),
                    "null_tts_count": metrics.get("null_tts_count"),
                    "no_valid_tts_count": metrics.get("no_valid_tts_count"),
                    "media_state_start_count": metrics.get("media_state_start_count"),
                    "actuator_marker_count": metrics.get("actuator_marker_count"),
                    "explicit_beep_marker_count": metrics.get("explicit_beep_marker_count"),
                    "weak_actuator_marker_count": metrics.get("weak_actuator_marker_count"),
                    "media_error_count": metrics.get("media_error_count"),
                    "command_kind": metrics.get("command_kind"),
                    "control_effect": metrics.get("control_effect"),
                    "actuator_expectation": metrics.get("actuator_expectation"),
                    "response_texts": "|".join(metrics.get("response_texts", [])),
                    "recognition_segment": nested(segments, "recognition", "result") if isinstance(segments, dict) else "",
                    "control_reply_segment": nested(segments, "control_reply", "result") if isinstance(segments, dict) else "",
                    "tts_response_segment": nested(segments, "tts_response", "result") if isinstance(segments, dict) else "",
                    "actuator_feedback_segment": nested(segments, "actuator_feedback", "result") if isinstance(segments, dict) else "",
                    "asr_texts": "|".join(metrics.get("asr_texts", [])),
                    "asr_pinyin_texts": "|".join(metrics.get("asr_pinyin_texts", [])),
                    "online_mids": "|".join(str(req.get("mid", "")) for req in metrics.get("online_request_ids", []) if isinstance(req, dict) and req.get("mid")),
                    "online_session_ids": "|".join(str(req.get("sessionId", "")) for req in metrics.get("online_request_ids", []) if isinstance(req, dict) and req.get("sessionId")),
                    "online_record_ids": "|".join(str(req.get("recordId", "")) for req in metrics.get("online_request_ids", []) if isinstance(req, dict) and req.get("recordId")),
                    **{field: first_latency_value(metrics, field) for field in LATENCY_FIELDS},
                    "latency_samples": json.dumps((metrics.get("latency_samples") or [])[:5], ensure_ascii=False, separators=(",", ":")),
                    "command_keywords": "|".join(metrics.get("command_keywords", [])),
                    "matched_command_keywords": "|".join(metrics.get("matched_command_keywords", [])),
                }
            )


def render_report(summary: Dict[str, Any]) -> str:
    serial_coverage = summary.get("serial_coverage", {}) if isinstance(summary.get("serial_coverage"), dict) else {}
    lines = [
        "# WS63 打开空调控制变量诊断报告",
        "",
        f"- Run dir: `{summary['run_dir']}`",
        f"- Env file: `{summary['env_file']}`",
        f"- Project: `{summary['project_id']}`",
        f"- Started: `{summary['started_at']}`",
        f"- Finished: `{summary['finished_at']}`",
        f"- Serial coverage: `{serial_coverage.get('status', '')}` - {serial_coverage.get('reason', '')}",
        f"- Overall attribution: `{summary['root_cause']['attribution']}`",
        f"- Snapshot diff: `{(summary.get('snapshots') or {}).get('diff', '')}`",
        f"- Snapshot coverage: `{(summary.get('snapshots') or {}).get('coverage', '')}`",
        "",
        "## Root Cause Findings",
        "",
    ]
    for item in summary["root_cause"].get("findings", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Matrix", ""])
    lines.append("| Case | Mode | Command | Delay | Result | Attribution | Reason |")
    lines.append("|---|---|---|---:|---|---|---|")
    for item in summary["results"]:
        lines.append(
            "| {case_id} | {mode} | {command} | {delay_ms} | {result} | {attribution} | {reason} |".format(
                **{key: str(item.get(key, "")).replace("|", "/") for key in ["case_id", "mode", "command", "delay_ms", "result", "attribution", "reason"]}
            )
        )
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Summary JSON: `{summary['summary_json']}`",
            f"- Matrix CSV: `{summary['matrix_csv']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def snapshot_task() -> Dict[str, Any]:
    return {
        "schema": "polaris.command-control-diagnosis.task.v1",
        "task_id": "command_control_diagnosis",
        "expected_state": {
            "wake_evidence": "expected",
            "cloud_link": "expected",
            "command_control_behavior": "diagnosis_matrix",
        },
    }


def capture_diagnosis_snapshot(
    run_dir: Path,
    env_path: Path,
    env_payload: Dict[str, Any],
    phase: str,
    *,
    extra_state: Optional[Dict[str, Any]] = None,
    file_name: str = "",
) -> Dict[str, Any]:
    snapshot = build_snapshot(
        env_path=env_path,
        env_payload=env_payload,
        task=snapshot_task(),
        phase=phase,
        run_dir=run_dir,
        evidence_roots=[run_dir],
        scope={"runner": "run_command_control_diagnosis"},
        extra_state=extra_state,
    )
    write_snapshot(run_dir, snapshot, file_name=file_name or None)
    return snapshot


def finalize_diagnosis_snapshots(
    run_dir: Path,
    env_path: Path,
    env_payload: Dict[str, Any],
    before_snapshot: Dict[str, Any],
    *,
    summary: Dict[str, Any],
) -> Dict[str, Any]:
    after = capture_diagnosis_snapshot(run_dir, env_path, env_payload, "after", extra_state={"summary": summary}, file_name="snapshot_after.json")
    diff = diff_snapshots(before_snapshot, after)
    diff_path = write_snapshot_diff(run_dir, diff)
    coverage_path = write_snapshot_coverage(run_dir, diff.get("coverage", {}))
    return {
        "before": str((run_dir / "snapshot_before.json").resolve()),
        "after": str((run_dir / "snapshot_after.json").resolve()),
        "diff": str(diff_path.resolve()),
        "coverage": str(coverage_path.resolve()),
        "coverage_summary": diff.get("coverage", {}),
        "diff_summary": diff.get("summary", {}),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WS63/full-duplex command failure control-variable diagnosis")
    parser.add_argument("--env-file", default="polaris.local.json")
    parser.add_argument("--out-root", default="satellite/cucumber-agent-testing/debug/command_control_diagnosis")
    parser.add_argument("--matrix-file", default="")
    parser.add_argument("--device-key", default="")
    parser.add_argument("--wake-text", default="")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--no-preflight", action="store_true")
    parser.add_argument("--restore-half-duplex", action="store_true", default=True)
    parser.add_argument("--required-serial-roles", default="", help="Comma-separated log roles that must open, e.g. ap,upper.")
    parser.add_argument("--wait-serial-coverage-s", type=float, default=4.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.allow_side_effects:
        raise SystemExit("control-variable diagnosis uses cloud/audio/serial side effects; pass --allow-side-effects")
    env_path = resolve_env_path(args.env_file, WORKSPACE_ROOT)
    env_payload = load_env_payload(env_path)
    device_key = text_value(args.device_key, nested(env_payload, "audio", "default_playback_device_key"))
    wake_text = text_value(args.wake_text, nested(env_payload, "device", "wake_word"), env_payload.get("current_wakeup_word"), "小美小美")
    matrix = load_matrix(Path(args.matrix_file)) if args.matrix_file else default_matrix()
    if args.max_cases > 0:
        matrix = matrix[: args.max_cases]
    ports = configured_ports(env_payload)
    run_dir = (WORKSPACE_ROOT / args.out_root / now_stamp()).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    session_dir = run_dir / "session"
    started_at = datetime.now()
    before_snapshot = capture_diagnosis_snapshot(run_dir, env_path, env_payload, "before", file_name="snapshot_before.json")
    serial_session = None
    marker_state: Optional[Dict[str, Any]] = None
    results: List[Dict[str, Any]] = []
    preflight = {"result": "SKIPPED", "actions": []}
    serial_coverage: Dict[str, Any] = {"status": "NOT_STARTED", "reason": "serial logger not started"}
    try:
        if not args.no_preflight:
            preflight = run_preflight(str(env_path), device_key, run_dir / "preflight")
            write_json(run_dir / "preflight.json", preflight)
            if preflight.get("result") == "BLOCKED":
                summary = {
                    "schema": "polaris.command-control-diagnosis.summary.v1",
                    "result": "BLOCKED",
                    "reason": "preflight blocked",
                    "run_dir": str(run_dir),
                    "env_file": str(env_path),
                    "project_id": env_payload.get("project_id"),
                    "preflight": preflight,
                    "serial_coverage": serial_coverage,
                    "results": [],
                }
                summary["snapshots"] = finalize_diagnosis_snapshots(run_dir, env_path, env_payload, before_snapshot, summary=summary)
                write_json(run_dir / "summary.json", summary)
                print(run_dir)
                print("result=BLOCKED reason=preflight")
                return 2
        serial_session = start_serial_logger_adapter(session_dir, env_file=str(env_path), log_path=run_dir / "adapter_serial_logger.log")
        serial_coverage = evaluate_serial_coverage(
            env_payload,
            session_dir,
            required_roles=parse_required_roles(args.required_serial_roles, env_payload),
            wait_s=args.wait_serial_coverage_s,
        )
        write_json(run_dir / "serial_coverage.json", serial_coverage)
        if serial_coverage.get("status") == "BLOCKED":
            summary = {
                "schema": "polaris.command-control-diagnosis.summary.v1",
                "result": "BLOCKED",
                "reason": serial_coverage.get("reason", "serial coverage blocked"),
                "run_dir": str(run_dir),
                "env_file": str(env_path),
                "project_id": env_payload.get("project_id"),
                "device_key": device_key,
                "wake_text": wake_text,
                "ports": ports,
                "started_at": started_at.isoformat(timespec="seconds"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "preflight": preflight,
                "serial_coverage": serial_coverage,
                "results": [],
            }
            summary["snapshots"] = finalize_diagnosis_snapshots(run_dir, env_path, env_payload, before_snapshot, summary=summary)
            write_json(run_dir / "summary.json", summary)
            print(run_dir)
            print(f"result=BLOCKED reason={serial_coverage.get('reason')}")
            return 2
        marker_state = install_session_marker(session_dir)
        for case in matrix:
            result = run_case(
                case,
                wake_text=wake_text,
                device_key=device_key,
                env_file=str(env_path),
                session_dir=session_dir,
                ports=ports,
                out_root=run_dir / "cases",
                serial_coverage=serial_coverage,
            )
            results.append(result)
            print(f"{case.case_id} result={result['result']} attribution={result['attribution']} reason={result['reason']}", flush=True)
            time.sleep(1.0)
    finally:
        if args.restore_half_duplex:
            try:
                restore = set_mode("half", 15, str(env_path), run_dir / "recovery")
                write_json(run_dir / "recovery" / "restore_half_duplex.json", restore)
            except Exception as exc:
                write_json(run_dir / "recovery" / "restore_half_duplex.json", {"result": "BLOCKED", "reason": str(exc)})
        if serial_session is not None:
            try:
                stop_serial_logger_adapter(serial_session)
            except Exception as exc:
                write_json(run_dir / "serial_logger_stop_error.json", {"error": str(exc)})
        restore_session_marker(marker_state)
    finished_at = datetime.now()
    root_cause = infer_root_cause(results)
    summary_path = run_dir / "summary.json"
    csv_path = run_dir / "matrix.csv"
    summary = {
        "schema": "polaris.command-control-diagnosis.summary.v1",
        "result": "DIAGNOSED",
        "run_dir": str(run_dir),
        "env_file": str(env_path),
        "project_id": env_payload.get("project_id"),
        "device_key": device_key,
        "wake_text": wake_text,
        "ports": ports,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "preflight": preflight,
        "serial_coverage": serial_coverage,
        "root_cause": root_cause,
        "results": results,
        "summary_json": str(summary_path),
        "matrix_csv": str(csv_path),
    }
    write_csv(csv_path, results)
    summary["snapshots"] = finalize_diagnosis_snapshots(run_dir, env_path, env_payload, before_snapshot, summary=summary)
    write_json(summary_path, summary)
    (run_dir / "report.md").write_text(render_report(summary), encoding="utf-8")
    print(run_dir)
    print(f"result=DIAGNOSED attribution={root_cause['attribution']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
