#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small plugin kernel for the Polaris validation runtime."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Sequence

from ..events import ValidationEvent, infer_event_plugin, infer_event_tags


@dataclass
class PluginContext:
    profile: str = ""
    project: str = ""
    capabilities: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


class RuntimePlugin:
    """Base class for deterministic runtime plugins."""

    name = "core"
    event_prefixes: Sequence[str] = ()

    def on_init(self, context: PluginContext) -> None:
        return None

    def handles(self, event: ValidationEvent) -> bool:
        if event.plugin == self.name:
            return True
        return bool(self.event_prefixes and event.event_type.startswith(tuple(self.event_prefixes)))

    def on_event(self, event: ValidationEvent, context: PluginContext) -> ValidationEvent:
        return tag_event(event, self.name)

    def on_shutdown(self, context: PluginContext) -> None:
        return None


def tag_event(event: ValidationEvent, plugin: str) -> ValidationEvent:
    resolved = plugin or event.plugin or infer_event_plugin(event.event_type)
    tags = list(event.tags or [])
    for tag in infer_event_tags(event.event_type, resolved):
        if tag not in tags:
            tags.append(tag)
    return replace(event, plugin=event.plugin or resolved, tags=tags)


class PluginManager:
    def __init__(self, plugins: Iterable[RuntimePlugin] | None = None) -> None:
        self.plugins = list(plugins or [])

    def run(self, events: Iterable[ValidationEvent], context: PluginContext) -> List[ValidationEvent]:
        for plugin in self.plugins:
            plugin.on_init(context)
        processed: List[ValidationEvent] = []
        for event in events:
            current = event
            for plugin in self.plugins:
                if plugin.handles(current):
                    current = plugin.on_event(current, context)
            processed.append(current)
        for plugin in reversed(self.plugins):
            plugin.on_shutdown(context)
        return processed
