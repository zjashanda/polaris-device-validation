#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Timeline utilities for event-based validation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .events import ValidationEvent, event_time_ms


SOURCE_GROUPS = {
    "ap": {"ap", "cskap"},
    "cp": {"cp", "cskcp"},
    "asr": {"asr", "upper", "ws63", "wb01"},
    "audio": {"audio", "playback"},
    "control": {"control"},
}


def normalize_source(source: str) -> str:
    text = (source or "").strip().lower()
    for group, aliases in SOURCE_GROUPS.items():
        if text == group or text in aliases:
            return group
    return text


@dataclass
class Timeline:
    events: List[ValidationEvent]

    @classmethod
    def from_events(cls, events: Iterable[ValidationEvent]) -> "Timeline":
        deduped: List[ValidationEvent] = []
        seen: set[tuple[str, str]] = set()
        for event in events:
            key = (event.event_type, event.raw)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        sorted_events = sorted(
            deduped,
            key=lambda event: (
                event.timestamp_monotonic_ms
                if event.timestamp_monotonic_ms is not None
                else event.timestamp_wall_ms
                if event.timestamp_wall_ms is not None
                else event.timestamp_ms
                if event.timestamp_ms is not None
                else 10**18,
                event.file,
                event.line_no,
                event.event_id,
            ),
        )
        return cls(_normalize_monotonic_clock(sorted_events))

    @property
    def start_ms(self) -> Optional[int]:
        for event in self.events:
            current = event_time_ms(event)
            if current is not None:
                return current
        return None

    @property
    def start_wall_ms(self) -> Optional[int]:
        for event in self.events:
            if event.timestamp_wall_ms is not None:
                return event.timestamp_wall_ms
        return None

    def find(
        self,
        event_type: Optional[str] = None,
        *,
        source: Optional[str] = None,
        sources: Optional[Sequence[str]] = None,
        after_ms: Optional[int] = None,
        before_ms: Optional[int] = None,
    ) -> List[ValidationEvent]:
        wanted_sources = {normalize_source(item) for item in sources or []}
        if source:
            wanted_sources.add(normalize_source(source))
        result: List[ValidationEvent] = []
        for event in self.events:
            current_ms = event_time_ms(event)
            if event_type and event.event_type != event_type:
                continue
            if wanted_sources and normalize_source(event.source) not in wanted_sources:
                continue
            if after_ms is not None and current_ms is not None and current_ms < after_ms:
                continue
            if before_ms is not None and current_ms is not None and current_ms > before_ms:
                continue
            result.append(event)
        return result

    def first(self, event_type: str, *, source: Optional[str] = None) -> Optional[ValidationEvent]:
        events = self.find(event_type, source=source)
        return events[0] if events else None

    def counts(self) -> Dict[str, int]:
        return dict(Counter(event.event_type for event in self.events))

    def counts_by_source(self, event_type: str) -> Dict[str, int]:
        return dict(Counter(normalize_source(event.source) for event in self.find(event_type)))

    def to_dict(self) -> Dict[str, Any]:
        start = self.start_ms
        serialized = []
        for event in self.events:
            item = event.to_dict()
            current_ms = event_time_ms(event)
            item["source_group"] = normalize_source(event.source)
            item["relative_ms"] = (
                current_ms - start
                if start is not None and current_ms is not None
                else None
            )
            serialized.append(item)
        return {
            "event_count": len(self.events),
            "clock": "monotonic_ms",
            "start_timestamp_ms": start,
            "start_monotonic_ms": start,
            "start_wall_ms": self.start_wall_ms,
            "event_counts": self.counts(),
            "events": serialized,
        }


def _normalize_monotonic_clock(events: List[ValidationEvent]) -> List[ValidationEvent]:
    """Convert parser timestamps to the runtime monotonic clock.

    Parsers often only know host wall time. The assertion engine should not
    reason on wall clock directly, so Timeline normalizes every event to a
    deterministic monotonic millisecond axis and keeps wall time in
    timestamp_wall/timestamp_wall_ms for reporting.
    """
    wall_values = [
        event.timestamp_wall_ms if event.timestamp_wall_ms is not None else event.timestamp_ms
        for event in events
        if (event.timestamp_wall_ms if event.timestamp_wall_ms is not None else event.timestamp_ms) is not None
    ]
    base_wall = min(wall_values) if wall_values else None
    normalized: List[ValidationEvent] = []
    last_ms = -1
    for index, event in enumerate(events):
        wall_ms = event.timestamp_wall_ms if event.timestamp_wall_ms is not None else event.timestamp_ms
        if event.timestamp_monotonic_ms is not None:
            mono_ms = int(event.timestamp_monotonic_ms)
        elif wall_ms is not None and base_wall is not None:
            mono_ms = int(wall_ms - base_wall)
        else:
            mono_ms = last_ms + 1 if last_ms >= 0 else index
        if wall_ms is None and mono_ms <= last_ms:
            mono_ms = last_ms + 1
        last_ms = max(last_ms, mono_ms)
        normalized.append(
            replace(
                event,
                timestamp_ms=mono_ms,
                timestamp_monotonic_ms=mono_ms,
                timestamp_wall=event.timestamp_wall or event.timestamp,
                timestamp_wall_ms=wall_ms,
            )
        )
    return normalized
