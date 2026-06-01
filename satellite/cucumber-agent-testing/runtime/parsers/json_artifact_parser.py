#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse structured playback/runtime JSON artifacts into runtime events."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..events import ValidationEvent, make_event


def _event_from_timestamp(
    path: Path,
    line_no: int,
    timestamp: str,
    event_type: str,
    payload: Dict[str, Any],
    *,
    source: str = "audio",
) -> ValidationEvent:
    raw = f"{timestamp} [PC/{source}] {event_type} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    return make_event(path=path, line_no=line_no, raw=raw, source=source, event_type=event_type, payload=payload)


def _artifact_event(path: Path, line_no: int, timestamp: str, event_type: str, payload: Dict[str, Any]) -> ValidationEvent:
    return _event_from_timestamp(path, line_no, timestamp, event_type, payload, source="artifact")


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _iso_ms(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except Exception:
        return None


def _sidecar_audio_duration_ms(audio_file: Any) -> int | None:
    """Read the generated audio metadata next to a wav file when available."""
    text = str(audio_file or "").strip()
    if not text:
        return None
    meta = Path(text).with_suffix(".json")
    if not meta.exists():
        return None
    try:
        payload = _json_load(meta)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return _optional_int(payload.get("duration_ms"))


def _playback_duration_payload(payload: Dict[str, Any], audio_file: Any) -> Dict[str, Any]:
    started_ms = _iso_ms(payload.get("playback_started_at") or payload.get("process_started_at"))
    finished_ms = _iso_ms(payload.get("finished_at"))
    process_duration_ms = finished_ms - started_ms if started_ms is not None and finished_ms is not None else None
    return {
        "audio_duration_ms": _sidecar_audio_duration_ms(audio_file),
        "playback_process_duration_ms": process_duration_ms,
    }


def _status_event(prefix: str, result: str) -> str:
    normalized = str(result or "").strip().upper()
    if normalized == "PASS":
        return f"{prefix}Passed"
    if normalized == "FAIL":
        return f"{prefix}Failed"
    if normalized == "BLOCKED":
        return f"{prefix}Blocked"
    return f"{prefix}Unknown"


def parse_runtime_events_jsonl(path: Path) -> List[ValidationEvent]:
    events: List[ValidationEvent] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw.lstrip("\ufeff"))
        except Exception:
            continue
        timestamp = str(item.get("timestamp", "") or item.get("timestamp_iso", "") or "")
        event_type = str(item.get("event_type", "") or "")
        if not timestamp or not event_type:
            continue
        payload = dict(item.get("payload", {}) or {})
        source = str(item.get("source", "") or "artifact")
        raw = f"{timestamp} [PC/{source}] {event_type} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        events.append(
            make_event(
                path=path,
                line_no=line_no,
                raw=raw,
                source=source,
                event_type=event_type,
                payload=payload,
                run_id=str(item.get("run_id", "") or ""),
                scene_id=str(item.get("scene_id", "") or ""),
                device_id=str(item.get("device_id", "") or ""),
                plugin=str(item.get("plugin", "") or ""),
                timestamp_monotonic_ms=_optional_int(item.get("timestamp_monotonic_ms")),
                severity=str(item.get("severity", "") or ""),
                tags=[str(tag) for tag in item.get("tags", [])] if isinstance(item.get("tags"), list) else None,
                parent_event=str(item.get("parent_event", "") or ""),
                caused_by=str(item.get("caused_by", "") or ""),
            )
        )
    return events


def parse_playback_json(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    events: List[ValidationEvent] = []
    started = str(payload.get("playback_started_at", "") or "")
    finished = str(payload.get("finished_at", "") or "")
    audio_file = payload.get("audio_file", "")
    common = {
        "audio_file": audio_file,
        "device_key": payload.get("device_key", ""),
        "returncode": payload.get("returncode"),
        "playback_device": payload.get("playback_device", ""),
        "timestamp_source": "playback_json",
    }
    common.update(_playback_duration_payload(payload, audio_file))
    if started:
        events.append(_event_from_timestamp(path, 1, started, "AudioInjected", common))
    if finished:
        events.append(_event_from_timestamp(path, 2, finished, "AudioCompleted", common))
    return events


def parse_generic_playback_artifact(path: Path) -> List[ValidationEvent]:
    """Parse listenai-play command JSON files that are not named playback.json."""
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    if not payload.get("playback_started_at") or not payload.get("finished_at"):
        return []
    cmd_text = " ".join(str(item) for item in payload.get("cmd", []) or [])
    if "listenai_play" not in cmd_text and not payload.get("playback_device"):
        return []
    common = {
        "audio_file": "",
        "device_key": payload.get("device_key", ""),
        "returncode": payload.get("returncode"),
        "playback_device": payload.get("playback_device", ""),
        "timestamp_source": "generic_playback_artifact",
    }
    cmd = payload.get("cmd", []) or []
    if isinstance(cmd, list):
        for index, item in enumerate(cmd):
            if str(item) == "--audio-file" and index + 1 < len(cmd):
                common["audio_file"] = str(cmd[index + 1])
                break
    common.update(_playback_duration_payload(payload, common.get("audio_file", "")))
    return [
        _event_from_timestamp(path, 1, str(payload.get("playback_started_at", "")), "AudioInjected", common),
        _event_from_timestamp(path, 2, str(payload.get("finished_at", "")), "AudioCompleted", common),
    ]


def parse_interrupt_injection_result(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    timing = payload.get("timing", {}) if isinstance(payload, dict) else {}
    if not isinstance(timing, dict):
        return []
    events: List[ValidationEvent] = []
    common = {
        "kind": payload.get("kind", ""),
        "result": payload.get("result", ""),
        "attribution": payload.get("attribution", ""),
        "timestamp_source": "interrupt_injection_result",
    }
    injection_start = str(timing.get("planned_injection_start", "") or "")
    injection_end = str(timing.get("planned_injection_end", "") or "")
    if injection_start:
        events.append(_event_from_timestamp(path, 1, injection_start, "InterruptInjected", common))
    if injection_end:
        events.append(_event_from_timestamp(path, 2, injection_end, "InterruptCompleted", common))
    windows = timing.get("containing_self_play_windows", []) or timing.get("nearby_self_play_windows", [])
    if isinstance(windows, list):
        for index, window in enumerate(windows, start=10):
            if not isinstance(window, dict):
                continue
            window_payload = dict(common)
            window_payload.update(
                {
                    "window_source": window.get("source", ""),
                    "start_kind": window.get("start_kind", ""),
                    "stop_kind": window.get("stop_kind", ""),
                    "duration_ms": window.get("duration_ms", ""),
                    "url": window.get("url", ""),
                    "timestamp_source": "interrupt_self_play_window",
                }
            )
            start = str(window.get("start", "") or "")
            end = str(window.get("end", "") or "")
            if start:
                events.append(_event_from_timestamp(path, index * 2, start, "MediaStarted", window_payload))
            if end:
                events.append(_event_from_timestamp(path, index * 2 + 1, end, "MediaCompleted", window_payload))
    return events


def parse_interrupt_prerequisite_measurement(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict) or path.name != "interrupt_prerequisite_measurement.json":
        return []
    timestamp = str(payload.get("generated_at", "") or "")
    if not timestamp:
        return []
    selected = payload.get("selected", {}) if isinstance(payload.get("selected"), dict) else {}
    rows = payload.get("rows", []) if isinstance(payload.get("rows"), list) else []
    events = [
        _artifact_event(
            path,
            1,
            timestamp,
            "InterruptPrerequisiteSummary",
            {
                "timestamp_source": "interrupt_prerequisite_measurement",
                "total": payload.get("total"),
                "counts": payload.get("counts", {}),
                "playback_returncode": payload.get("playback_returncode"),
                "minimum_duration_ms": payload.get("minimum_duration_ms"),
                "injection_guard_ms": payload.get("injection_guard_ms"),
                "selected_exists": bool(selected),
            },
        )
    ]
    if selected:
        events.append(
            _artifact_event(
                path,
                2,
                timestamp,
                "InterruptPrerequisiteSelected",
                {
                    "timestamp_source": "interrupt_prerequisite_selected",
                    "candidate_id": selected.get("candidate_id", ""),
                    "phrase": selected.get("phrase", ""),
                    "verdict": selected.get("verdict", ""),
                    "attribution": selected.get("attribution", ""),
                    "reason": selected.get("reason", ""),
                    "self_play_duration_ms": selected.get("self_play_duration_ms"),
                    "injection_offset_ms": selected.get("injection_offset_ms"),
                    "window_count": selected.get("window_count"),
                    "cp_wake_count": selected.get("cp_wake_count"),
                    "ap_wake_count": selected.get("ap_wake_count"),
                    "asr_total": selected.get("asr_total"),
                    "recognized_command_keywords": selected.get("recognized_command_keywords", ""),
                },
            )
        )
        best_window = selected.get("best_window", {}) if isinstance(selected.get("best_window"), dict) else {}
        if best_window.get("start"):
            events.append(
                _event_from_timestamp(
                    path,
                    3,
                    str(best_window.get("start", "")),
                    "MediaStarted",
                    {"timestamp_source": "interrupt_prerequisite_best_window", **best_window},
                )
            )
        if best_window.get("end"):
            events.append(
                _event_from_timestamp(
                    path,
                    4,
                    str(best_window.get("end", "")),
                    "MediaCompleted",
                    {"timestamp_source": "interrupt_prerequisite_best_window", **best_window},
                )
            )
    for index, row in enumerate(rows, start=10):
        if not isinstance(row, dict):
            continue
        verdict = str(row.get("verdict", "") or "").upper()
        event_type = "InterruptPrerequisiteUsable" if verdict == "USABLE" else "InterruptPrerequisiteUnusable"
        events.append(
            _artifact_event(
                path,
                index,
                timestamp,
                event_type,
                {
                    "timestamp_source": "interrupt_prerequisite_row",
                    "index": row.get("index"),
                    "candidate_id": row.get("candidate_id", ""),
                    "phrase": row.get("phrase", ""),
                    "verdict": verdict,
                    "reason": row.get("reason", ""),
                    "self_play_duration_ms": row.get("self_play_duration_ms"),
                    "injection_offset_ms": row.get("injection_offset_ms"),
                    "window_count": row.get("window_count"),
                },
            )
        )
    return events


def parse_hotspot_cycle_summary(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict) or payload.get("action") != "hotspot-cycle":
        return []
    events: List[ValidationEvent] = []
    stop_result = payload.get("stop_result", {}) if isinstance(payload.get("stop_result"), dict) else {}
    after_stop = payload.get("after_stop_status", {}) if isinstance(payload.get("after_stop_status"), dict) else {}
    start_result = payload.get("start_result", {}) if isinstance(payload.get("start_result"), dict) else {}
    after_start = payload.get("after_start_status", {}) if isinstance(payload.get("after_start_status"), dict) else {}
    off_window = payload.get("off_window", {}) if isinstance(payload.get("off_window"), dict) else {}
    on_window = payload.get("on_window", {}) if isinstance(payload.get("on_window"), dict) else {}

    if str(stop_result.get("operational_state", "")).lower() == "off" or str(after_stop.get("operational_state", "")).lower() == "off":
        timestamp = str(stop_result.get("ts", "") or off_window.get("start", "") or after_stop.get("ts", "") or "")
        if timestamp:
            events.append(
                _event_from_timestamp(
                    path,
                    1,
                    timestamp,
                    "NetworkLost",
                    {
                        "marker": "hotspot_off",
                        "requested_state": stop_result.get("requested_state"),
                        "op_status": stop_result.get("op_status", ""),
                        "client_count": stop_result.get("client_count", after_stop.get("client_count", "")),
                        "timestamp_source": "hotspot_cycle_summary",
                    },
                )
            )

    recovered_by_hotspot = str(start_result.get("operational_state", "")).lower() == "on"
    recovered_by_clients = int(after_start.get("client_count", 0) or 0) > 0
    if recovered_by_hotspot or recovered_by_clients:
        timestamp = str(after_start.get("ts", "") or on_window.get("start", "") or start_result.get("ts", "") or "")
        if timestamp:
            events.append(
                _event_from_timestamp(
                    path,
                    2,
                    timestamp,
                    "NetworkRecovered",
                    {
                        "marker": "hotspot_on_or_clients_back",
                        "requested_state": start_result.get("requested_state"),
                        "op_status": start_result.get("op_status", ""),
                        "client_count": after_start.get("client_count", start_result.get("client_count", "")),
                        "timestamp_source": "hotspot_cycle_summary",
                    },
                )
            )
    return events


def parse_fa2_command_batch_summary(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict) or "rows" not in payload or "fa2_command_batch" not in path.name:
        return []
    timestamp = str(payload.get("generated_at", "") or payload.get("playback_finished_at", "") or "")
    if not timestamp:
        return []
    rows = payload.get("rows", []) if isinstance(payload.get("rows"), list) else []
    counts = payload.get("counts", {}) if isinstance(payload.get("counts"), dict) else {}
    total = int(payload.get("total", len(rows)) or 0)
    summary_payload = {
        "timestamp_source": "fa2_command_batch_summary",
        "total": total,
        "counts": counts,
        "pass_count": int(counts.get("PASS", 0) or 0),
        "fail_count": int(counts.get("FAIL", 0) or 0),
        "blocked_count": int(counts.get("BLOCKED", 0) or 0),
        "playback_returncode": payload.get("playback_returncode"),
        "command_file": payload.get("command_file", ""),
        "wake_word": payload.get("wake_word", ""),
        "audio_duration_ms": payload.get("audio_duration_ms"),
    }
    events = [_artifact_event(path, 1, timestamp, "CommandBatchSummary", summary_payload)]
    for index, row in enumerate(rows, start=2):
        if not isinstance(row, dict):
            continue
        row_ts = str(row.get("started_at", "") or payload.get("playback_started_at", "") or timestamp)
        result = str(row.get("result", "") or "")
        row_payload = {
            "timestamp_source": "fa2_command_batch_summary_row",
            "index": row.get("index", index - 1),
            "command": row.get("command", ""),
            "result": result,
            "reason": row.get("reason", ""),
            "failure_type": row.get("failure_type", ""),
            "cp_wake_count": int(row.get("cp_wake_count", 0) or 0),
            "ap_wake_count": int(row.get("ap_wake_count", 0) or 0),
            "asr_total": int(row.get("asr_total", 0) or 0),
            "cp_command_count": int(row.get("cp_command_count", 0) or 0),
            "unique_command_keyword_count": int(row.get("unique_command_keyword_count", 0) or 0),
            "ap_online_asr_texts": row.get("ap_online_asr_texts", ""),
            "recognized_command_keywords": row.get("recognized_command_keywords", ""),
        }
        events.append(_artifact_event(path, index, row_ts, _status_event("CommandUtterance", result), row_payload))
    return events


def parse_oneshot_matrix_summary(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict) or path.name != "oneshot_matrix_summary.json":
        return []
    timestamp = str(payload.get("generated_at", "") or "")
    if not timestamp:
        return []
    rows = payload.get("rows", []) if isinstance(payload.get("rows"), list) else []
    counts = payload.get("counts", {}) if isinstance(payload.get("counts"), dict) else {}
    events = [
        _artifact_event(
            path,
            1,
            timestamp,
            "OneshotMatrixSummary",
            {
                "timestamp_source": "oneshot_matrix_summary",
                "result": payload.get("result", ""),
                "attribution": payload.get("attribution", ""),
                "reason": payload.get("reason", ""),
                "command_text": payload.get("command_text", ""),
                "wake_word": payload.get("wake_word", ""),
                "intervals": payload.get("intervals", []),
                "counts": counts,
                "row_count": len(rows),
            },
        )
    ]
    for index, row in enumerate(rows, start=2):
        if not isinstance(row, dict):
            continue
        nested_row = row.get("row", {}) if isinstance(row.get("row"), dict) else {}
        result = str(row.get("result", "") or "")
        events.append(
            _artifact_event(
                path,
                index,
                str(row.get("started_at", "") or timestamp),
                _status_event("OneshotInterval", result),
                {
                    "timestamp_source": "oneshot_matrix_row",
                    "interval_ms": row.get("interval_ms"),
                    "result": result,
                    "attribution": row.get("attribution", ""),
                    "reason": row.get("reason", ""),
                    "returncode": row.get("returncode"),
                    "cp_wake_count": int(nested_row.get("cp_wake_count", 0) or 0),
                    "ap_wake_count": int(nested_row.get("ap_wake_count", 0) or 0),
                    "asr_total": int(nested_row.get("asr_total", 0) or 0),
                    "cp_command_count": int(nested_row.get("cp_command_count", 0) or 0),
                    "unique_command_keyword_count": int(nested_row.get("unique_command_keyword_count", 0) or 0),
                    "ap_online_asr_texts": nested_row.get("ap_online_asr_texts", ""),
                    "recognized_command_keywords": nested_row.get("recognized_command_keywords", ""),
                },
            )
        )
    return events


def parse_wake_matrix_summary(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict) or path.name != "wake_matrix_summary.json":
        return []
    timestamp = str(payload.get("generated_at", "") or "")
    if not timestamp:
        return []
    rows = payload.get("rows", []) if isinstance(payload.get("rows"), list) else []
    events = [
        _artifact_event(
            path,
            1,
            timestamp,
            "WakeMatrixSummary",
            {
                "timestamp_source": "wake_matrix_summary",
                "scenario": payload.get("scenario", ""),
                "result": payload.get("result", ""),
                "attribution": payload.get("attribution", ""),
                "reason": payload.get("reason", ""),
                "rounds_requested": payload.get("rounds_requested"),
                "counted_rounds": payload.get("counted_rounds"),
                "pass": payload.get("pass"),
                "fail": payload.get("fail"),
                "rate": payload.get("rate"),
                "latency": payload.get("latency", {}),
                "counts": payload.get("counts", {}),
                "wake_audio_duration_ms": payload.get("wake_audio_duration_ms"),
            },
        )
    ]
    for index, row in enumerate(rows, start=2):
        if not isinstance(row, dict):
            continue
        result = str(row.get("result", "") or "")
        events.append(
            _artifact_event(
                path,
                index,
                str(row.get("started_at", "") or timestamp),
                _status_event("WakeRound", result),
                {
                    "timestamp_source": "wake_matrix_row",
                    "round": row.get("round"),
                    "scenario": row.get("scenario", ""),
                    "result": result,
                    "counted": bool(row.get("counted", False)),
                    "attribution": row.get("attribution", ""),
                    "reason": row.get("reason", ""),
                    "playback_returncode": row.get("playback_returncode"),
                    "cp_wake_count": int(row.get("cp_wake_count", 0) or 0),
                    "ap_wake_count": int(row.get("ap_wake_count", 0) or 0),
                    "asr_wake_count": int(row.get("asr_wake_count", 0) or 0),
                    "boot_marker_count": int(row.get("boot_marker_count", 0) or 0),
                    "crash_marker_count": int(row.get("crash_marker_count", 0) or 0),
                    "continuous_segments": row.get("continuous_segments"),
                    "first_wake_marker_latency_ms": row.get("first_wake_marker_latency_ms"),
                },
            )
        )
    return events


def parse_online_vad_special_summary(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict) or path.name != "online_vad_special_summary.json":
        return []
    timestamp = str(payload.get("generated_at", "") or "")
    if not timestamp:
        return []
    rows = payload.get("rows", []) if isinstance(payload.get("rows"), list) else []
    events = [
        _artifact_event(
            path,
            1,
            timestamp,
            "OnlineVADSummary",
            {
                "timestamp_source": "online_vad_special_summary",
                "result": payload.get("result", ""),
                "attribution": payload.get("attribution", ""),
                "reason": payload.get("reason", ""),
                "candidate_count": payload.get("candidate_count"),
                "needs_review_count": payload.get("needs_review_count"),
                "counts": payload.get("counts", {}),
            },
        )
    ]
    for index, row in enumerate(rows, start=2):
        if not isinstance(row, dict):
            continue
        result = str(row.get("result", "") or "")
        coverage = row.get("coverage", {}) if isinstance(row.get("coverage"), dict) else {}
        events.append(
            _artifact_event(
                path,
                index,
                str(row.get("started_at", "") or timestamp),
                _status_event("OnlineVADCase", result),
                {
                    "timestamp_source": "online_vad_special_row",
                    "candidate_id": row.get("candidate_id", ""),
                    "category": row.get("category", ""),
                    "expected_text": row.get("expected_text", ""),
                    "result": result,
                    "attribution": row.get("attribution", ""),
                    "reason": row.get("reason", ""),
                    "cp_wake_count": int(row.get("cp_wake_count", 0) or 0),
                    "ap_wake_count": int(row.get("ap_wake_count", 0) or 0),
                    "asr_wake_count": int(row.get("asr_wake_count", 0) or 0),
                    "online_asr_texts": row.get("online_asr_texts", []),
                    "vad_end_count": int(row.get("vad_end_count", 0) or 0),
                    "cloud_tts_or_instruction_count": int(row.get("cloud_tts_or_instruction_count", 0) or 0),
                    "coverage": coverage.get("coverage"),
                    "missing_chars": coverage.get("missing_chars", []),
                },
            )
        )
    return events


def parse_false_wake_summary(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict) or path.name not in {"false_wake_quiet_summary.json", "false_wake_playback_summary.json"}:
        return []
    timestamp = str(payload.get("generated_at", "") or payload.get("started_at", "") or "")
    if not timestamp:
        return []
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    kind = payload.get("kind", "quiet" if "quiet" in path.name else "playback")
    return [
        _artifact_event(
            path,
            1,
            timestamp,
            "FalseWakeSummary",
            {
                "timestamp_source": path.name,
                "kind": kind,
                "result": payload.get("result", ""),
                "attribution": payload.get("attribution", ""),
                "reason": payload.get("reason", ""),
                "duration_s": payload.get("duration_s"),
                "line_count": metrics.get("line_count", metrics.get("total_lines", 0)),
                "total_lines": metrics.get("total_lines", metrics.get("line_count", 0)),
                "wake_marker_total": metrics.get("wake_marker_total", metrics.get("wake_line_count", 0)),
                "wake_line_count": metrics.get("wake_line_count", metrics.get("wake_marker_total", 0)),
                "boot_marker_count": metrics.get("boot_marker_count", 0),
                "crash_marker_count": metrics.get("crash_marker_count", 0),
                "boot_or_crash_count": metrics.get("boot_or_crash_count", 0),
                "audio_manifest": payload.get("audio_manifest", {}),
            },
        )
    ]


def parse_attribution_validator_summary(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict) or path.name != "attribution_validator_summary.json":
        return []
    timestamp = str(payload.get("generated_at", "") or "")
    if not timestamp:
        return []
    events = [
        _artifact_event(
            path,
            1,
            timestamp,
            "AttributionValidatorSummary",
            {
                "timestamp_source": "attribution_validator_summary",
                "result": payload.get("result", ""),
                "attribution": payload.get("attribution", ""),
                "reason": payload.get("reason", ""),
                "run_count": payload.get("run_count"),
                "finding_count": payload.get("finding_count"),
                "error_count": payload.get("error_count"),
                "warn_count": payload.get("warn_count"),
            },
        )
    ]
    findings = payload.get("findings", []) if isinstance(payload.get("findings"), list) else []
    for index, finding in enumerate(findings, start=2):
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity", "") or finding.get("level", "") or "").upper()
        event_type = "AttributionFindingError" if severity == "ERROR" else "AttributionFindingWarn"
        events.append(_artifact_event(path, index, timestamp, event_type, {"timestamp_source": "attribution_validator_finding", **finding}))
    return events


def parse_doc_case_judge(path: Path) -> List[ValidationEvent]:
    try:
        payload = _json_load(path)
    except Exception:
        return []
    if not isinstance(payload, dict) or path.name != "judge.json" or "checks" not in payload:
        return []
    timestamp = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="milliseconds")
    checks = payload.get("checks", []) if isinstance(payload.get("checks"), list) else []
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    dialog = payload.get("dialog_behavior", {}) if isinstance(payload.get("dialog_behavior"), dict) else {}
    reason = str(payload.get("reason", "") or "")
    cloud_apply = False
    for check in checks:
        if isinstance(check, dict) and check.get("name") == "cloud_apply_success":
            cloud_apply = bool(check.get("passed")) and bool(check.get("actual"))
            break
    half_values = dialog.get("half_timeout_values", []) if isinstance(dialog.get("half_timeout_values"), list) else []
    full_values = dialog.get("full_timeout_values", []) if isinstance(dialog.get("full_timeout_values"), list) else []
    mode = ""
    if "half-duplex" in reason.lower() or half_values:
        mode = "half"
    if "full-duplex" in reason.lower() or full_values:
        mode = "full"
    common = {
        "timestamp_source": "doc_case_judge",
        "case_id": payload.get("case_id", ""),
        "name": payload.get("name", ""),
        "result": payload.get("result", ""),
        "confidence": payload.get("confidence", ""),
        "reason": reason,
        "mode": mode,
        "cloud_apply_success": cloud_apply,
        "half_timeout_values": half_values,
        "full_timeout_values": full_values,
        "restart_session_values": dialog.get("restart_session_values", []),
        "successful_response_count": dialog.get("successful_response_count", metrics.get("successful_response_count", 0)),
        "actual_online_asr_texts": dialog.get("actual_online_asr_texts", metrics.get("ap_online_asr_texts", [])),
        "recognized_command_keywords": metrics.get("recognized_command_keywords", []),
        "cp_wake_count": metrics.get("cp_wake_count", 0),
        "ap_wake_count": metrics.get("ap_wake_count", 0),
        "boot_marker_count": metrics.get("boot_marker_count", 0),
        "crash_marker_count": metrics.get("crash_marker_count", 0),
    }
    events = [
        _artifact_event(path, 1, timestamp, "DocCaseJudgeSummary", common),
        _artifact_event(path, 2, timestamp, _status_event("DocCaseJudge", str(payload.get("result", ""))), common),
    ]
    if mode:
        events.append(_artifact_event(path, 3, timestamp, "DuplexModeApplied", common))
    return events


def parse_json_artifacts(root: Path) -> List[ValidationEvent]:
    events: List[ValidationEvent] = []
    for path in root.rglob("*_runtime_events.jsonl"):
        events.extend(parse_runtime_events_jsonl(path))
    for path in root.rglob("playback.json"):
        events.extend(parse_playback_json(path))
    for path in root.rglob("*_command.json"):
        events.extend(parse_generic_playback_artifact(path))
    for path in root.rglob("interrupt_injection_result.json"):
        events.extend(parse_interrupt_injection_result(path))
    for path in root.rglob("interrupt_prerequisite_measurement.json"):
        events.extend(parse_interrupt_prerequisite_measurement(path))
    for path in root.rglob("summary.json"):
        events.extend(parse_hotspot_cycle_summary(path))
    for path in root.rglob("fa2_command_batch_summary.json"):
        events.extend(parse_fa2_command_batch_summary(path))
    for path in root.rglob("oneshot_matrix_summary.json"):
        events.extend(parse_oneshot_matrix_summary(path))
    for path in root.rglob("wake_matrix_summary.json"):
        events.extend(parse_wake_matrix_summary(path))
    for path in root.rglob("online_vad_special_summary.json"):
        events.extend(parse_online_vad_special_summary(path))
    for path in root.rglob("false_wake_quiet_summary.json"):
        events.extend(parse_false_wake_summary(path))
    for path in root.rglob("false_wake_playback_summary.json"):
        events.extend(parse_false_wake_summary(path))
    for path in root.rglob("attribution_validator_summary.json"):
        events.extend(parse_attribution_validator_summary(path))
    for path in root.rglob("judge.json"):
        events.extend(parse_doc_case_judge(path))
    return events
