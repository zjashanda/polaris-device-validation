#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Heuristic event graph builder over a replay Timeline.

Project-specific cloud/media markers can be added through a small rule overlay
without changing the core graph heuristics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .assertion_engine import build_audio_windows, cluster_wake_events
from .events import ValidationEvent
from .timeline import Timeline, normalize_source


@dataclass
class EventGraphNode:
    event_id: str
    event_type: str
    source: str
    timestamp_ms: Optional[int]
    plugin: str = ""
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EventGraphEdge:
    src: str
    dst: str
    relation: str
    confidence: str = "medium"
    delta_ms: Optional[int] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EventGraph:
    nodes: List[EventGraphNode]
    edges: List[EventGraphEdge]
    warnings: List[str] = field(default_factory=list)
    risk_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "polaris.event_graph.v1",
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "warnings": self.warnings,
            "risk_summary": self.risk_summary,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }


def _summary(event: ValidationEvent) -> str:
    payload = event.payload or {}
    for key in ("recognized_text", "recognized_command", "wake_keyword", "marker"):
        if payload.get(key):
            return str(payload.get(key))[:120]
    return (event.raw or "")[:120]


def _delta(src: ValidationEvent, dst: ValidationEvent) -> Optional[int]:
    if src.timestamp_ms is None or dst.timestamp_ms is None:
        return None
    return int(dst.timestamp_ms - src.timestamp_ms)


def _latest_before(events: List[ValidationEvent], target: ValidationEvent, within_ms: int) -> Optional[ValidationEvent]:
    if target.timestamp_ms is None:
        return None
    candidates = [
        event
        for event in events
        if event.timestamp_ms is not None
        and event.timestamp_ms <= target.timestamp_ms
        and int(target.timestamp_ms - event.timestamp_ms) <= within_ms
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda event: int(event.timestamp_ms or 0))[-1]


def _append_edge(edges: List[EventGraphEdge], edge: EventGraphEdge, seen: set[tuple[str, str, str]]) -> None:
    key = (edge.src, edge.dst, edge.relation)
    if key in seen:
        return
    seen.add(key)
    edges.append(edge)


def _as_text_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if str(value or "").strip():
        return [str(value).strip()]
    return []


def _payload_text(event: ValidationEvent) -> str:
    payload = event.payload or {}
    parts = [event.raw or ""]
    for key, value in payload.items():
        parts.append(str(key))
        parts.append(str(value))
    return "\n".join(parts).lower()


def _event_matches(event: ValidationEvent, spec: Dict[str, Any]) -> bool:
    event_types = _as_text_list(spec.get("event_types") or spec.get("event_type"))
    if event_types and event.event_type not in event_types:
        return False
    sources = {normalize_source(item) for item in _as_text_list(spec.get("sources") or spec.get("source"))}
    if sources and normalize_source(event.source) not in sources:
        return False
    plugins = set(_as_text_list(spec.get("plugins") or spec.get("plugin")))
    if plugins and event.plugin not in plugins:
        return False
    payload_equals = spec.get("payload_equals", {}) if isinstance(spec.get("payload_equals"), dict) else {}
    for key, expected in payload_equals.items():
        if str((event.payload or {}).get(key, "")) != str(expected):
            return False
    text = _payload_text(event)
    contains_all = [item.lower() for item in _as_text_list(spec.get("contains_all"))]
    if contains_all and not all(item in text for item in contains_all):
        return False
    contains_any = [item.lower() for item in _as_text_list(spec.get("contains_any"))]
    if contains_any and not any(item in text for item in contains_any):
        return False
    not_contains = [item.lower() for item in _as_text_list(spec.get("not_contains"))]
    if not_contains and any(item in text for item in not_contains):
        return False
    return True


def _apply_rule_overlay(
    timeline: Timeline,
    edges: List[EventGraphEdge],
    seen: set[tuple[str, str, str]],
    warnings: List[str],
    rule_overlay: Optional[Dict[str, Any]],
    project_id: str = "",
) -> Dict[str, Any]:
    if not rule_overlay:
        return {"enabled": False, "rule_edge_count": 0, "matched_rules": []}
    rule_edge_count = 0
    matched_rules: List[Dict[str, Any]] = []
    for rule in rule_overlay.get("rules", []) if isinstance(rule_overlay.get("rules"), list) else []:
        if not isinstance(rule, dict) or rule.get("enabled") is False:
            continue
        relation = str(rule.get("relation", "") or "").strip()
        if not relation:
            continue
        projects = set(_as_text_list(rule.get("projects")))
        if projects and project_id and project_id not in projects:
            continue
        if projects and not project_id:
            matched_rules.append({"id": str(rule.get("id", relation) or relation), "relation": relation, "matches": 0, "skipped": "project_id_missing"})
            continue
        src_spec = rule.get("src", {}) if isinstance(rule.get("src"), dict) else {}
        dst_spec = rule.get("dst", {}) if isinstance(rule.get("dst"), dict) else {}
        within_ms = int(rule.get("within_ms", 15000) or 15000)
        confidence = str(rule.get("confidence", "medium") or "medium")
        reason = str(rule.get("reason", rule.get("id", "rule_overlay")) or "rule_overlay")
        rule_id = str(rule.get("id", relation) or relation)
        matches = 0
        for dst in timeline.events:
            if not _event_matches(dst, dst_spec) or dst.timestamp_ms is None:
                continue
            candidates = [
                src
                for src in timeline.events
                if src.event_id != dst.event_id
                and src.timestamp_ms is not None
                and src.timestamp_ms <= dst.timestamp_ms
                and int(dst.timestamp_ms - src.timestamp_ms) <= within_ms
                and _event_matches(src, src_spec)
            ]
            if candidates:
                src = sorted(candidates, key=lambda event: int(event.timestamp_ms or 0))[-1]
                before = len(edges)
                _append_edge(edges, EventGraphEdge(src.event_id, dst.event_id, relation, confidence, _delta(src, dst), reason), seen)
                if len(edges) > before:
                    rule_edge_count += 1
                    matches += 1
            elif rule.get("warn_if_unmatched"):
                warnings.append(f"Event graph rule `{rule_id}` matched dst event `{dst.event_id}` but no src was found within {within_ms}ms.")
        matched_rules.append({"id": rule_id, "relation": relation, "matches": matches})
    return {
        "enabled": True,
        "rule_set": rule_overlay.get("name", ""),
        "rule_edge_count": rule_edge_count,
        "matched_rules": matched_rules,
    }


def build_event_graph(timeline: Timeline, rule_overlay: Optional[Dict[str, Any]] = None, project_id: str = "") -> EventGraph:
    nodes = [
        EventGraphNode(
            event_id=event.event_id,
            event_type=event.event_type,
            source=normalize_source(event.source),
            timestamp_ms=event.timestamp_ms,
            plugin=event.plugin,
            summary=_summary(event),
        )
        for event in timeline.events
    ]
    edges: List[EventGraphEdge] = []
    seen: set[tuple[str, str, str]] = set()

    for previous, current in zip(timeline.events, timeline.events[1:]):
        _append_edge(
            edges,
            EventGraphEdge(previous.event_id, current.event_id, "timeline_next", confidence="low", delta_ms=_delta(previous, current)),
            seen,
        )

    wakes = timeline.find("WakeDetected")
    asrs = timeline.find("ASRDetected")
    commands = timeline.find("CommandDetected")
    tts_started = timeline.find("TTSStarted")
    media_started = timeline.find("MediaStarted")
    media_completed = timeline.find("MediaCompleted")
    tts_or_media = sorted(tts_started + media_started, key=lambda event: int(event.timestamp_ms or 0))
    responses_completed = sorted(media_completed, key=lambda event: int(event.timestamp_ms or 0))
    interrupt_injected = timeline.find("InterruptInjected")
    interrupt_completed = timeline.find("InterruptCompleted")
    network_lost = timeline.find("NetworkLost")
    network_recovered = timeline.find("NetworkRecovered")
    reboots = timeline.find("RebootDetected") + timeline.find("CrashDetected")

    audio_windows = build_audio_windows(timeline)
    wake_clusters = cluster_wake_events(timeline)
    for cluster in wake_clusters:
        if not cluster.events or cluster.start_ms is None:
            continue
        first = cluster.events[0]
        matched_window = None
        for window in audio_windows:
            if window.start_ms - 800 <= int(cluster.start_ms) <= window.end_ms + 800:
                matched_window = window
                break
        if matched_window:
            effective_start = matched_window.end_ms - int(matched_window.audio_duration_ms or 0) if matched_window.audio_duration_ms else matched_window.start_ms
            _append_edge(
                edges,
                EventGraphEdge(
                    matched_window.start_event_id,
                    first.event_id,
                    "audio_caused_wake",
                    confidence="high" if matched_window.audio_duration_ms else "medium",
                    delta_ms=int(cluster.start_ms - effective_start),
                    reason="wake cluster falls inside audio playback window",
                ),
                seen,
            )

    for asr in asrs:
        wake = _latest_before(wakes, asr, 5000)
        if wake:
            _append_edge(edges, EventGraphEdge(wake.event_id, asr.event_id, "wake_to_asr", "high", _delta(wake, asr)), seen)
    for command in commands:
        asr = _latest_before(asrs, command, 5000)
        if asr:
            _append_edge(edges, EventGraphEdge(asr.event_id, command.event_id, "asr_to_command", "high", _delta(asr, command)), seen)
    for media in tts_or_media:
        command = _latest_before(commands, media, 15000)
        asr = _latest_before(asrs, media, 15000)
        wake = _latest_before(wakes, media, 15000)
        relation_suffix = "tts_response" if media.event_type == "TTSStarted" else "media_response"
        if command:
            _append_edge(edges, EventGraphEdge(command.event_id, media.event_id, f"command_to_{relation_suffix}", "high", _delta(command, media)), seen)
        elif asr:
            _append_edge(edges, EventGraphEdge(asr.event_id, media.event_id, f"asr_to_{relation_suffix}", "medium", _delta(asr, media)), seen)
        elif wake:
            _append_edge(edges, EventGraphEdge(wake.event_id, media.event_id, "wake_to_response", "medium", _delta(wake, media)), seen)
    for completed in responses_completed:
        start = _latest_before(media_started + tts_started, completed, 300000)
        if start:
            relation = "tts_or_media_completed" if start.event_type == "TTSStarted" else "media_started_to_completed"
            _append_edge(edges, EventGraphEdge(start.event_id, completed.event_id, relation, "high", _delta(start, completed)), seen)
    for injected in interrupt_injected:
        media = _latest_before(media_started + tts_started, injected, 300000)
        if media:
            _append_edge(edges, EventGraphEdge(media.event_id, injected.event_id, "media_interrupted", "medium", _delta(media, injected)), seen)
        completed = _latest_before(interrupt_completed, injected, 0)
        # _latest_before only searches backwards; find the first completion after injection.
        if injected.timestamp_ms is not None:
            after = [
                event
                for event in interrupt_completed
                if event.timestamp_ms is not None and event.timestamp_ms >= injected.timestamp_ms and int(event.timestamp_ms - injected.timestamp_ms) <= 30000
            ]
            if after:
                completed = sorted(after, key=lambda event: int(event.timestamp_ms or 0))[0]
        if completed:
            _append_edge(edges, EventGraphEdge(injected.event_id, completed.event_id, "interrupt_injected_to_completed", "high", _delta(injected, completed)), seen)
        follow = None
        if injected.timestamp_ms is not None:
            candidates = [
                event
                for event in wakes + asrs + commands
                if event.timestamp_ms is not None and event.timestamp_ms >= injected.timestamp_ms and int(event.timestamp_ms - injected.timestamp_ms) <= 10000
            ]
            if candidates:
                follow = sorted(candidates, key=lambda event: int(event.timestamp_ms or 0))[0]
        if follow:
            _append_edge(edges, EventGraphEdge(injected.event_id, follow.event_id, "interrupt_to_recognition", "medium", _delta(injected, follow)), seen)
    for recovered in network_recovered:
        lost = _latest_before(network_lost, recovered, 120000)
        if lost:
            _append_edge(edges, EventGraphEdge(lost.event_id, recovered.event_id, "network_recovered_after_loss", "high", _delta(lost, recovered)), seen)
    for reboot in reboots:
        anchor = _latest_before(wakes + asrs + commands + tts_or_media + interrupt_injected + network_lost, reboot, 60000)
        if anchor:
            relation = "possible_crash_after_activity" if reboot.event_type == "CrashDetected" else "possible_reboot_after_activity"
            _append_edge(edges, EventGraphEdge(anchor.event_id, reboot.event_id, relation, "medium", _delta(anchor, reboot)), seen)

    warnings: List[str] = []
    overlay_summary = _apply_rule_overlay(timeline, edges, seen, warnings, rule_overlay, project_id=project_id)
    if wakes and not any(edge.relation == "audio_caused_wake" for edge in edges):
        warnings.append("WakeDetected exists but no audio_caused_wake edge was inferred.")
    orphan_media_completed = [
        event.event_id
        for event in media_completed
        if not any(edge.dst == event.event_id and edge.relation in {"media_started_to_completed", "tts_or_media_completed"} for edge in edges)
    ]
    if orphan_media_completed:
        warnings.append(f"MediaCompleted exists without inferred MediaStarted/TTSStarted parent: {len(orphan_media_completed)} event(s).")
    responses_without_upstream = [
        event.event_id
        for event in tts_or_media
        if not any(edge.dst == event.event_id and edge.relation in {"command_to_tts_response", "command_to_media_response", "asr_to_tts_response", "asr_to_media_response", "wake_to_response"} for edge in edges)
    ]
    if responses_without_upstream:
        warnings.append(f"TTS/Media response exists without wake/asr/command parent: {len(responses_without_upstream)} event(s).")
    if reboots:
        warnings.append(f"Reboot/Crash events observed: {len(reboots)}.")

    relation_counts: Dict[str, int] = {}
    for edge in edges:
        relation_counts[edge.relation] = relation_counts.get(edge.relation, 0) + 1
    risk_summary = {
        "crash_events": len(timeline.find("CrashDetected")),
        "reboot_events": len(timeline.find("RebootDetected")),
        "network_loss_events": len(network_lost),
        "interrupt_injected_events": len(interrupt_injected),
        "media_started_events": len(media_started),
        "media_completed_events": len(media_completed),
        "orphan_media_completed": len(orphan_media_completed),
        "responses_without_upstream": len(responses_without_upstream),
        "rule_overlay": overlay_summary,
        "relation_counts": dict(sorted(relation_counts.items())),
    }
    return EventGraph(nodes=nodes, edges=edges, warnings=warnings, risk_summary=risk_summary)


def render_event_graph_markdown(graph: EventGraph) -> str:
    lines = [
        "# Polaris Event Graph",
        "",
        f"- nodes: `{len(graph.nodes)}`",
        f"- edges: `{len(graph.edges)}`",
        "",
        "## Causal Edges",
        "",
    ]
    causal = [edge for edge in graph.edges if edge.relation != "timeline_next"]
    if not causal:
        lines.append("- <none>")
    for edge in causal:
        delta = "" if edge.delta_ms is None else f", delta={edge.delta_ms}ms"
        lines.append(f"- `{edge.relation}` `{edge.src}` -> `{edge.dst}` ({edge.confidence}{delta}) {edge.reason}")
    if graph.warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in graph.warnings:
            lines.append(f"- {warning}")
    if graph.risk_summary:
        lines.extend(["", "## Risk Summary", ""])
        for key, value in graph.risk_summary.items():
            if key == "relation_counts":
                continue
            lines.append(f"- `{key}`: `{value}`")
        relation_counts = graph.risk_summary.get("relation_counts", {})
        if isinstance(relation_counts, dict) and relation_counts:
            lines.extend(["", "## Relation Counts", ""])
            for key, value in sorted(relation_counts.items()):
                lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)
