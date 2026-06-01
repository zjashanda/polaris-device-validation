#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Media, TTS and interrupt runtime plugin."""

from __future__ import annotations

from ..kernel import RuntimePlugin


class MediaPlugin(RuntimePlugin):
    name = "media"
    event_prefixes = ("TTS", "Media", "AudioCompleted", "Interrupt")
