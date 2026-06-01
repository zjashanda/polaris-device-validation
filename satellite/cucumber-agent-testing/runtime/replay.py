#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay package builder for event-runtime validation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .assertion_engine import (
    collect_recognition_observations,
    evaluate_attribution_validator,
    evaluate_basic_command,
    evaluate_command_interrupt,
    evaluate_command_batch,
    evaluate_duplex_recognition,
    evaluate_false_wake,
    evaluate_first_wake,
    evaluate_interrupt_prerequisite,
    evaluate_network_recovery,
    evaluate_online_vad_special,
    evaluate_oneshot_matrix,
    evaluate_recognition_mode_wake,
    evaluate_wake_matrix,
    evaluate_wake_interrupt,
)
from .parsers import parse_artifact_tree
from .kernel import PluginContext, PluginManager
from .plugins import default_plugins
from .state_machine import RuntimeStateMachine
from .timeline import Timeline


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def evaluate_profile(profile: str, timeline: Timeline, capabilities: Dict[str, Any]) -> Dict[str, Any]:
    cp_log = bool(capabilities.get("cp_log", True))
    asr_log = bool(capabilities.get("asr_log", True))
    if profile == "first_wake":
        return evaluate_first_wake(timeline, cp_log=cp_log, asr_log=asr_log)
    if profile == "recognition_mode_wake":
        return evaluate_recognition_mode_wake(
            timeline,
            cp_log=cp_log,
            asr_log=asr_log,
            recognition_timeout_s=int(capabilities.get("recognition_timeout_s", 15) or 15),
            timing_guard_ms=int(capabilities.get("timing_guard_ms", 1200) or 1200),
            wake_cluster_gap_ms=int(capabilities.get("wake_cluster_gap_ms", 2500) or 2500),
        )
    if profile == "basic_command":
        return evaluate_basic_command(timeline, cp_log=cp_log, asr_log=asr_log)
    if profile == "half_duplex_recognition":
        return evaluate_duplex_recognition(timeline, mode="half", cp_log=cp_log, asr_log=asr_log)
    if profile == "full_duplex_recognition":
        return evaluate_duplex_recognition(timeline, mode="full", cp_log=cp_log, asr_log=asr_log)
    if profile == "command_batch":
        return evaluate_command_batch(timeline, cp_log=cp_log, asr_log=asr_log)
    if profile == "command_batch_exploratory":
        return evaluate_command_batch(timeline, cp_log=cp_log, asr_log=asr_log, exploratory=True)
    if profile == "offline_oneshot_matrix":
        return evaluate_oneshot_matrix(timeline, online=False, cp_log=cp_log, asr_log=asr_log)
    if profile == "online_oneshot_matrix":
        return evaluate_oneshot_matrix(timeline, online=True, cp_log=cp_log, asr_log=asr_log)
    if profile == "wake_matrix":
        return evaluate_wake_matrix(timeline, cp_log=cp_log, asr_log=asr_log)
    if profile == "online_vad_special":
        return evaluate_online_vad_special(timeline, cp_log=cp_log, asr_log=asr_log)
    if profile == "false_wake_quiet":
        return evaluate_false_wake(timeline, playback_required=False)
    if profile == "false_wake_playback":
        return evaluate_false_wake(timeline, playback_required=True)
    if profile == "attribution_validator":
        return evaluate_attribution_validator(timeline)
    if profile == "interrupt_prerequisite_measurement":
        return evaluate_interrupt_prerequisite(timeline)
    if profile == "wake_interrupt":
        return evaluate_wake_interrupt(
            timeline,
            cp_log=cp_log,
            asr_log=asr_log,
            guard_ms=int(capabilities.get("interrupt_guard_ms", 600) or 600),
            post_injection_ms=int(capabilities.get("post_injection_ms", 5000) or 5000),
        )
    if profile == "command_interrupt":
        return evaluate_command_interrupt(
            timeline,
            cp_log=cp_log,
            asr_log=asr_log,
            guard_ms=int(capabilities.get("interrupt_guard_ms", 600) or 600),
            post_injection_ms=int(capabilities.get("post_injection_ms", 5000) or 5000),
        )
    if profile == "network_recovery_basic":
        return evaluate_network_recovery(
            timeline,
            post_recovery_ms=int(capabilities.get("post_recovery_ms", 60000) or 60000),
        )
    raise ValueError(f"unsupported runtime profile: {profile}")


def render_report(package: Dict[str, Any]) -> str:
    assertion = package["assertion_summary"]
    lines = [
        "# Event Runtime Replay Report",
        "",
        f"- input_dir: `{package['metadata']['input_dir']}`",
        f"- profile: `{package['metadata']['profile']}`",
        f"- project: `{package['metadata'].get('project', '')}`",
        f"- event_count: `{package['timeline']['event_count']}`",
        f"- result: `{assertion.get('result')}`",
        "",
        "## Event Counts",
        "",
    ]
    for name, count in sorted(package["timeline"].get("event_counts", {}).items()):
        lines.append(f"- {name}: `{count}`")
    lines.extend(["", "## Assertions", ""])
    for item in assertion.get("assertions", []):
        lines.append(f"- `{item['result']}` {item['name']}: {item['reason']}")
    recognition = assertion.get("recognition_observations", {}) if isinstance(assertion.get("recognition_observations"), dict) else {}
    if recognition:
        lines.extend(["", "## Recognition Observations", ""])
        lines.append(f"- wake events: `{recognition.get('wake_event_count', 0)}`")
        lines.append(f"- ASR events: `{recognition.get('asr_event_count', 0)}`")
        lines.append(f"- command events: `{recognition.get('command_event_count', 0)}`")
        texts = recognition.get("recognized_texts", []) or []
        commands = recognition.get("recognized_commands", []) or []
        wakes = recognition.get("wake_keywords", []) or []
        if texts:
            lines.append(f"- recognized texts: `{', '.join(str(item) for item in texts[:20])}`")
        if commands:
            lines.append(f"- recognized commands: `{', '.join(str(item) for item in commands[:20])}`")
        if wakes:
            lines.append(f"- wake keywords: `{', '.join(str(item) for item in wakes[:20])}`")
    lines.extend(["", "## First Events", ""])
    for item in package["timeline"].get("events", [])[:30]:
        rel = item.get("relative_ms")
        rel_text = "" if rel is None else f"+{rel}ms "
        lines.append(f"- {rel_text}`{item['event_type']}` from `{item['source']}`: {item.get('raw', '')[:180]}")
    lines.append("")
    return "\n".join(lines)


def build_replay_package(
    *,
    input_dir: Path,
    out_dir: Path,
    profile: str,
    project: str = "",
    capabilities: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    capabilities = capabilities or {}
    raw_events = parse_artifact_tree(input_dir)
    plugin_context = PluginContext(profile=profile, project=project, capabilities=capabilities)
    plugin_manager = PluginManager(default_plugins())
    events = plugin_manager.run(raw_events, plugin_context)
    timeline = Timeline.from_events(events)
    state_machine = RuntimeStateMachine().run(timeline.events)
    assertion_summary = evaluate_profile(profile, timeline, capabilities)
    assertion_summary.setdefault("recognition_observations", collect_recognition_observations(timeline))
    package = {
        "metadata": {
            "schema": "polaris.runtime_replay.v1",
            "event_schema": "polaris.validation_event.v1",
            "clock": "monotonic_ms",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "input_dir": str(input_dir),
            "profile": profile,
            "project": project,
            "capabilities": capabilities,
            "plugins": [plugin.name for plugin in plugin_manager.plugins],
            "plugin_notes": plugin_context.notes,
        },
        "timeline": timeline.to_dict(),
        "runtime_state": state_machine.to_dict(),
        "assertion_summary": assertion_summary,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "events.json", [event.to_dict() for event in timeline.events])
    write_json(out_dir / "timeline.json", package["timeline"])
    write_json(out_dir / "runtime_state.json", package["runtime_state"])
    write_json(out_dir / "assertions.json", assertion_summary)
    write_json(out_dir / "replay_package.json", package)
    (out_dir / "runtime_replay_report.md").write_text(render_report(package), encoding="utf-8")
    return package
