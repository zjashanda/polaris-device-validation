#!/usr/bin/env python3
from pathlib import Path
import sys

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from tools.audio.polaris_audio_builder import build_from_case
from tools.core.polaris_adapter_bridge import run_audio_playback_adapter
from tools.core.polaris_config import read_env_config
from tools.core.polaris_runtime import current_session_dir, new_artifact_dir, read_lines_between, workspace_root
from tools.probe.polaris_state_probe import diff_states, snapshot


ENV_CONFIG = workspace_root() / "config" / "polaris_env.json"
TONE_RE = re.compile(r"play next tone (?P<tone>\d+):", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
PLAYER_START_RES = (
    re.compile(r"local player status\s+2\s+playing\b", re.I),
    re.compile(r"soundplayer status:\s*2\b", re.I),
)
PLAYER_END_RES = (
    re.compile(r"local player status\s+6\s+playback_complete\b", re.I),
    re.compile(r"soundplayer status:\s*6\b", re.I),
)
JUDGE_VERSION = "2026-04-15-r2"


def load_case(case_path: Path) -> dict:
    return yaml.safe_load(case_path.read_text(encoding="utf-8"))


def load_env() -> dict:
    return read_env_config()


def default_playback_device_key(env: dict) -> str:
    audio = env.get("audio", {}) if isinstance(env.get("audio"), dict) else {}
    return str(env.get("default_playback_device_key") or audio.get("default_playback_device_key") or "").strip()


def playback_device_label(device_key: str) -> str:
    return str(device_key or "").strip() or "system-default"


def sanitize_line(line: str) -> str:
    return ANSI_RE.sub("", line).replace("\r", "").rstrip("\n")


def sanitize_logs(window_logs: Dict[str, List[str]]) -> Dict[str, List[str]]:
    return {port: [sanitize_line(line) for line in lines] for port, lines in window_logs.items()}


def matches_any_regex(line: str, patterns: Tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(line) for pattern in patterns)


def playback_timeout_seconds(audio_file: Path, *, minimum_seconds: int = 120, padding_seconds: int = 90) -> int:
    try:
        with wave.open(str(audio_file), "rb") as wav_file:
            duration_seconds = wav_file.getnframes() / float(wav_file.getframerate())
    except Exception:
        return minimum_seconds
    return max(minimum_seconds, int(duration_seconds + padding_seconds))


def run_playback(
    audio_file: Path,
    device_key: str,
    execution_dir: Path,
    skip_probe: bool = False,
    log_prefix: str = "play",
) -> subprocess.CompletedProcess:
    device_key = str(device_key or "").strip()
    capture = run_audio_playback_adapter(
        audio_file,
        device_key,
        skip_probe=skip_probe,
        timeout_s=playback_timeout_seconds(audio_file),
    )
    completed = capture.completed
    started_at = capture.started_at
    finished_at = capture.finished_at
    (execution_dir / f"{log_prefix}_stdout.log").write_text(completed.stdout, encoding="utf-8")
    (execution_dir / f"{log_prefix}_stderr.log").write_text(completed.stderr, encoding="utf-8")
    (execution_dir / f"{log_prefix}_command.json").write_text(
        json.dumps(
            {
                "cmd": list(completed.args),
                "returncode": completed.returncode,
                "device_key": device_key,
                "playback_device": playback_device_label(device_key),
                "process_started_at": started_at.isoformat(timespec="milliseconds"),
                "playback_started_at": capture.playback_started_at.isoformat(timespec="milliseconds"),
                "finished_at": finished_at.isoformat(timespec="milliseconds"),
                "adapter_executor": capture.action_result.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    runtime_events = [
        {
            "timestamp": started_at.isoformat(timespec="milliseconds"),
            "event_type": "AudioInjected",
            "payload": {
                "audio_file": str(audio_file),
                "device_key": device_key,
                "log_prefix": log_prefix,
                "timestamp_source": "adapter_executor",
            },
        },
        {
            "timestamp": finished_at.isoformat(timespec="milliseconds"),
            "event_type": "AudioCompleted",
            "payload": {
                "audio_file": str(audio_file),
                "device_key": device_key,
                "log_prefix": log_prefix,
                "returncode": completed.returncode,
            },
        },
    ]
    (execution_dir / f"{log_prefix}_runtime_events.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in runtime_events) + "\n",
        encoding="utf-8",
    )
    return completed


def match_expectation(lines: List[str], match_type: str, pattern: str) -> Tuple[bool, str]:
    if match_type == "contains":
        lower_pattern = pattern.lower()
        for line in lines:
            if lower_pattern in line.lower():
                return True, line
        return False, ""
    if match_type == "regex":
        compiled = re.compile(pattern, re.I)
        for line in lines:
            if compiled.search(line):
                return True, line
        return False, ""
    raise ValueError(f"unsupported match_type: {match_type}")


def summarize_window(window_logs: Dict[str, List[str]]) -> dict:
    summary = {
        "line_counts": {port: len(lines) for port, lines in window_logs.items()},
        "tones": [],
        "wakeup_lines": [],
        "offline_asr_lines": [],
        "player_status_lines": [],
        "playback_start_markers": [],
        "playback_end_markers": [],
    }
    tone_values = []
    for port, lines in window_logs.items():
        for line in lines:
            lower = line.lower()
            if "wake(" in lower or "wakeup_callback" in lower or "offline_wakeup" in lower or "offline wakeup" in lower or "line_wakeup" in lower:
                summary["wakeup_lines"].append(line)
            if "offline_asr_callbak" in lower:
                summary["offline_asr_lines"].append(line)
            if "playing" in lower or "playback_complete" in lower or "soundplayer status:" in lower:
                summary["player_status_lines"].append(line)
            if matches_any_regex(line, PLAYER_START_RES):
                summary["playback_start_markers"].append(line)
            if matches_any_regex(line, PLAYER_END_RES):
                summary["playback_end_markers"].append(line)
            match = TONE_RE.search(line)
            if match:
                tone_values.append({"port": port, "tone_id": int(match.group("tone")), "line": line})
    summary["tones"] = tone_values
    return summary


def diagnose(
    case_spec: dict,
    play_result: subprocess.CompletedProcess,
    window_logs: Dict[str, List[str]],
    matched_map: Dict[str, dict],
) -> dict:
    required_failures = [item for item in matched_map.values() if item["required"] and not item["matched"]]
    total_lines = sum(len(lines) for lines in window_logs.values())
    if play_result.returncode != 0:
        return {
            "result": "BLOCKED",
            "failure_type": "BLOCKED_AUDIO_ROUTE",
            "suspected_root_cause": "audio_route_or_playback_tool",
            "reason": "Playback command failed before log validation.",
        }
    if total_lines == 0:
        return {
            "result": "BLOCKED",
            "failure_type": "BLOCKED_DEVICE",
            "suspected_root_cause": "device_or_log_capture",
            "reason": "No COM12/COM13/COM14 logs were captured in the observe window.",
        }
    if not required_failures:
        return {
            "result": "PASS",
            "failure_type": "",
            "suspected_root_cause": "",
            "reason": "All required expectations matched.",
        }

    com12_has_wake = any("wake(" in line.lower() for line in window_logs.get("COM12", []))
    com13_has_asr = any("offline_asr_callbak" in line.lower() for line in window_logs.get("COM13", []))
    com14_has_asr = any("offline_asr_callbak" in line.lower() for line in window_logs.get("COM14", []))
    com13_has_playback = any(matches_any_regex(line, PLAYER_END_RES) for line in window_logs.get("COM13", []))
    com14_has_tone = any("play next tone" in line.lower() for line in window_logs.get("COM14", []))
    is_open_ac_case = "OPEN_AC" in case_spec["case_id"] or "打开空调" in case_spec["name"]

    if com12_has_wake and is_open_ac_case and not (com13_has_asr and com14_has_asr):
        return {
            "result": "FAIL",
            "failure_type": "STABLE_FAIL",
            "suspected_root_cause": "device_business_or_middle_layer",
            "reason": "Wake happened in CP logs, but the command chain did not fully reach AP/WB01.",
        }

    if com12_has_wake and not (com13_has_asr or com14_has_asr):
        return {
            "result": "FAIL",
            "failure_type": "STABLE_FAIL",
            "suspected_root_cause": "device_or_case_expectation",
            "reason": "Wake happened in CP logs, but higher-layer ASR evidence is missing.",
        }

    if com14_has_tone and not com13_has_playback:
        return {
            "result": "FAIL",
            "failure_type": "STABLE_FAIL",
            "suspected_root_cause": "device_business_or_player_status",
            "reason": "AP tone evidence exists, but WB01 playback completion marker is missing.",
        }

    return {
        "result": "FAIL",
        "failure_type": "CHECK_LOGIC_ISSUE",
        "suspected_root_cause": "judge_logic_or_case_definition",
        "reason": "Logs exist, but current expectations did not match them cleanly.",
    }


def build_fingerprint(
    case_spec: dict,
    play_result: subprocess.CompletedProcess,
    window_summary: dict,
    matched_map: Dict[str, dict],
    diagnosis: dict,
) -> dict:
    return {
        "judge_version": JUDGE_VERSION,
        "case_id": case_spec["case_id"],
        "result": diagnosis["result"],
        "failure_type": diagnosis["failure_type"],
        "playback_returncode": play_result.returncode,
        "line_counts": window_summary["line_counts"],
        "tone_ids": [item["tone_id"] for item in window_summary["tones"]],
        "wake_count": len(window_summary["wakeup_lines"]),
        "asr_count": len(window_summary["offline_asr_lines"]),
        "playback_start_count": len(window_summary["playback_start_markers"]),
        "playback_end_count": len(window_summary["playback_end_markers"]),
        "matched_expectations": [key for key, item in matched_map.items() if item["matched"]],
        "missing_expectations": [key for key, item in matched_map.items() if item["required"] and not item["matched"]],
    }


def build_judge_payload(case_spec: dict, matched_map: Dict[str, dict], window_summary: dict, diagnosis: dict) -> dict:
    return {
        "judge_version": JUDGE_VERSION,
        "case_id": case_spec["case_id"],
        "case_name": case_spec["name"],
        "diagnosis": diagnosis,
        "matched_expectations": [key for key, item in matched_map.items() if item["matched"]],
        "missing_required_expectations": [
            {
                "id": key,
                "port": item["port"],
                "pattern": item["pattern"],
            }
            for key, item in matched_map.items()
            if item["required"] and not item["matched"]
        ],
        "tone_ids": [item["tone_id"] for item in window_summary["tones"]],
        "playback_start_markers": window_summary["playback_start_markers"],
        "playback_end_markers": window_summary["playback_end_markers"],
        "wake_lines": window_summary["wakeup_lines"],
        "offline_asr_lines": window_summary["offline_asr_lines"],
        "line_counts": window_summary["line_counts"],
    }


def build_excerpt(case_spec: dict, diagnosis: dict, matched_map: Dict[str, dict], window_summary: dict) -> str:
    lines = [
        f"# {case_spec['case_id']}",
        "",
        f"- Name: `{case_spec['name']}`",
        f"- Result: `{diagnosis['result']}`",
        f"- Failure type: `{diagnosis['failure_type'] or 'PASS'}`",
        f"- Suspected root cause: `{diagnosis['suspected_root_cause'] or 'none'}`",
        f"- Reason: {diagnosis['reason']}",
        "",
        "## Expectation evidence",
        "",
    ]
    for key, item in matched_map.items():
        evidence = item["evidence"] or "<no evidence>"
        lines.append(f"- `{key}` -> `{'PASS' if item['matched'] else 'MISS'}` | `{item['pattern']}` | `{evidence}`")

    lines += [
        "",
        "## Tone markers",
        "",
    ]
    if window_summary["tones"]:
        for item in window_summary["tones"]:
            lines.append(f"- `{item['port']}` tone `{item['tone_id']}` | `{item['line']}`")
    else:
        lines.append("- <none>")

    lines += [
        "",
        "## Playback start markers",
        "",
    ]
    if window_summary["playback_start_markers"]:
        for line in window_summary["playback_start_markers"][:10]:
            lines.append(f"- `{line}`")
    else:
        lines.append("- <none>")

    lines += [
        "",
        "## Playback end markers",
        "",
    ]
    if window_summary["playback_end_markers"]:
        for line in window_summary["playback_end_markers"][:10]:
            lines.append(f"- `{line}`")
    else:
        lines.append("- <none>")

    lines += [
        "",
        "## Wake / ASR markers",
        "",
    ]
    marker_lines = window_summary["wakeup_lines"][:10] + window_summary["offline_asr_lines"][:10]
    if marker_lines:
        for line in marker_lines:
            lines.append(f"- `{line}`")
    else:
        lines.append("- <none>")
    return "\n".join(lines) + "\n"


def run_case(case_path: Path, device_key: str = "") -> Path:
    session_dir = current_session_dir()
    lock_path = session_dir / ".case_runner.lock"
    lock_fd = None
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode("ascii", errors="ignore"))
    except FileExistsError as exc:
        raise RuntimeError(f"another case runner is already active: {lock_path}") from exc

    try:
        env = load_env()
        case_spec = load_case(case_path)
        device_key = str(device_key or default_playback_device_key(env)).strip()
        execution_dir = new_artifact_dir(f"case_run_{case_spec['case_id']}", session_dir)
        shutil.copy2(case_path, execution_dir / case_path.name)

        state_dir = execution_dir / "state"
        audio_dir = execution_dir / "audio"
        logs_dir = execution_dir / "window_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        before_state = snapshot("before", state_dir, session_dir)
        audio_file, audio_manifest = build_from_case(case_path, audio_dir)

        start_dt = datetime.now()
        play_result = run_playback(audio_file, device_key, execution_dir)
        observe_after_ms = int(case_spec.get("observe_after_ms", 10000))
        time.sleep(observe_after_ms / 1000.0)
        end_dt = datetime.now()

        after_state = snapshot("after", state_dir, session_dir)
        state_diff = diff_states(before_state, after_state, state_dir / "state_diff.json")

        raw_window_logs: Dict[str, List[str]] = {}
        for port in ["COM12", "COM13", "COM14"]:
            lines = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
            raw_window_logs[port] = lines
            (logs_dir / f"{port}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        clean_window_logs = sanitize_logs(raw_window_logs)
        for port, lines in clean_window_logs.items():
            (logs_dir / f"{port}.clean.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        matched_map: Dict[str, dict] = {}
        for expectation in case_spec["expected"]:
            lines = clean_window_logs[expectation["port"]]
            matched, evidence = match_expectation(lines, expectation["match_type"], expectation["pattern"])
            matched_map[expectation["id"]] = {
                "port": expectation["port"],
                "match_type": expectation["match_type"],
                "pattern": expectation["pattern"],
                "required": bool(expectation.get("required", True)),
                "matched": matched,
                "evidence": evidence,
            }

        window_summary = summarize_window(clean_window_logs)
        diagnosis = diagnose(case_spec, play_result, clean_window_logs, matched_map)
        fingerprint = build_fingerprint(case_spec, play_result, window_summary, matched_map, diagnosis)
        judge_payload = build_judge_payload(case_spec, matched_map, window_summary, diagnosis)
        excerpt_text = build_excerpt(case_spec, diagnosis, matched_map, window_summary)

        judge_path = execution_dir / "judge.json"
        fingerprint_path = execution_dir / "fingerprint.json"
        excerpt_path = execution_dir / "failure_excerpt.md"
        judge_path.write_text(json.dumps(judge_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        fingerprint_path.write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
        excerpt_path.write_text(excerpt_text, encoding="utf-8")

        result = {
            "case_id": case_spec["case_id"],
            "name": case_spec["name"],
            "mode": case_spec.get("mode", ""),
            "device_key": device_key,
            "playback_device": playback_device_label(device_key),
            "execution_dir": str(execution_dir),
            "session_dir": str(session_dir),
            "started_at": start_dt.isoformat(timespec="milliseconds"),
            "ended_at": end_dt.isoformat(timespec="milliseconds"),
            "observe_after_ms": observe_after_ms,
            "playback": {
                "audio_file": str(audio_file),
                "manifest": audio_manifest,
                "returncode": play_result.returncode,
            },
            "states": {
                "before": str(before_state),
                "after": str(after_state),
                "diff": str(state_diff),
            },
            "expectations": matched_map,
            "window_summary": window_summary,
            "diagnosis": diagnosis,
            "artifacts": {
                "judge": str(judge_path),
                "fingerprint": str(fingerprint_path),
                "failure_excerpt": str(excerpt_path),
            },
        }
        out_path = execution_dir / "execution_result.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        summary_lines = [
            f"# {case_spec['case_id']}",
            "",
            f"- Name: `{case_spec['name']}`",
            f"- Result: `{diagnosis['result']}`",
            f"- Classification: `{diagnosis['failure_type'] or 'PASS'}`",
            f"- Suspected root cause: `{diagnosis['suspected_root_cause'] or 'none'}`",
            f"- Reason: {diagnosis['reason']}",
            f"- Playback device: `{playback_device_label(device_key)}`",
            f"- Observe window: `{result['started_at']}` ~ `{result['ended_at']}`",
            f"- Judge artifact: `{judge_path}`",
            f"- Fingerprint artifact: `{fingerprint_path}`",
            f"- Excerpt artifact: `{excerpt_path}`",
            "",
            "## Expectation matches",
            "",
        ]
        for key, item in matched_map.items():
            summary_lines.append(
                f"- `{key}`: `{'PASS' if item['matched'] else 'MISS'}` -> `{item['pattern']}`"
                + (f" | evidence: `{item['evidence']}`" if item["evidence"] else "")
            )
        summary_lines += [
            "",
            "## Observe summary",
            "",
            f"- Line counts: `{window_summary['line_counts']}`",
            f"- Tone sequence: `{[item['tone_id'] for item in window_summary['tones']]}`",
            f"- Wake events: `{len(window_summary['wakeup_lines'])}`",
            f"- ASR events: `{len(window_summary['offline_asr_lines'])}`",
            f"- Playback start markers: `{len(window_summary['playback_start_markers'])}`",
            f"- Playback end markers: `{len(window_summary['playback_end_markers'])}`",
            f"- Player status events: `{len(window_summary['player_status_lines'])}`",
        ]
        (execution_dir / "execution_summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        return out_path
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if lock_path.exists():
            lock_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polaris single case runner")
    parser.add_argument("--case-file", required=True)
    parser.add_argument("--device-key", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result_path = run_case(Path(args.case_file), device_key=args.device_key)
    print(result_path)


if __name__ == "__main__":
    main()
