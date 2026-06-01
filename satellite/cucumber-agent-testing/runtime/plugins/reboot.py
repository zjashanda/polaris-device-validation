#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reboot and crash runtime plugin."""

from __future__ import annotations

from dataclasses import replace

from ..events import ValidationEvent
from ..kernel import PluginContext, RuntimePlugin, tag_event


class RebootPlugin(RuntimePlugin):
    name = "reboot"
    event_prefixes = ("Reboot", "Crash")

    def on_event(self, event: ValidationEvent, context: PluginContext) -> ValidationEvent:
        tagged = tag_event(event, self.name)
        if event.event_type.startswith("Crash"):
            return replace(tagged, severity="error")
        if event.event_type.startswith("Reboot"):
            return replace(tagged, severity="warn")
        return tagged
