#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ASR and command recognition runtime plugin."""

from __future__ import annotations

from ..kernel import RuntimePlugin


class ASRPlugin(RuntimePlugin):
    name = "asr"
    event_prefixes = ("ASR", "Command", "Oneshot", "OnlineVAD", "DocCaseJudge", "Duplex")
