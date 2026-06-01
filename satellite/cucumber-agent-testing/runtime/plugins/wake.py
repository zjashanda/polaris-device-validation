#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wake-domain runtime plugin."""

from __future__ import annotations

from ..kernel import RuntimePlugin


class WakePlugin(RuntimePlugin):
    name = "wake"
    event_prefixes = ("Wake", "AudioInjected")
