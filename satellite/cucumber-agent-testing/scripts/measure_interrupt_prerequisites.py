#!/usr/bin/env python3
"""Measure self-play windows for interruption prerequisites.

This script is intentionally independent from LLM generation: it can either
run a small FA2 wake+command batch on real hardware, or analyze an existing
FA2 batch artifact, then decide which command is usable as the "device is
currently speaking" precondition for wake/recognition interruption tests.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


BASE = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DEVICE_KEY = ""
DEFAULT_WAKE_WORD = "小美小美"

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

try:
    from tools.core.polaris_runtime import parse_prefixed_timestamp
    from tools.validation.polaris_fa2_command_batch import run_command_batch
except Exception:  # pragma: no cover - fallback for offline parsing outside repo.
    def parse_prefixed_timestamp(line: str) -> Optional[datetime]:  # type: ignore[no-redef]
        match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)", line)
        if not match:
            return None
        try:
            return datetime.fromisoformat(match.group(1))
        except ValueError:
            return None
    run_command_batch = None  # type: ignore[assignment]


URL_RE = re.compile(r"https?://[^\"\\\s}]+", re.I)
TTS_REPORT_PLAY_RE = re.compile(r"ttsplayer report state:\s*play\s*2", re.I)
TTS_REPORT_STOP_RE = re.compile(r"ttsplayer report state:\s*stop\s*6", re.I)
TTS_PLAYING_RE = re.compile(r"TTS playing with\s+(https?://[^\"\\\s}]+)", re.I)
TTS_PLAYER_STATUS_PLAY_RE = re.compile(r"ttsplayer status:\s*2\b", re.I)
TTS_PLAYER_STATUS_STOP_RE = re.compile(r"ttsplayer status:\s*6\b", re.I)
TTS_PLAYER_PLAY_RE = re.compile(r"ttsplayer play:\s*(https?://[^\"\\\s}]+)", re.I)
PLAY_AUDIO_HTTP_RE = re.compile(r"play audio\s+(https?://[^\"\\\s}]+)", re.I)
PLAY_COMPLETE_RE = re.compile(r"play complete,\s*all len", re.I)
TONE_EVT_2_RE = re.compile(r"tone player evt\s+2\b", re.I)
TONE_EVT_6_RE = re.compile(r"tone player evt\s+6\b", re.I)
WB_PLAY_START_RE = re.compile(r"local player status\s+2\s+PLAYING", re.I)
WB_PLAY_END_RE = re.compile(r"local player status\s+6\s+PLAYBACK_COMPLETE", re.I)
BOOT_RE = re.compile(r"\bboot\b|Boot Reason|crash|assert|exception", re.I)


@dataclass
class Event:
    ts: datetime
    kind: str
    line: str
    url: str = ""
    source: str = "COM14"


@dataclass
class PlayWindow:
    start: datetime
    end: datetime
    source: str
    start_kind: str
    stop_kind: str
    start_line: str
    stop_line: str
    url: str = ""

    @property
    def duration_ms(self) -> int:
        return int((self.end - self.start).total_seconds() * 1000)


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_output_dir() -> Path:
    bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
    if bdd_run_dir:
        return Path(bdd_run_dir).resolve() / "interrupt_measurement"
    return BASE / "debug" / "interrupt_measurements" / stamp()


def latest_candidate_file() -> Optional[Path]:
    roots = list((BASE / "debug" / "interrupt_prerequisites").glob("*/interrupt_prerequisite_candidates.json"))
    if not roots:
        return None
    return max(roots, key=lambda path: path.stat().st_mtime)


def first_url(line: str) -> str:
    match = URL_RE.search(line)
    return match.group(0).rstrip('",') if match else ""


def parse_ts(line: str) -> Optional[datetime]:
    try:
        return parse_prefixed_timestamp(line)
    except Exception:
        return None


def read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def load_candidate_records(path: Optional[Path]) -> List[Dict[str, Any]]:
    if not path or not path.exists():
        return []
    payload = load_json(path)
    if isinstance(payload, dict):
        records = payload.get("candidates", [])
    else:
        records = payload
    return [dict(item) for item in records if isinstance(item, dict)]


def candidate_allowed(candidate: Dict[str, Any], include_online: bool, types: List[str]) -> bool:
    if not include_online and bool(candidate.get("requires_online")):
        return False
    if types and str(candidate.get("type", "")) not in types:
        return False
    phrase = str(candidate.get("phrase", "")).strip()
    return bool(phrase)


def select_candidates(
    candidates: List[Dict[str, Any]],
    *,
    include_online: bool,
    types: List[str],
    limit: int,
) -> List[Dict[str, Any]]:
    filtered = [item for item in candidates if candidate_allowed(item, include_online, types)]
    filtered.sort(key=lambda item: (-int(item.get("priority", 0) or 0), str(item.get("id", ""))))
    if limit > 0:
        filtered = filtered[:limit]
    return filtered


def read_plain_commands(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    seen = set()
    rows: List[Dict[str, Any]] = []
    for index, raw in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), start=1):
        phrase = raw.strip()
        if not phrase or phrase.startswith("#") or phrase in seen:
            continue
        seen.add(phrase)
        rows.append(
            {
                "id": f"command_file.{index}",
                "type": "command_file",
                "phrase": phrase,
                "requires_online": False,
                "priority": 0,
            }
        )
    return rows


def write_candidate_command_file(candidates: List[Dict[str, Any]], output_dir: Path) -> Path:
    path = output_dir / "measurement_candidates.txt"
    path.write_text("\n".join(str(item.get("phrase", "")).strip() for item in candidates) + "\n", encoding="utf-8")
    return path


def quote_cmd(cmd: Iterable[str]) -> str:
    parts: List[str] = []
    for item in cmd:
        if not item:
            parts.append('""')
        elif any(ch.isspace() for ch in item) or any(ch in item for ch in ['"', "'", "&"]):
            parts.append('"' + item.replace('"', '\\"') + '"')
        else:
            parts.append(item)
    return " ".join(parts)


def run_fa2_batch(
    command_file: Path,
    output_dir: Path,
    *,
    device_key: str,
    wake_word: str,
    label: str,
    post_command_gap_ms: int,
    wake_gap_ms: int,
) -> Tuple[int, str, Optional[Path]]:
    log_path = output_dir / "run_fa2_batch.log"
    with log_path.open("w", encoding="utf-8", newline="") as log:
        log.write(
            "adapter-only fa2 batch: "
            f"label={label} wake_gap_ms={wake_gap_ms} post_command_gap_ms={post_command_gap_ms}\n"
        )
        log.flush()
        if run_command_batch is None:
            raise RuntimeError("run_command_batch is unavailable outside the Polaris repository")
        batch = run_command_batch(
            command_file=command_file,
            device_key=device_key,
            wake_word=wake_word,
            label=label,
            post_command_gap_ms=post_command_gap_ms,
            wake_gap_ms=wake_gap_ms,
        )
        log.write(str(batch.get("output_dir", "")) + "\n")
        log.write(json.dumps({"total": batch.get("total"), "counts": batch.get("counts")}, ensure_ascii=False) + "\n")
        returncode = int(batch.get("returncode", 0) or 0)

    batch_dir: Optional[Path] = batch.get("output_dir") if isinstance(batch.get("output_dir"), Path) else None
    if batch_dir is None:
        batch_dir = latest_fa2_dir_from_session(label)
    return returncode, rel(log_path), batch_dir


def latest_fa2_dir_from_session(label: str) -> Optional[Path]:
    marker = WORKSPACE_ROOT / ".current_result_dir"
    session_dir = Path(marker.read_text(encoding="utf-8").strip()) if marker.exists() else WORKSPACE_ROOT / "result"
    if not session_dir.is_absolute():
        session_dir = WORKSPACE_ROOT / session_dir
    roots = list((session_dir / "artifacts" / "misc" / "fa2").glob(f"*{label}*"))
    roots = [path for path in roots if (path / "fa2_command_batch_summary.json").exists()]
    if not roots:
        return None
    return max(roots, key=lambda path: path.stat().st_mtime)


def latest_fa2_dir_from_run(run_dir: Path, label: str) -> Optional[Path]:
    roots = list((run_dir / "session" / "artifacts" / "misc" / "fa2").glob(f"*{label}*"))
    if not roots:
        roots = list((run_dir / "session" / "artifacts" / "misc" / "fa2").glob("*"))
    roots = [path for path in roots if (path / "fa2_command_batch_summary.json").exists()]
    if not roots:
        return None
    return max(roots, key=lambda path: path.stat().st_mtime)


def build_events(lines: List[str], source: str) -> Tuple[List[Event], List[Event]]:
    starts: List[Event] = []
    stops: List[Event] = []
    last_url = ""
    for line in lines:
        ts = parse_ts(line)
        if ts is None:
            continue
        url = first_url(line) or last_url
        if url:
            last_url = url
        playing_match = TTS_PLAYING_RE.search(line)
        play_request_match = TTS_PLAYER_PLAY_RE.search(line) or PLAY_AUDIO_HTTP_RE.search(line)
        if TTS_REPORT_PLAY_RE.search(line):
            starts.append(Event(ts=ts, kind="tts_report_play", line=line, url=first_url(line), source=source))
        elif playing_match:
            starts.append(Event(ts=ts, kind="tts_keytime_playing", line=line, url=playing_match.group(1), source=source))
        elif TTS_PLAYER_STATUS_PLAY_RE.search(line):
            starts.append(Event(ts=ts, kind="tts_status_2", line=line, url=url, source=source))
        elif TONE_EVT_2_RE.search(line) and "APP_PLAYER" in line:
            # Fallback for tone/local prompts without a URL.
            starts.append(Event(ts=ts, kind="tone_evt_2", line=line, url="", source=source))
        elif play_request_match:
            starts.append(Event(ts=ts, kind="tts_play_request", line=line, url=play_request_match.group(1), source=source))

        if TTS_REPORT_STOP_RE.search(line):
            stops.append(Event(ts=ts, kind="tts_report_stop", line=line, url=first_url(line), source=source))
        elif TTS_PLAYER_STATUS_STOP_RE.search(line):
            stops.append(Event(ts=ts, kind="tts_status_6", line=line, url=url, source=source))
        elif PLAY_COMPLETE_RE.search(line):
            stops.append(Event(ts=ts, kind="play_complete", line=line, url=url, source=source))
        elif TONE_EVT_6_RE.search(line) and "APP_PLAYER" in line:
            stops.append(Event(ts=ts, kind="tone_evt_6", line=line, url="", source=source))
    return starts, stops


def build_wb_events(lines: List[str], source: str = "COM13") -> Tuple[List[Event], List[Event]]:
    starts: List[Event] = []
    stops: List[Event] = []
    for line in lines:
        ts = parse_ts(line)
        if ts is None:
            continue
        if WB_PLAY_START_RE.search(line):
            starts.append(Event(ts=ts, kind="wb_local_playing", line=line, source=source))
        if WB_PLAY_END_RE.search(line):
            stops.append(Event(ts=ts, kind="wb_playback_complete", line=line, source=source))
    return starts, stops


START_PRIORITY = {
    "tts_report_play": 1,
    "tts_keytime_playing": 2,
    "tts_status_2": 3,
    "wb_local_playing": 4,
    "tone_evt_2": 5,
    "tts_play_request": 6,
}

STOP_PRIORITY = {
    "tts_report_stop": 1,
    "wb_playback_complete": 1,
    "tts_status_6": 2,
    "play_complete": 3,
    "tone_evt_6": 4,
}


def pair_windows(starts: List[Event], stops: List[Event], *, max_duration_ms: int) -> List[PlayWindow]:
    starts = prefer_start_events(starts)
    starts = sorted(starts, key=lambda item: (item.ts, START_PRIORITY.get(item.kind, 99)))
    stops = sorted(stops, key=lambda item: (item.ts, STOP_PRIORITY.get(item.kind, 99)))
    windows: List[PlayWindow] = []
    used_stops: set[int] = set()
    for start in starts:
        candidates: List[Tuple[int, Event]] = []
        for stop_index, stop in enumerate(stops):
            if stop_index in used_stops:
                continue
            if stop.ts <= start.ts:
                continue
            duration_ms = int((stop.ts - start.ts).total_seconds() * 1000)
            if duration_ms > max_duration_ms:
                continue
            if not events_compatible(start, stop):
                continue
            if start.url and stop.url and start.url != stop.url:
                continue
            candidates.append((stop_index, stop))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (STOP_PRIORITY.get(item[1].kind, 99), item[1].ts))
        stop_index, stop = candidates[0]
        used_stops.add(stop_index)
        windows.append(
            PlayWindow(
                start=start.ts,
                end=stop.ts,
                source=start.source,
                start_kind=start.kind,
                stop_kind=stop.kind,
                start_line=start.line,
                stop_line=stop.line,
                url=start.url or stop.url,
            )
        )
    return dedupe_windows(windows)


def prefer_start_events(starts: List[Event]) -> List[Event]:
    preferred_kinds = {"tts_report_play", "tts_keytime_playing"}
    usable: List[Event] = []
    for item in starts:
        if item.kind == "tts_keytime_playing" and item.url:
            report_play = [
                other
                for other in starts
                if other.url == item.url
                and other.kind == "tts_report_play"
                and abs((other.ts - item.ts).total_seconds()) <= 1.5
            ]
            if report_play:
                continue
        if item.kind == "tts_play_request" and item.url:
            better = [
                other
                for other in starts
                if other.url == item.url
                and other.kind in preferred_kinds | {"tts_status_2"}
                and timedelta(milliseconds=0) <= other.ts - item.ts <= timedelta(milliseconds=3000)
            ]
            if better:
                continue
        if item.kind == "tts_status_2" and item.url:
            better = [
                other
                for other in starts
                if other.url == item.url
                and other.kind in preferred_kinds
                and abs((other.ts - item.ts).total_seconds()) <= 1.5
            ]
            if better:
                continue
        usable.append(item)
    return usable


def events_compatible(start: Event, stop: Event) -> bool:
    """Avoid pairing a prompt/tone start with an unrelated cloud TTS stop."""
    if start.kind.startswith("tone_"):
        return stop.kind in {"tone_evt_6", "play_complete"} and not stop.url
    if stop.kind.startswith("tone_") and start.url:
        return False
    if stop.kind in {"tts_report_stop", "tts_status_6"} and stop.url and not start.url:
        return False
    if start.kind == "tts_play_request" and stop.kind in {"tone_evt_6"}:
        return False
    return True


def dedupe_windows(windows: List[PlayWindow]) -> List[PlayWindow]:
    selected: Dict[Tuple[str, int], PlayWindow] = {}
    for window in windows:
        key = (window.url or window.source, int(window.end.timestamp() * 10))
        old = selected.get(key)
        if old is None:
            selected[key] = window
            continue
        old_score = START_PRIORITY.get(old.start_kind, 99) + STOP_PRIORITY.get(old.stop_kind, 99)
        new_score = START_PRIORITY.get(window.start_kind, 99) + STOP_PRIORITY.get(window.stop_kind, 99)
        if new_score < old_score or (new_score == old_score and window.duration_ms > old.duration_ms):
            selected[key] = window
    return sorted(selected.values(), key=lambda item: item.start)


def find_boot_or_crash(lines: Iterable[str], start: datetime, end: datetime) -> List[str]:
    found: List[str] = []
    for line in lines:
        ts = parse_ts(line)
        if ts is None or not (start <= ts <= end):
            continue
        if BOOT_RE.search(line):
            found.append(line)
            if len(found) >= 5:
                break
    return found


def load_manifest(batch_dir: Path) -> Dict[str, Any]:
    manifests = list((batch_dir / "audio").glob("*.json"))
    if not manifests:
        raise FileNotFoundError(f"FA2 batch audio manifest not found under {batch_dir / 'audio'}")
    return load_json(manifests[0])


def window_to_payload(window: PlayWindow) -> Dict[str, Any]:
    return {
        "source": window.source,
        "start": window.start.isoformat(timespec="milliseconds"),
        "end": window.end.isoformat(timespec="milliseconds"),
        "duration_ms": window.duration_ms,
        "start_kind": window.start_kind,
        "stop_kind": window.stop_kind,
        "url": window.url,
        "start_line": window.start_line,
        "stop_line": window.stop_line,
    }


def choose_injection_offset(duration_ms: int, guard_ms: int) -> Optional[int]:
    if duration_ms <= guard_ms * 2:
        return None
    return max(guard_ms, min(duration_ms - guard_ms, int(duration_ms * 0.45)))


def analyze_fa2_batch(
    batch_dir: Path,
    output_dir: Path,
    *,
    candidates: List[Dict[str, Any]],
    minimum_duration_ms: int,
    injection_guard_ms: int,
    max_window_duration_ms: int,
) -> Dict[str, Any]:
    summary = load_json(batch_dir / "fa2_command_batch_summary.json")
    manifest = load_manifest(batch_dir)
    playback = load_json(batch_dir / "playback.json")
    playback_started_at = datetime.fromisoformat(str(playback["playback_started_at"]))
    playback_returncode = int(playback.get("returncode", -1))
    com14_lines = read_lines(batch_dir / "full_window_logs" / "COM14.log")
    com13_lines = read_lines(batch_dir / "full_window_logs" / "COM13.log")
    ap_starts, ap_stops = build_events(com14_lines, "COM14")
    wb_starts, wb_stops = build_wb_events(com13_lines, "COM13")
    all_windows = pair_windows(ap_starts, ap_stops, max_duration_ms=max_window_duration_ms) + pair_windows(
        wb_starts, wb_stops, max_duration_ms=max_window_duration_ms
    )
    all_windows.sort(key=lambda item: item.start)

    candidate_by_phrase = {str(item.get("phrase", "")).strip(): item for item in candidates}
    per_command_dir = batch_dir / "per_command"
    entries = manifest.get("entries", [])
    rows: List[Dict[str, Any]] = []

    for offset, entry in enumerate(entries):
        command = str(entry.get("command", "")).strip()
        next_entry = entries[offset + 1] if offset + 1 < len(entries) else None
        command_start = playback_started_at + timedelta(milliseconds=int(entry.get("command_start_ms", 0) or 0))
        command_end = playback_started_at + timedelta(milliseconds=int(entry.get("command_end_ms", 0) or 0))
        segment_end = playback_started_at + timedelta(milliseconds=int(entry.get("segment_end_ms", 0) or 0))
        if next_entry:
            start_upper = playback_started_at + timedelta(milliseconds=int(next_entry.get("command_start_ms", 0) or 0)) - timedelta(
                milliseconds=250
            )
        else:
            start_upper = segment_end + timedelta(milliseconds=5000)
        start_lower = command_start - timedelta(milliseconds=250)
        matching_windows = [
            window
            for window in all_windows
            if start_lower <= window.start <= start_upper and window.duration_ms > 0
        ]
        matching_windows.sort(key=lambda item: item.duration_ms, reverse=True)
        best = matching_windows[0] if matching_windows else None
        detail_path = per_command_dir / f"{int(entry.get('index', offset + 1)):03d}.json"
        detail = load_json(detail_path) if detail_path.exists() else {}
        diagnosis = dict(detail.get("diagnosis", {}))
        metrics = dict(detail.get("metrics", {}))
        boot_lines = find_boot_or_crash(com14_lines + com13_lines, command_start, segment_end + timedelta(seconds=5))
        duration_ms = best.duration_ms if best else 0
        injection_offset_ms = choose_injection_offset(duration_ms, injection_guard_ms) if best else None

        if playback_returncode != 0:
            verdict = "BLOCKED"
            attribution = "audio_playback_or_device_key"
            reason = f"FA2 批量播放失败，returncode={playback_returncode}。"
        elif str(diagnosis.get("result", "")) != "PASS":
            verdict = "UNUSABLE"
            attribution = "candidate_not_recognized_or_precondition"
            reason = f"候选命令未形成可靠识别闭环：{diagnosis.get('reason', '')}"
        elif boot_lines:
            verdict = "BLOCKED"
            attribution = "device_reboot_or_log_instability"
            reason = "候选窗口内出现 boot/crash/exception 类日志，不能作为稳定打断前置。"
        elif best is None:
            verdict = "NEEDS_REVIEW"
            attribution = "self_play_marker_missing"
            reason = "命令识别通过，但未解析到可配对的自播/TTS start-end 标记。"
        elif duration_ms < minimum_duration_ms:
            verdict = "UNUSABLE"
            attribution = "self_play_too_short"
            reason = f"自播时长 {duration_ms}ms 小于最小要求 {minimum_duration_ms}ms。"
        elif injection_offset_ms is None:
            verdict = "UNUSABLE"
            attribution = "no_safe_injection_point"
            reason = f"自播时长 {duration_ms}ms 无法留下前后保护时间 {injection_guard_ms}ms。"
        else:
            verdict = "USABLE"
            attribution = "pass"
            reason = f"识别闭环通过，解析到 {duration_ms}ms 自播窗口，可在 +{injection_offset_ms}ms 注入打断音频。"

        candidate = candidate_by_phrase.get(command, {})
        row = {
            "index": int(entry.get("index", offset + 1)),
            "candidate_id": candidate.get("id", ""),
            "candidate_type": candidate.get("type", ""),
            "phrase": command,
            "fa2_result": diagnosis.get("result", ""),
            "verdict": verdict,
            "attribution": attribution,
            "reason": reason,
            "self_play_duration_ms": duration_ms,
            "injection_offset_ms": injection_offset_ms if injection_offset_ms is not None else "",
            "response_start_delta_ms": int((best.start - command_end).total_seconds() * 1000) if best else "",
            "window_source": best.source if best else "",
            "window_start_kind": best.start_kind if best else "",
            "window_stop_kind": best.stop_kind if best else "",
            "window_count": len(matching_windows),
            "cp_wake_count": metrics.get("cp_wake_count", 0),
            "ap_wake_count": metrics.get("ap_wake_count", 0),
            "asr_total": metrics.get("asr_total", 0),
            "ap_online_asr_texts": "|".join(str(item) for item in metrics.get("ap_online_asr_texts", [])),
            "recognized_command_keywords": "|".join(str(item) for item in metrics.get("recognized_command_keywords", [])),
            "detail_path": rel(detail_path) if detail_path.exists() else "",
            "best_window": window_to_payload(best) if best else {},
            "all_windows": [window_to_payload(item) for item in matching_windows[:5]],
            "boot_or_crash_lines": boot_lines,
            "candidate": candidate,
        }
        rows.append(row)

    usable = [row for row in rows if row["verdict"] == "USABLE"]
    usable.sort(key=lambda item: (-int(item["self_play_duration_ms"] or 0), -int(candidate_by_phrase.get(item["phrase"], {}).get("priority", 0) or 0)))
    selected = usable[0] if usable else None
    counts: Dict[str, int] = {}
    for row in rows:
        counts[str(row["verdict"])] = counts.get(str(row["verdict"]), 0) + 1

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "batch_dir": rel(batch_dir),
        "output_dir": rel(output_dir),
        "playback_returncode": playback_returncode,
        "minimum_duration_ms": minimum_duration_ms,
        "injection_guard_ms": injection_guard_ms,
        "max_window_duration_ms": max_window_duration_ms,
        "counts": counts,
        "total": len(rows),
        "selected": selected,
        "rows": rows,
        "fa2_summary": rel(batch_dir / "fa2_command_batch_summary.json"),
        "audio_manifest": rel(next((batch_dir / "audio").glob("*.json"))),
    }
    write_json(output_dir / "interrupt_prerequisite_measurement.json", result)
    if selected:
        write_json(output_dir / "selected_interrupt_prerequisite.json", selected)
    write_measurement_csv(output_dir / "interrupt_prerequisite_measurement.csv", rows)
    (output_dir / "interrupt_prerequisite_measurement_report.md").write_text(
        render_report(result),
        encoding="utf-8",
    )
    return result


def write_measurement_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "index",
        "candidate_id",
        "candidate_type",
        "phrase",
        "fa2_result",
        "verdict",
        "attribution",
        "self_play_duration_ms",
        "injection_offset_ms",
        "response_start_delta_ms",
        "window_source",
        "window_start_kind",
        "window_stop_kind",
        "window_count",
        "reason",
        "ap_online_asr_texts",
        "recognized_command_keywords",
        "detail_path",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def render_report(result: Dict[str, Any]) -> str:
    selected = result.get("selected")
    lines = [
        "# 打断前置自播测量报告",
        "",
        f"- 生成时间：`{result.get('generated_at')}`",
        f"- FA2 批量证据：`{result.get('batch_dir')}`",
        f"- 最小时长要求：`{result.get('minimum_duration_ms')}ms`",
        f"- 注入保护时间：`{result.get('injection_guard_ms')}ms`",
        f"- 统计：`{json.dumps(result.get('counts', {}), ensure_ascii=False)}`",
        "",
    ]
    if selected:
        lines.extend(
            [
                "## 选中的打断前置",
                "",
                f"- 候选：`{selected.get('phrase')}`",
                f"- 候选 ID：`{selected.get('candidate_id')}`",
                f"- 自播时长：`{selected.get('self_play_duration_ms')}ms`",
                f"- 建议注入点：自播开始后 `+{selected.get('injection_offset_ms')}ms`",
                f"- 证据来源：`{selected.get('window_source')}` `{selected.get('window_start_kind')}` -> `{selected.get('window_stop_kind')}`",
                f"- 归因：`{selected.get('attribution')}`",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## 选中的打断前置",
                "",
                "- 暂未选出可用候选；后续打断用例应判为 `BLOCKED/NEEDS_REVIEW`，不能算固件 FAIL。",
                "",
            ]
        )
    lines.extend(
        [
            "## 明细",
            "",
            "| # | 候选 | 结论 | 时长(ms) | 注入点(ms) | 来源 | 归因 | 原因 |",
            "|---|---|---|---:|---:|---|---|---|",
        ]
    )
    for row in result.get("rows", []):
        reason = str(row.get("reason", "")).replace("\n", " ")
        if len(reason) > 90:
            reason = reason[:87] + "..."
        lines.append(
            f"| {row.get('index')} | {row.get('phrase')} | `{row.get('verdict')}` | "
            f"{row.get('self_play_duration_ms') or 0} | {row.get('injection_offset_ms') or ''} | "
            f"{row.get('window_source')}/{row.get('window_start_kind')}->{row.get('window_stop_kind')} | "
            f"`{row.get('attribution')}` | {reason} |"
        )
    lines.extend(
        [
            "",
            "## 断言口径",
            "",
            "- 候选未识别、播放失败、串口缺失、无法解析 start/end 时，只说明该候选不能作为打断前置，不直接判固件打断失败。",
            "- 只有 `USABLE` 候选才能进入后续 wake interrupt / command interrupt 主流程。",
            "- 后续真正打断时，注入音频必须落在自播 start/end 之间，并避开起播和结束保护时间。",
            "",
        ]
    )
    return "\n".join(lines)


def resolve_batch_dir(args: argparse.Namespace, output_dir: Path) -> Tuple[Optional[Path], Dict[str, Any]]:
    metadata: Dict[str, Any] = {}
    if args.fa2_dir:
        return Path(args.fa2_dir).resolve(), metadata
    if args.run_dir:
        return latest_fa2_dir_from_run(Path(args.run_dir).resolve(), args.label), metadata
    if args.run_batch:
        candidate_records = load_candidate_records(args.candidate_file)
        if candidate_records:
            candidates = select_candidates(
                candidate_records,
                include_online=args.include_online,
                types=args.candidate_type,
                limit=args.candidate_limit,
            )
        else:
            candidates = read_plain_commands(args.command_file)
            if args.candidate_limit > 0:
                candidates = candidates[: args.candidate_limit]
        if not candidates:
            raise SystemExit("没有可测量的打断前置候选，请检查 --candidate-file 或 --command-file。")
        command_file = write_candidate_command_file(candidates, output_dir)
        write_json(output_dir / "measurement_candidates.json", candidates)
        returncode, log_path, batch_dir = run_fa2_batch(
            command_file,
            output_dir,
            device_key=args.device_key,
            wake_word=args.wake_word,
            label=args.label,
            post_command_gap_ms=args.post_command_gap_ms,
            wake_gap_ms=args.wake_gap_ms,
        )
        metadata["run_batch_returncode"] = returncode
        metadata["run_batch_log"] = log_path
        return batch_dir, metadata
    return latest_fa2_dir_from_session(args.label), metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure interrupt prerequisite self-play duration from FA2 evidence.")
    parser.add_argument("--candidate-file", type=Path, default=None)
    parser.add_argument("--command-file", type=Path, default=Path("docs") / "fa2命令词.txt")
    parser.add_argument("--fa2-dir", default="", help="直接分析指定 FA2 batch artifact 目录")
    parser.add_argument("--run-dir", default="", help="分析指定 Cucumber run_dir 下最新 FA2 batch")
    parser.add_argument("--run-batch", action="store_true", help="先执行真实 FA2 候选批量播放，再测量自播时长")
    parser.add_argument("--candidate-limit", type=int, default=8)
    parser.add_argument(
        "--candidate-type",
        action="append",
        default=[],
        help="候选 type 过滤，可重复；默认不过滤但会排除 requires_online=true",
    )
    parser.add_argument("--include-online", action="store_true", help="允许在线天气/播歌等候选进入测量")
    parser.add_argument("--device-key", default=DEFAULT_DEVICE_KEY)
    parser.add_argument("--wake-word", default=DEFAULT_WAKE_WORD)
    parser.add_argument("--label", default="bdd_interrupt_prerequisite")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--minimum-duration-ms", type=int, default=2500)
    parser.add_argument("--injection-guard-ms", type=int, default=800)
    parser.add_argument("--max-window-duration-ms", type=int, default=30000)
    parser.add_argument("--wake-gap-ms", type=int, default=900)
    parser.add_argument("--post-command-gap-ms", type=int, default=10000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.candidate_file is None:
        args.candidate_file = latest_candidate_file()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = load_candidate_records(args.candidate_file)
    if not candidates and args.command_file:
        candidates = read_plain_commands(args.command_file)
    batch_dir, metadata = resolve_batch_dir(args, output_dir)
    if batch_dir is None or not (batch_dir / "fa2_command_batch_summary.json").exists():
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": "BLOCKED",
            "attribution": "fa2_artifact_missing",
            "reason": "未找到可解析的 FA2 batch artifact。",
            "metadata": metadata,
        }
        write_json(output_dir / "interrupt_prerequisite_measurement.json", payload)
        (output_dir / "interrupt_prerequisite_measurement_report.md").write_text(render_report({"counts": {"BLOCKED": 1}, "rows": []}), encoding="utf-8")
        print(output_dir)
        print(json.dumps({"status": "BLOCKED", "reason": payload["reason"]}, ensure_ascii=False))
        return 2
    result = analyze_fa2_batch(
        batch_dir,
        output_dir,
        candidates=candidates,
        minimum_duration_ms=args.minimum_duration_ms,
        injection_guard_ms=args.injection_guard_ms,
        max_window_duration_ms=args.max_window_duration_ms,
    )
    result["metadata"] = metadata
    write_json(output_dir / "interrupt_prerequisite_measurement.json", result)
    status = "PASS" if result.get("selected") else "NEEDS_REVIEW"
    print(output_dir)
    print(
        json.dumps(
            {
                "status": status,
                "counts": result.get("counts", {}),
                "selected": (result.get("selected") or {}).get("phrase", ""),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.get("selected") else 3


if __name__ == "__main__":
    raise SystemExit(main())

