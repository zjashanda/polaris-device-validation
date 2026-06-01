#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay VM-lite for Polaris replay packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class ReplaySnapshot:
    cursor: int
    event_id: str
    runtime_state: Dict[str, Any] = field(default_factory=dict)
    resource_state: Dict[str, Any] = field(default_factory=dict)
    plugin_state: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ReplayVM:
    def __init__(self, package: Dict[str, Any]) -> None:
        self.package = package
        self.events: List[Dict[str, Any]] = list((package.get("timeline", {}) or {}).get("events", []) or [])
        self.cursor = -1
        self.snapshots: List[ReplaySnapshot] = []

    def step(self) -> Dict[str, Any]:
        if self.cursor + 1 >= len(self.events):
            return {}
        self.cursor += 1
        return self.events[self.cursor]

    def run_to_event(self, event_id: str) -> Dict[str, Any]:
        while self.cursor + 1 < len(self.events):
            event = self.step()
            if event.get("event_id") == event_id:
                return event
        return {}

    def snapshot(self) -> ReplaySnapshot:
        event = self.events[self.cursor] if 0 <= self.cursor < len(self.events) else {}
        snap = ReplaySnapshot(
            cursor=self.cursor,
            event_id=str(event.get("event_id", "")),
            runtime_state=self.package.get("runtime_state", {}) if isinstance(self.package.get("runtime_state"), dict) else {},
            resource_state=self.package.get("resource_state", {}) if isinstance(self.package.get("resource_state"), dict) else {},
            plugin_state={"plugins": (self.package.get("metadata", {}) or {}).get("plugins", [])},
        )
        self.snapshots.append(snap)
        return snap

    def rollback(self, snapshot: ReplaySnapshot) -> None:
        self.cursor = snapshot.cursor

    def time_travel(self, event_id: str, offset: int = 0) -> ReplaySnapshot:
        index = next((idx for idx, item in enumerate(self.events) if item.get("event_id") == event_id), -1)
        if index < 0:
            raise ValueError(f"event not found: {event_id}")
        self.cursor = max(-1, min(len(self.events) - 1, index + offset))
        return self.snapshot()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "polaris.replay_vm_lite.v1",
            "cursor": self.cursor,
            "event_count": len(self.events),
            "current_event": self.events[self.cursor] if 0 <= self.cursor < len(self.events) else {},
            "snapshots": [item.to_dict() for item in self.snapshots],
        }
