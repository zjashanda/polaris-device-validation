#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Network-domain runtime plugin."""

from __future__ import annotations

from ..kernel import RuntimePlugin


class NetworkPlugin(RuntimePlugin):
    name = "network"
    event_prefixes = ("Network",)
