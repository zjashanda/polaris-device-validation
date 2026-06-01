#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence-first snapshot helpers for Polaris validation runs.

The snapshot layer is intentionally conservative: it records configuration,
artifact/log observations, and gaps, but it never turns missing evidence into a
fabricated device state.  Runners can use the same functions for before/after,
checkpoint, incident, recovery, and final snapshots.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from runtime.resource_runtime import build_resource_snapshot
except Exception:  # pragma: no cover - allows standalone py_compile/imports
    build_resource_snapshot = None  # type: ignore


SNAPSHOT_SCHEMA = "polaris.snapshot.v1"
SNAPSHOT_DIFF_SCHEMA = "polaris.snapshot_diff.v1"
SNAPSHOT_COVERAGE_SCHEMA = "polaris.snapshot_coverage.v1"
UNKNOWN = "UNKNOWN"

SNAPSHOT_TYPES = [
    "env_config_snapshot",
    "device_runtime_snapshot",
    "voice_session_snapshot",
    "health_snapshot",
    "behavior_snapshot",
    "cloud_config_snapshot",
]

TEXT_EXTENSIONS = {".log", ".txt", ".md", ".json", ".jsonl", ".csv"}
SKIP_EXTENSIONS = {".wav", ".pcm", ".mp3", ".bin", ".pyc", ".pyd", ".dll", ".exe", ".xlsx", ".png", ".jpg", ".jpeg"}
MAX_EVIDENCE_FILES = 400
MAX_FILE_BYTES = 2_000_000
MAX_SAMPLES_PER_SIGNAL = 8

PATTERNS: Dict[str, re.Pattern[str]] = {
    "duplex_full": re.compile(r"(?:\bfullduplex\s*[:=]\s*1\b|set\s+fullduplex\s+to\s+on\b|fullduplex\s+timeout\s+refresh\s+to\s+\d+s)", re.I),
    "duplex_half": re.compile(r"(?:\bfullduplex\s*[:=]\s*0\b|set\s+fullduplex\s+to\s+off\b)", re.I),
    "duplex_refresh": re.compile(r"(?:(?:full|half)duplex\s+timeout\s+refresh\s+to\s+(\d+)s|restart\s+session\s+timer\s+with\s+(\d+)s)", re.I),
    "wake_ap": re.compile(r"(?:Pre Wakeup|wakeup_callback|multi_allow_wakeup_callback|mark has wakeup)", re.I),
    "wake_cp": re.compile(r"\bWAKE\(1\)", re.I),
    "wake_asr": re.compile(r"\b(?:online_wakeup|offline_wakeup|wakeup)\b", re.I),
    "asr": re.compile(r"(?:(?:online|offline)_asr_callbak|MSpeech Cloud 3 evt|cloud asr with|Recv .* ASR|recognizer start)", re.I),
    "cloud": re.compile(r"(?:cloud\.speech\.reply|cloud\.instructions|MSpeech Cloud|device\.event\.keepAlive|login success|online_request)", re.I),
    "media_play": re.compile(r"(?:ttsplayer play|play audio https?://|TTS playing|audioBroadcast|\"status\"\s*:\s*\"play\")", re.I),
    "media_stop": re.compile(r"(?:ttsplayer report state:\s*stop|PLAYBACK_COMPLETE|\"status\"\s*:\s*\"stop\"|tone player evt 6)", re.I),
    "media_error": re.compile(r"(?:recv timeout|http send failed|open probe url fail|HTTP.*(?:fail|error|timeout)|download.*(?:fail|error|timeout)|demux.*(?:fail|error|timeout))", re.I),
    "reboot": re.compile(r"(?:Boot Reason|boot reason|watchdog|panic|assert|hard fault|hardfault|reboot_reason|RESET=|will reboot device)", re.I),
    "serial_error": re.compile(r"(?:LOGGER_ERROR|serial logger exited|port not open|SerialException)", re.I),
    "mic": re.compile(r"(?:mic disabled|mic enabled|mic(?:rophone)?\s*(?:mute|unmute|enable|disable)|device\.event\.mic)", re.I),
    "volume": re.compile(r"(?:set volume|volume\s*[:=]|\"volume\"\s*:)", re.I),
    "night": re.compile(r"(?:night[_ -]?mode|set-night-mode|night mode)", re.I),
    "wakeup_threshold": re.compile(r"(?:wake(?:up)? threshold|awake[_ -]?threshold|ncmThreshold|get threshold is)", re.I),
}

ONLINE_ID_RE = re.compile(r"(?P<key>mid|sessionId|recordId)[\"'\s:=]+(?P<value>[A-Za-z0-9_-]{8,})")
BUSINESS_501_RE = re.compile(r"(?:business_code|code|errorCode|errCode)[\"'\s:=]+501\b|HTTP\s*200.*\b501\b", re.I)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def nested(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def rel(path: Path, workspace_root: Optional[Path] = None) -> str:
    root = workspace_root or Path.cwd()
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def read_json_safe(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_snapshot_id(project_id: str, phase: str) -> str:
    project = re.sub(r"[^A-Za-z0-9_.-]+", "_", project_id or "project").strip("_") or "project"
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", phase or "snapshot").strip("_") or "snapshot"
    return f"{project}_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{os.getpid()}"


def env_config_state(env_payload: Dict[str, Any], env_path: Optional[Path]) -> Dict[str, Any]:
    serial = nested(env_payload, "serial", "ports")
    serial_ports = serial if isinstance(serial, dict) else {}
    return {
        "env_file": rel(env_path) if env_path else "",
        "project_id": _text(env_payload.get("project_id") or nested(env_payload, "_config_source", "active_project")),
        "project_type": _text(env_payload.get("project_type")),
        "serial": {
            "ports": serial_ports,
            "baudrate": _text(nested(env_payload, "serial", "baudrate") or env_payload.get("baudrate")),
            "control_baudrate": _text(nested(env_payload, "serial", "control_baudrate")),
            "topology": _text(nested(env_payload, "serial", "topology")),
            "required_log_roles": nested(env_payload, "serial", "required_log_roles") or [],
        },
        "audio": {
            "default_playback_device_key": _text(nested(env_payload, "audio", "default_playback_device_key") or env_payload.get("default_playback_device_key")) or "DEFAULT_RENDER_DEVICE",
            "playback_volume": nested(env_payload, "audio", "playback_volume"),
        },
        "network": {
            "wifi_ssid": _text(nested(env_payload, "network", "wifi_ssid") or env_payload.get("current_connected_ssid")),
            "enable_hotspot_control": bool(nested(env_payload, "network", "enable_hotspot_control")),
        },
        "cloud": {
            "api_environment": _text(nested(env_payload, "cloud", "api_environment")),
            "device_env": _text(nested(env_payload, "cloud", "device_env")),
            "device_env_command": _text(nested(env_payload, "cloud", "device_env_command")),
        },
        "device": {
            "wake_word": _text(nested(env_payload, "device", "wake_word") or env_payload.get("current_wakeup_word")),
            "device_id": _text(nested(env_payload, "device", "device_id") or env_payload.get("device_id")),
        },
    }


def expected_state_from_task(task: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    task = task or {}
    expected = task.get("expected_state")
    if isinstance(expected, dict):
        result = dict(expected)
    else:
        result = {}
    generic_expected = task.get("expected")
    if isinstance(generic_expected, dict):
        result.setdefault("summary", generic_expected.get("summary", ""))

    tags = " ".join(
        [
            _text(nested(task, "scenario", "tag")),
            _text(task.get("scenario_tag")),
            _text(task.get("task_id")),
            _text(task.get("schema")),
        ]
    ).lower()
    if "full_duplex" in tags or "fullduplex" in tags:
        result.setdefault("duplex_mode", "full")
    elif "half_duplex" in tags or "halfduplex" in tags:
        result.setdefault("duplex_mode", "half")
    if "online" in tags:
        result.setdefault("cloud_link", "expected")
    if "wake" in tags:
        result.setdefault("wake_evidence", "expected")

    flows: List[Any] = []
    execution = task.get("execution", {}) if isinstance(task.get("execution"), dict) else {}
    raw_flows = execution.get("adapter_flows", {})
    if isinstance(raw_flows, dict):
        for value in raw_flows.values():
            if isinstance(value, list):
                flows.extend(value)
    elif isinstance(raw_flows, list):
        flows.extend(raw_flows)
    for legacy_key in ("pre_adapter_flows", "post_adapter_flows"):
        value = execution.get(legacy_key)
        if isinstance(value, list):
            flows.extend(value)
    for flow in flows:
        if isinstance(flow, str):
            name = flow
            params: Dict[str, Any] = {}
        elif isinstance(flow, dict):
            name = _text(flow.get("flow") or flow.get("name"))
            params = flow.get("params", {}) if isinstance(flow.get("params"), dict) else {}
        else:
            continue
        lowered = name.lower()
        if lowered == "set_full_duplex":
            result["duplex_mode"] = "full"
            result["duplex_api_expected"] = {"enable": str(params.get("enable", "1"))}
        elif lowered == "set_half_duplex":
            result["duplex_mode"] = "half"
            result["duplex_api_expected"] = {"enable": str(params.get("enable", "0"))}
    return result or {"state_contract": "evidence_only"}


def _iter_candidate_files(roots: Sequence[Path]) -> List[Path]:
    files: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root:
            continue
        path = Path(root)
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = [item for item in path.rglob("*") if item.is_file()]
        else:
            continue
        for item in candidates:
            key = str(item.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            suffix = item.suffix.lower()
            if suffix in SKIP_EXTENSIONS or suffix not in TEXT_EXTENSIONS:
                continue
            name = item.name.lower()
            if name.startswith("snapshot_") or name in {"snapshot_diff.json", "snapshot_final.json"}:
                continue
            try:
                size = item.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            files.append(item)
    files.sort(key=lambda p: (str(p.parent), p.name))
    return files[:MAX_EVIDENCE_FILES]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except TypeError:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _sample_lines(text: str, pattern: re.Pattern[str], limit: int = MAX_SAMPLES_PER_SIGNAL) -> List[str]:
    samples: List[str] = []
    for raw in text.splitlines():
        if pattern.search(raw):
            samples.append(raw.strip()[:260])
            if len(samples) >= limit:
                break
    return samples


def _count(text: str, pattern: re.Pattern[str]) -> int:
    return len(pattern.findall(text))


def _collect_online_ids(text: str, limit: int = 80) -> List[Dict[str, str]]:
    ids: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for match in ONLINE_ID_RE.finditer(text):
        current[match.group("key")] = match.group("value")
        if len(current) >= 3:
            ids.append(dict(current))
            current = {}
            if len(ids) >= limit:
                break
    if current and len(ids) < limit:
        ids.append(dict(current))
    return ids


def _walk_json(payload: Any, *, limit: int = 2000) -> Iterable[Tuple[str, Any]]:
    stack: List[Tuple[str, Any]] = [("", payload)]
    visited = 0
    while stack and visited < limit:
        prefix, value = stack.pop()
        visited += 1
        yield prefix, value
        if isinstance(value, dict):
            for key, item in list(value.items())[:80]:
                next_key = f"{prefix}.{key}" if prefix else str(key)
                stack.append((next_key, item))
        elif isinstance(value, list):
            for index, item in enumerate(value[:80]):
                stack.append((f"{prefix}[{index}]", item))


def _json_signals(path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    signals: Dict[str, Any] = {}
    results: Dict[str, int] = {}
    api_results: List[Dict[str, Any]] = []
    online_ids: List[Dict[str, Any]] = []
    serial_coverage: List[Dict[str, Any]] = []

    for key, value in _walk_json(payload):
        leaf = key.rsplit(".", 1)[-1].lower()
        if leaf in {"result", "status"} and isinstance(value, (str, int, float)):
            text = str(value).upper()
            if text:
                results[text] = results.get(text, 0) + 1
        if leaf in {"result_counts", "counts", "overall_counts"} and isinstance(value, dict):
            for result_key, count in value.items():
                try:
                    results[str(result_key).upper()] = results.get(str(result_key).upper(), 0) + int(count)
                except Exception:
                    continue
        if leaf == "online_request_ids" and isinstance(value, list):
            online_ids.extend([item for item in value if isinstance(item, dict)][:40])
        if leaf == "serial_coverage" and isinstance(value, dict):
            serial_coverage.append(value)
        if isinstance(value, dict):
            adapter_id = _text(value.get("adapter_id") or nested(value, "adapter", "adapter_id"))
            action = _text(value.get("action") or nested(value, "adapter", "action") or value.get("name"))
            if adapter_id == "cloud.api" or action.startswith("set_") or "duplex" in action:
                api_results.append(
                    {
                        "path": rel(path),
                        "adapter_id": adapter_id,
                        "action": action,
                        "result": _text(value.get("result") or nested(value, "adapter", "result")),
                        "reason": _text(value.get("reason") or nested(value, "adapter", "reason")),
                        "returncode": value.get("returncode") or nested(value, "adapter", "returncode"),
                    }
                )
    if results:
        signals["results"] = results
    if api_results:
        signals["api_results"] = api_results[:80]
    if online_ids:
        signals["online_request_ids"] = online_ids[:80]
    if serial_coverage:
        signals["serial_coverage"] = serial_coverage[:20]
    return signals


def collect_evidence(roots: Optional[Sequence[Path]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    roots = roots or []
    signal_counts: Dict[str, int] = {key: 0 for key in PATTERNS}
    signal_samples: Dict[str, List[str]] = {key: [] for key in PATTERNS}
    result_counts: Dict[str, int] = {}
    api_results: List[Dict[str, Any]] = []
    online_ids: List[Dict[str, Any]] = []
    serial_coverage: List[Dict[str, Any]] = []
    business_501_sources: List[str] = []
    sources: List[Dict[str, Any]] = []

    for path in _iter_candidate_files([Path(item) for item in roots]):
        text = _read_text(path)
        if not text:
            continue
        file_signals: Dict[str, int] = {}
        for name, pattern in PATTERNS.items():
            count = _count(text, pattern)
            if not count:
                continue
            signal_counts[name] += count
            file_signals[name] = count
            if len(signal_samples[name]) < MAX_SAMPLES_PER_SIGNAL:
                signal_samples[name].extend(_sample_lines(text, pattern, MAX_SAMPLES_PER_SIGNAL - len(signal_samples[name])))
        if BUSINESS_501_RE.search(text):
            business_501_sources.append(rel(path))
        online_ids.extend(_collect_online_ids(text))

        json_signals: Dict[str, Any] = {}
        if path.suffix.lower() == ".json":
            payload = read_json_safe(path)
            if payload:
                json_signals = _json_signals(path, payload)
        if json_signals.get("results"):
            for key, value in json_signals["results"].items():
                result_counts[key] = result_counts.get(key, 0) + int(value)
        api_results.extend(json_signals.get("api_results", []))
        online_ids.extend(json_signals.get("online_request_ids", []))
        serial_coverage.extend(json_signals.get("serial_coverage", []))

        signals_payload: Dict[str, Any] = {}
        if file_signals:
            signals_payload["pattern_counts"] = file_signals
        if json_signals:
            signals_payload["json"] = json_signals
        if signals_payload:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            sources.append({"path": rel(path), "bytes": size, "signals": signals_payload})

    aggregated = {
        "signal_counts": {key: value for key, value in signal_counts.items() if value},
        "signal_samples": {key: value for key, value in signal_samples.items() if value},
        "result_counts": result_counts,
        "api_results": api_results[:120],
        "online_request_ids": online_ids[:120],
        "serial_coverage": serial_coverage[:40],
        "business_501_sources": business_501_sources[:40],
    }
    return sources, aggregated


def observed_mode(counts: Dict[str, int]) -> str:
    full = int(counts.get("duplex_full", 0) or 0)
    half = int(counts.get("duplex_half", 0) or 0)
    if full and half:
        return "conflict"
    if full:
        return "full"
    if half:
        return "half"
    return UNKNOWN


def _timeout_refresh_values(samples: Dict[str, List[str]]) -> List[int]:
    values: List[int] = []
    for sample in samples.get("duplex_refresh", []):
        for match in PATTERNS["duplex_refresh"].finditer(sample):
            raw = match.group(1) or match.group(2)
            if raw:
                try:
                    values.append(int(raw))
                except ValueError:
                    pass
    return sorted(set(values))


def build_observed_state(aggregated: Dict[str, Any]) -> Dict[str, Any]:
    counts = aggregated.get("signal_counts", {}) if isinstance(aggregated.get("signal_counts"), dict) else {}
    samples = aggregated.get("signal_samples", {}) if isinstance(aggregated.get("signal_samples"), dict) else {}
    return {
        "duplex": {
            "mode": observed_mode(counts),
            "full_marker_count": int(counts.get("duplex_full", 0) or 0),
            "half_marker_count": int(counts.get("duplex_half", 0) or 0),
            "timeout_refresh_count": int(counts.get("duplex_refresh", 0) or 0),
            "timeout_refresh_s": _timeout_refresh_values(samples),
            "samples": {
                "full": samples.get("duplex_full", [])[:5],
                "half": samples.get("duplex_half", [])[:5],
                "refresh": samples.get("duplex_refresh", [])[:5],
            },
        },
        "wake": {
            "ap_count": int(counts.get("wake_ap", 0) or 0),
            "cp_count": int(counts.get("wake_cp", 0) or 0),
            "asr_count": int(counts.get("wake_asr", 0) or 0),
            "samples": {
                "ap": samples.get("wake_ap", [])[:5],
                "cp": samples.get("wake_cp", [])[:5],
                "asr": samples.get("wake_asr", [])[:5],
            },
        },
        "asr": {
            "event_count": int(counts.get("asr", 0) or 0),
            "samples": samples.get("asr", [])[:8],
        },
        "cloud": {
            "event_count": int(counts.get("cloud", 0) or 0),
            "online_request_ids": aggregated.get("online_request_ids", [])[:80],
            "business_501_sources": aggregated.get("business_501_sources", [])[:40],
            "samples": samples.get("cloud", [])[:8],
        },
        "media": {
            "play_count": int(counts.get("media_play", 0) or 0),
            "stop_count": int(counts.get("media_stop", 0) or 0),
            "error_count": int(counts.get("media_error", 0) or 0),
            "error_samples": samples.get("media_error", [])[:8],
        },
        "config_markers": {
            "mic_marker_count": int(counts.get("mic", 0) or 0),
            "volume_marker_count": int(counts.get("volume", 0) or 0),
            "night_mode_marker_count": int(counts.get("night", 0) or 0),
            "wakeup_threshold_marker_count": int(counts.get("wakeup_threshold", 0) or 0),
            "samples": {
                "mic": samples.get("mic", [])[:5],
                "volume": samples.get("volume", [])[:5],
                "night": samples.get("night", [])[:5],
                "wakeup_threshold": samples.get("wakeup_threshold", [])[:5],
            },
        },
    }


def build_api_state(aggregated: Dict[str, Any]) -> Dict[str, Any]:
    api_results = aggregated.get("api_results", []) if isinstance(aggregated.get("api_results"), list) else []
    business_501_sources = aggregated.get("business_501_sources", []) if isinstance(aggregated.get("business_501_sources"), list) else []
    result_counts: Dict[str, int] = {}
    for item in api_results:
        result = _text(item.get("result")).upper() if isinstance(item, dict) else ""
        if result:
            result_counts[result] = result_counts.get(result, 0) + 1
    return {
        "api_results": api_results,
        "api_result_counts": result_counts,
        "business_501_blocked": bool(business_501_sources),
        "business_501_sources": business_501_sources,
    }


def build_health_state(aggregated: Dict[str, Any]) -> Dict[str, Any]:
    counts = aggregated.get("signal_counts", {}) if isinstance(aggregated.get("signal_counts"), dict) else {}
    samples = aggregated.get("signal_samples", {}) if isinstance(aggregated.get("signal_samples"), dict) else {}
    return {
        "reboot_or_crash_count": int(counts.get("reboot", 0) or 0),
        "serial_error_count": int(counts.get("serial_error", 0) or 0),
        "reboot_samples": samples.get("reboot", [])[:8],
        "serial_error_samples": samples.get("serial_error", [])[:8],
        "serial_coverage": aggregated.get("serial_coverage", [])[:20],
    }


def build_behavior_state(aggregated: Dict[str, Any]) -> Dict[str, Any]:
    observed = build_observed_state(aggregated)
    result_counts = aggregated.get("result_counts", {}) if isinstance(aggregated.get("result_counts"), dict) else {}
    return {
        "result_counts": result_counts,
        "wake_asr_cloud_media": {
            "wake_any_count": observed["wake"]["ap_count"] + observed["wake"]["cp_count"] + observed["wake"]["asr_count"],
            "asr_event_count": observed["asr"]["event_count"],
            "cloud_event_count": observed["cloud"]["event_count"],
            "media_play_count": observed["media"]["play_count"],
            "media_error_count": observed["media"]["error_count"],
        },
        "online_request_ids": observed["cloud"]["online_request_ids"],
    }


def unknown_fields_for_snapshot(observed: Dict[str, Any], sources: List[Dict[str, Any]]) -> List[str]:
    unknown: List[str] = []
    if not sources:
        return [
            "device_runtime_snapshot.device_observed_state",
            "voice_session_snapshot",
            "health_snapshot.runtime_health",
            "behavior_snapshot.behavior_observed_state",
            "cloud_config_snapshot.api_state",
        ]
    if observed.get("duplex", {}).get("mode") == UNKNOWN:
        unknown.append("voice_session_snapshot.duplex.mode")
    if observed.get("wake", {}).get("ap_count", 0) == 0 and observed.get("wake", {}).get("asr_count", 0) == 0:
        unknown.append("voice_session_snapshot.wake")
    if observed.get("cloud", {}).get("event_count", 0) == 0:
        unknown.append("cloud_config_snapshot.device_observed_cloud_link")
    if observed.get("media", {}).get("play_count", 0) == 0 and observed.get("media", {}).get("error_count", 0) == 0:
        unknown.append("behavior_snapshot.media_behavior")
    return unknown


def confidence_for_snapshot(sources: List[Dict[str, Any]], aggregated: Dict[str, Any]) -> Dict[str, Any]:
    counts = aggregated.get("signal_counts", {}) if isinstance(aggregated.get("signal_counts"), dict) else {}
    if not sources:
        level = "CONFIG_ONLY"
    elif sum(int(v or 0) for v in counts.values()) >= 20:
        level = "HIGH"
    elif counts:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {
        "overall": level,
        "source_count": len(sources),
        "signal_count": sum(int(v or 0) for v in counts.values()),
        "policy": "evidence_only_no_state_fabrication",
    }


def limitations_for_snapshot(phase: str, sources: List[Dict[str, Any]], aggregated: Dict[str, Any]) -> List[str]:
    limitations = [
        "Snapshot records observed evidence only; missing markers are UNKNOWN/EVIDENCE_GAP, not an inferred device failure.",
    ]
    if not sources:
        limitations.append("No runtime artifact/log source was available for this phase; only env_config_snapshot is reliable.")
    if not aggregated.get("signal_counts"):
        limitations.append("Runtime logs did not expose known Polaris markers for this snapshot.")
    limitations.append("Acoustic capture/loopback oracle is not included unless an external artifact is provided.")
    if phase in {"before", "pre"}:
        limitations.append("Before snapshot normally precedes behavior evidence; behavior fields are expected to be UNKNOWN.")
    return limitations


def build_snapshot(
    *,
    env_path: Optional[Path] = None,
    env_payload: Optional[Dict[str, Any]] = None,
    task: Optional[Dict[str, Any]] = None,
    phase: str = "snapshot",
    run_dir: Optional[Path] = None,
    evidence_roots: Optional[Sequence[Path]] = None,
    scope: Optional[Dict[str, Any]] = None,
    extra_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    env_payload = env_payload or {}
    project_id = _text(env_payload.get("project_id") or nested(env_payload, "_config_source", "active_project"))
    evidence_sources, aggregated = collect_evidence(evidence_roots or ([run_dir] if run_dir else []))
    observed = build_observed_state(aggregated)
    api_state = build_api_state(aggregated)
    health = build_health_state(aggregated)
    behavior = build_behavior_state(aggregated)
    resource_snapshot: Dict[str, Any] = {}
    if build_resource_snapshot is not None:
        try:
            resource_snapshot = build_resource_snapshot(env_payload, task or {}).to_dict()
        except Exception as exc:
            resource_snapshot = {"ok": False, "warnings": [str(exc)]}
    state = {
        "expected_state": expected_state_from_task(task),
        "env_config_snapshot": env_config_state(env_payload, env_path),
        "device_runtime_snapshot": {
            "resource_snapshot": resource_snapshot,
            "device_observed_state": {
                "serial_coverage": health.get("serial_coverage", []),
                "config_markers": observed.get("config_markers", {}),
            },
        },
        "voice_session_snapshot": {
            "duplex": observed.get("duplex", {}),
            "wake": observed.get("wake", {}),
            "asr": observed.get("asr", {}),
        },
        "health_snapshot": {"runtime_health": health},
        "behavior_snapshot": {"behavior_observed_state": behavior},
        "cloud_config_snapshot": {
            "configured_cloud": env_config_state(env_payload, env_path).get("cloud", {}),
            "api_state": api_state,
            "device_observed_cloud_link": observed.get("cloud", {}),
        },
    }
    if extra_state:
        state["extra_state"] = extra_state
    unknown_fields = unknown_fields_for_snapshot(observed, evidence_sources)
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "snapshot_id": safe_snapshot_id(project_id, phase),
        "phase": phase,
        "project_id": project_id,
        "timestamp": now_iso(),
        "run_dir": rel(run_dir) if run_dir else "",
        "scope": scope or {},
        "snapshot_types": SNAPSHOT_TYPES,
        "state": state,
        "evidence_sources": evidence_sources,
        "confidence": confidence_for_snapshot(evidence_sources, aggregated),
        "unknown_fields": unknown_fields,
        "limitations": limitations_for_snapshot(phase, evidence_sources, aggregated),
    }
    return payload


def flatten(value: Any, prefix: str = "") -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if isinstance(value, dict):
        if not value:
            result[prefix] = {}
        for key, item in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten(item, next_prefix))
    elif isinstance(value, list):
        result[prefix] = value
    else:
        result[prefix] = value
    return result


def is_unknown_value(value: Any) -> bool:
    if value is None:
        return True
    if value == "":
        return True
    if value == UNKNOWN:
        return True
    if value == {} or value == []:
        return True
    return False


def expected_for_field(expected_state: Dict[str, Any], field: str) -> Any:
    lowered = field.lower()
    if "duplex" in lowered:
        return expected_state.get("duplex_mode", "")
    if "wake" in lowered:
        return expected_state.get("wake_evidence", "")
    if "cloud" in lowered:
        return expected_state.get("cloud_link", "")
    leaf = field.rsplit(".", 1)[-1]
    return expected_state.get(leaf, "")


def attribution_hint(field: str, before_value: Any, after_value: Any, expected: Any, before: Dict[str, Any], after: Dict[str, Any]) -> str:
    field_l = field.lower()
    api_state = nested(after, "state", "cloud_config_snapshot", "api_state")
    if isinstance(api_state, dict) and api_state.get("business_501_blocked"):
        return "EXTERNAL_PRECONDITION_BLOCKED"
    if "duplex" in field_l:
        mode = nested(after, "state", "voice_session_snapshot", "duplex", "mode")
        if mode == "conflict":
            return "MODE_EVIDENCE_CONFLICT"
        if expected and mode == UNKNOWN:
            return "CONFIG_APPLY_GAP"
    if "reboot" in field_l or "crash" in field_l or "watchdog" in field_l:
        try:
            if int(after_value or 0) > int(before_value or 0):
                return "FIRMWARE_DEVICE_STABILITY"
        except Exception:
            pass
    if is_unknown_value(before_value) or is_unknown_value(after_value):
        return "EVIDENCE_GAP"
    if before_value != after_value:
        return "STATE_CHANGED"
    return "UNCHANGED"


def snapshot_coverage(snapshot: Dict[str, Any], diff: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = snapshot.get("state", {}) if isinstance(snapshot.get("state"), dict) else {}
    coverage_by_type: Dict[str, Any] = {}
    covered_types = 0
    for snapshot_type in SNAPSHOT_TYPES:
        value = state.get(snapshot_type)
        flat = flatten(value) if isinstance(value, dict) else {}
        total = len(flat) or 1
        known = sum(1 for item in flat.values() if not is_unknown_value(item))
        observed = known > 0
        if observed:
            covered_types += 1
        coverage_by_type[snapshot_type] = {
            "observed": observed,
            "known_field_count": known,
            "total_field_count": total,
            "coverage_ratio": round(known / total, 4) if total else 0.0,
        }
    diff_counts = {}
    if isinstance(diff, dict):
        diff_counts = {
            "changed": len(diff.get("changed", []) or []),
            "unchanged": len(diff.get("unchanged", []) or []),
            "unknown": len(diff.get("unknown", []) or []),
        }
    return {
        "schema": SNAPSHOT_COVERAGE_SCHEMA,
        "generated_at": now_iso(),
        "snapshot_id": snapshot.get("snapshot_id", ""),
        "phase": snapshot.get("phase", ""),
        "required_snapshot_types": SNAPSHOT_TYPES,
        "covered_type_count": covered_types,
        "required_type_count": len(SNAPSHOT_TYPES),
        "coverage_ratio": round(covered_types / len(SNAPSHOT_TYPES), 4),
        "coverage_by_type": coverage_by_type,
        "unknown_fields": snapshot.get("unknown_fields", []),
        "diff_counts": diff_counts,
    }


def diff_snapshots(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    before_flat = flatten(before.get("state", {}))
    after_flat = flatten(after.get("state", {}))
    expected_state = after.get("state", {}).get("expected_state", {}) if isinstance(after.get("state"), dict) else {}
    if not isinstance(expected_state, dict):
        expected_state = {}
    changed: List[Dict[str, Any]] = []
    unchanged: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    for field in sorted(set(before_flat) | set(after_flat)):
        before_value = before_flat.get(field, UNKNOWN)
        after_value = after_flat.get(field, UNKNOWN)
        expected = expected_for_field(expected_state, field)
        item = {
            "field": field,
            "before": before_value,
            "after": after_value,
            "expected": expected,
            "evidence": {
                "before_snapshot_id": before.get("snapshot_id", ""),
                "after_snapshot_id": after.get("snapshot_id", ""),
            },
            "attribution_hint": attribution_hint(field, before_value, after_value, expected, before, after),
        }
        if is_unknown_value(before_value) or is_unknown_value(after_value):
            unknown.append(item)
        elif before_value != after_value:
            changed.append(item)
        else:
            unchanged.append(item)
    payload = {
        "schema": SNAPSHOT_DIFF_SCHEMA,
        "generated_at": now_iso(),
        "before_snapshot_id": before.get("snapshot_id", ""),
        "after_snapshot_id": after.get("snapshot_id", ""),
        "project_id": after.get("project_id") or before.get("project_id", ""),
        "changed": changed,
        "unchanged": unchanged,
        "unknown": unknown,
        "summary": {
            "changed_count": len(changed),
            "unchanged_count": len(unchanged),
            "unknown_count": len(unknown),
            "has_mode_evidence_conflict": any(item.get("attribution_hint") == "MODE_EVIDENCE_CONFLICT" for item in changed + unknown),
            "has_external_precondition_blocked": any(item.get("attribution_hint") == "EXTERNAL_PRECONDITION_BLOCKED" for item in changed + unknown),
            "has_config_apply_gap": any(item.get("attribution_hint") == "CONFIG_APPLY_GAP" for item in changed + unknown),
        },
    }
    payload["coverage"] = snapshot_coverage(after, payload)
    return payload


def snapshot_file_name(phase: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", phase or "snapshot").strip("_").lower() or "snapshot"
    if normalized in {"before", "after", "final", "recovery"}:
        return f"snapshot_{normalized}.json"
    if normalized.startswith("checkpoint") or normalized.startswith("incident"):
        return f"snapshot_{normalized}.json"
    return f"snapshot_{normalized}.json"


def write_snapshot(run_dir: Path, snapshot: Dict[str, Any], *, file_name: Optional[str] = None) -> Path:
    path = run_dir / (file_name or snapshot_file_name(str(snapshot.get("phase") or "snapshot")))
    write_json(path, snapshot)
    return path


def write_diff(run_dir: Path, diff: Dict[str, Any], *, file_name: str = "snapshot_diff.json") -> Path:
    path = run_dir / file_name
    write_json(path, diff)
    return path


def write_coverage(run_dir: Path, coverage: Dict[str, Any], *, file_name: str = "snapshot_coverage.json") -> Path:
    path = run_dir / file_name
    write_json(path, coverage)
    return path


def write_snapshot_pair(
    run_dir: Path,
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Dict[str, Any]:
    before_path = write_snapshot(run_dir, before, file_name="snapshot_before.json")
    after_path = write_snapshot(run_dir, after, file_name="snapshot_after.json")
    diff = diff_snapshots(before, after)
    diff_path = write_diff(run_dir, diff)
    coverage_path = write_coverage(run_dir, diff.get("coverage", snapshot_coverage(after, diff)))
    return {
        "before": rel(before_path),
        "after": rel(after_path),
        "diff": rel(diff_path),
        "coverage": rel(coverage_path),
        "coverage_summary": diff.get("coverage", {}),
        "diff_summary": diff.get("summary", {}),
    }


def render_snapshot_report(snapshot_refs: Dict[str, Any]) -> List[str]:
    coverage = snapshot_refs.get("coverage_summary", {}) if isinstance(snapshot_refs.get("coverage_summary"), dict) else {}
    diff_summary = snapshot_refs.get("diff_summary", {}) if isinstance(snapshot_refs.get("diff_summary"), dict) else {}
    return [
        "## Snapshot Coverage",
        "",
        f"- Before: `{snapshot_refs.get('before', '')}`",
        f"- After: `{snapshot_refs.get('after', '')}`",
        f"- Diff: `{snapshot_refs.get('diff', '')}`",
        f"- Coverage: `{snapshot_refs.get('coverage', '')}`",
        f"- Covered types: `{coverage.get('covered_type_count', 0)}/{coverage.get('required_type_count', len(SNAPSHOT_TYPES))}`",
        f"- Diff summary: `{json.dumps(diff_summary, ensure_ascii=False)}`",
        "",
    ]
