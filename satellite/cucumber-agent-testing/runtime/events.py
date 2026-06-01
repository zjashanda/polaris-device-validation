#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Common event model used by the Polaris validation runtime.

The runtime keeps assertions away from raw grep results. Log parsers convert
serial/playback/cloud lines into these stable event records first.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
HOST_TS_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
)


@dataclass(frozen=True)
class ValidationEvent:
    event_id: str
    timestamp: str
    timestamp_ms: Optional[int]
    source: str
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    file: str = ""
    line_no: int = 0
    event_version: str = "v1"
    run_id: str = ""
    scene_id: str = ""
    device_id: str = ""
    plugin: str = ""
    timestamp_wall: str = ""
    timestamp_wall_ms: Optional[int] = None
    timestamp_monotonic_ms: Optional[int] = None
    severity: str = "info"
    tags: List[str] = field(default_factory=list)
    parent_event: str = ""
    caused_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def effective_ms(self) -> Optional[int]:
        """Return the deterministic runtime clock used by assertions."""
        if self.timestamp_monotonic_ms is not None:
            return self.timestamp_monotonic_ms
        return self.timestamp_ms


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def parse_host_timestamp(line: str) -> tuple[str, Optional[int]]:
    match = HOST_TS_RE.search(line)
    if not match:
        return "", None
    text = match.group("ts")
    try:
        dt = datetime.fromisoformat(text)
        return text, int(dt.timestamp() * 1000)
    except Exception:
        return text, None


def stable_event_id(path: Path, line_no: int, event_type: str, raw: str) -> str:
    material = f"{path.as_posix()}:{line_no}:{event_type}:{raw}".encode("utf-8", errors="replace")
    return "evt_" + hashlib.sha1(material).hexdigest()[:16]


PLUGIN_EVENT_PREFIXES = {
    "wake": ("Wake", "AudioInjected"),
    "asr": ("ASR", "Command", "Oneshot", "OnlineVAD", "DocCaseJudge", "Duplex"),
    "media": ("TTS", "Media", "AudioCompleted", "Interrupt"),
    "network": ("Network",),
    "reboot": ("Reboot", "Crash"),
}


def infer_event_plugin(event_type: str) -> str:
    for plugin, prefixes in PLUGIN_EVENT_PREFIXES.items():
        if event_type.startswith(prefixes):
            return plugin
    return "core"


def infer_event_severity(event_type: str) -> str:
    if event_type.startswith("Crash"):
        return "error"
    if event_type.startswith("Reboot"):
        return "warn"
    return "info"


def infer_event_tags(event_type: str, plugin: str) -> List[str]:
    tags = ["runtime", plugin]
    lowered = event_type.lower()
    for token in ("wake", "asr", "command", "media", "tts", "network", "reboot", "crash", "interrupt"):
        if token in lowered and token not in tags:
            tags.append(token)
    return tags


def event_time_ms(event: ValidationEvent) -> Optional[int]:
    return event.effective_ms


def make_event(
    *,
    path: Path,
    line_no: int,
    raw: str,
    source: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    run_id: str = "",
    scene_id: str = "",
    device_id: str = "",
    plugin: str = "",
    timestamp_monotonic_ms: Optional[int] = None,
    severity: str = "",
    tags: Optional[List[str]] = None,
    parent_event: str = "",
    caused_by: str = "",
) -> ValidationEvent:
    timestamp, timestamp_ms = parse_host_timestamp(raw)
    clean = strip_ansi(raw.rstrip("\n"))
    resolved_plugin = plugin or infer_event_plugin(event_type)
    return ValidationEvent(
        event_id=stable_event_id(path, line_no, event_type, clean),
        timestamp=timestamp,
        timestamp_ms=timestamp_ms,
        source=source,
        event_type=event_type,
        payload=payload or {},
        raw=clean,
        file=str(path),
        line_no=line_no,
        event_version="v1",
        run_id=run_id,
        scene_id=scene_id,
        device_id=device_id,
        plugin=resolved_plugin,
        timestamp_wall=timestamp,
        timestamp_wall_ms=timestamp_ms,
        timestamp_monotonic_ms=timestamp_monotonic_ms,
        severity=severity or infer_event_severity(event_type),
        tags=tags if tags is not None else infer_event_tags(event_type, resolved_plugin),
        parent_event=parent_event,
        caused_by=caused_by,
    )
