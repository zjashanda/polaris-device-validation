#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analyze media/TTS/MP3 response evidence from Polaris run artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.events import ValidationEvent  # noqa: E402
from runtime.parsers.serial_log_parser import parse_log_tree  # noqa: E402

MEDIA_ERROR_PATTERNS = [
    r"http.*(?:timeout|retry|fail|error)",
    r"\[HTTPC\].*(?:ERR|ERROR|FAIL)",
    r"http_retry.*read_failed",
    r"tts.*(?:fail|error|timeout)",
    r"media.*(?:fail|error|timeout)",
    r"player.*(?:fail|error|timeout)",
    r"play(?:back)?.*(?:fail|error)",
    r"audioBroadcast.*(?:fail|error|timeout)",
]
IGNORED_MEDIA_ERROR_PATTERNS = [
    # PA manager's configured idle timeout after tone end is normal teardown,
    # not a playback failure.
    r"Refresh PA to OFF,\s*timeout\s+\d+,\s*by\s+\"tone_player_end\"",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def event_from_payload(item: Dict[str, Any]) -> ValidationEvent | None:
    fields = set(ValidationEvent.__dataclass_fields__.keys())
    try:
        return ValidationEvent(**{key: value for key, value in item.items() if key in fields})
    except Exception:
        return None


def collect_events(run_dir: Path) -> Tuple[List[ValidationEvent], List[str]]:
    events: List[ValidationEvent] = []
    sources: List[str] = []
    for package_path in run_dir.rglob("replay_package.json"):
        payload = load_json(package_path)
        timeline = payload.get("timeline", {}) if isinstance(payload.get("timeline"), dict) else {}
        before = len(events)
        for item in timeline.get("events", []) if isinstance(timeline.get("events"), list) else []:
            if isinstance(item, dict):
                event = event_from_payload(item)
                if event is not None:
                    events.append(event)
        if len(events) > before:
            sources.append(rel(package_path))
    if events:
        return events, sources
    parsed = parse_log_tree(run_dir)
    if parsed:
        events.extend(parsed)
        sources.append(rel(run_dir))
    return events, sources


def iter_log_lines(root: Path) -> Iterable[Tuple[Path, int, str]]:
    for path in root.rglob("*.log"):
        if not path.is_file() or ".clean." in path.name.lower():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line_no, line in enumerate(lines, start=1):
            yield path, line_no, line


def collect_media_errors(root: Path) -> List[Dict[str, Any]]:
    compiled = [re.compile(pattern, re.I) for pattern in MEDIA_ERROR_PATTERNS]
    ignored = [re.compile(pattern, re.I) for pattern in IGNORED_MEDIA_ERROR_PATTERNS]
    findings: List[Dict[str, Any]] = []
    for path, line_no, line in iter_log_lines(root):
        if any(pattern.search(line) for pattern in ignored):
            continue
        for pattern in compiled:
            if pattern.search(line):
                findings.append({"file": rel(path), "line_no": line_no, "pattern": pattern.pattern, "line": line.strip()[:500]})
                break
    return findings


def first_event(events: List[ValidationEvent], event_types: set[str]) -> Dict[str, Any]:
    for event in events:
        if event.event_type in event_types:
            return {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "source": event.source,
                "timestamp": event.timestamp,
                "file": event.file,
                "line_no": event.line_no,
                "marker": (event.payload or {}).get("marker", ""),
                "summary": (event.payload or {}).get("recognized_text") or (event.payload or {}).get("marker") or event.raw[:160],
            }
    return {}


def classify(counts: Counter[str], media_errors: List[Dict[str, Any]]) -> Tuple[str, str, str]:
    trigger_count = counts.get("WakeDetected", 0) + counts.get("ASRDetected", 0) + counts.get("CommandDetected", 0)
    tts_count = counts.get("TTSStarted", 0)
    media_started = counts.get("MediaStarted", 0)
    media_completed = counts.get("MediaCompleted", 0) + counts.get("AudioCompleted", 0)
    reboot_or_crash = counts.get("RebootDetected", 0) + counts.get("CrashDetected", 0)
    if reboot_or_crash:
        return "FAIL", "reboot_or_crash_during_media", "媒体/在线响应窗口观察到重启或崩溃事件。"
    if not counts:
        return "BLOCKED", "log_or_runtime_artifact_missing", "未找到可解析 runtime event 或串口日志，无法判断媒体响应。"
    if media_errors and not (tts_count or media_started):
        return "FAIL", "media_or_network_response_error", "发现媒体/TTS/HTTP/播放器错误，且未观察到有效 TTS/Media 启动。"
    if media_errors and (tts_count or media_started):
        return "PASS_WITH_WARNINGS", "media_error_with_partial_response", "观察到媒体响应，但同时存在媒体/TTS/HTTP/播放器错误，需要复核播放质量。"
    if media_started and media_completed:
        return "PASS", "media_started_and_completed", "观察到播放器启动和播放完成证据。"
    if media_started:
        return "PASS_WITH_WARNINGS", "media_started_without_completion", "观察到播放器启动，但未观察到播放完成；可能是长播、被打断或日志缺失。"
    if tts_count:
        return "PASS_WITH_WARNINGS", "tts_or_cloud_response_only", "观察到 TTS/云端播报响应，但未观察到明确播放器启动。"
    if trigger_count:
        return "FAIL", "no_media_response_after_asr_or_command", "已有唤醒/ASR/命令证据，但未观察到 TTS/Media 响应。"
    return "BLOCKED", "no_voice_trigger_evidence", "未观察到唤醒/ASR/命令触发，不能判断媒体响应链路。"


def analyze_run(run_dir: Path) -> Dict[str, Any]:
    events, sources = collect_events(run_dir)
    counts = Counter(event.event_type for event in events)
    media_errors = collect_media_errors(run_dir)
    result, attribution, reason = classify(counts, media_errors)
    response = {
        "cloud_or_tts_response_observed": counts.get("TTSStarted", 0) > 0,
        "device_player_started_observed": counts.get("MediaStarted", 0) > 0,
        "device_player_completed_observed": counts.get("MediaCompleted", 0) + counts.get("AudioCompleted", 0) > 0,
        "media_error_observed": bool(media_errors),
        "reboot_or_crash_observed": counts.get("RebootDetected", 0) + counts.get("CrashDetected", 0) > 0,
        "first_tts": first_event(events, {"TTSStarted"}),
        "first_media_started": first_event(events, {"MediaStarted"}),
        "first_media_completed": first_event(events, {"MediaCompleted", "AudioCompleted"}),
    }
    return {
        "schema": "polaris.media_response_oracle.v1",
        "generated_at": now_iso(),
        "run_dir": rel(run_dir),
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "event_sources": sources,
        "event_counts": dict(sorted(counts.items())),
        "media_response": response,
        "media_errors": media_errors[:50],
        "media_error_count": len(media_errors),
        "oracle_scope": "log_event_only",
        "oracle_limitations": [
            "V1 只能证明日志/事件层面的 TTS、播放器、媒体错误与完成 marker。",
            "没有配置 loopback/capture 时，不能证明扬声器真实出声、音质正常或音频完整。",
            "长播、被打断或项目私有 marker 缺失时，完成事件可能缺失，应结合 Event Graph 和原始日志复核。",
        ],
    }


def render_markdown(payload: Dict[str, Any]) -> str:
    lines = ["# Polaris 媒体/TTS/MP3 响应 Oracle v1", "", f"- run_dir：`{payload.get('run_dir', '')}`", f"- result：`{payload.get('result', '')}`", f"- attribution：`{payload.get('attribution', '')}`", f"- reason：{payload.get('reason', '')}", f"- oracle_scope：`{payload.get('oracle_scope', '')}`", "", "## 响应链路", ""]
    response = payload.get("media_response", {}) if isinstance(payload.get("media_response"), dict) else {}
    for key in ["cloud_or_tts_response_observed", "device_player_started_observed", "device_player_completed_observed", "media_error_observed", "reboot_or_crash_observed"]:
        lines.append(f"- `{key}`：`{response.get(key)}`")
    lines += ["", "## 事件统计", ""]
    for key, value in (payload.get("event_counts", {}) or {}).items():
        lines.append(f"- `{key}`：`{value}`")
    if payload.get("media_errors"):
        lines += ["", "## 媒体错误样本", ""]
        for item in payload.get("media_errors", [])[:10]:
            lines.append(f"- `{item.get('file')}:{item.get('line_no')}` pattern=`{item.get('pattern')}` {item.get('line')}")
    lines += ["", "## 限制", ""]
    for item in payload.get("oracle_limitations", []):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Polaris media/TTS/MP3 response oracle from a run directory.")
    parser.add_argument("--run", required=True)
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()
    run_dir = resolve_path(args.run)
    if not run_dir.exists():
        raise SystemExit(f"run directory not found: {run_dir}")
    payload = analyze_run(run_dir)
    out_dir = resolve_path(args.out_dir) if args.out_dir else run_dir / "media_oracle"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "media_response_oracle.json", payload)
    (out_dir / "media_response_oracle.md").write_text(render_markdown(payload), encoding="utf-8")
    print(out_dir)
    print(f"result={payload['result']} attribution={payload['attribution']} media_errors={payload['media_error_count']}")
    return 0 if payload["result"] in {"PASS", "PASS_WITH_WARNINGS", "BLOCKED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
