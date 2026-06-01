#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simulation-lite helpers for deterministic replay tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List


def write_simulated_log(path: Path, events: Iterable[str], *, start: str = "2026-01-01T00:00:00.000", step_ms: int = 100) -> Path:
    base = datetime.fromisoformat(start)
    lines: List[str] = []
    for index, event in enumerate(events):
        ts = (base + timedelta(milliseconds=index * step_ms)).isoformat(timespec="milliseconds")
        if event == "AudioInjected":
            lines.append(f"{ts} [PC/audio] play iteration simulated")
        elif event == "WakeDetected":
            lines.append(f"{ts} [COM14/ap] Pre Wakeup detected")
        elif event == "CPWakeDetected":
            lines.append(f"{ts} [COM13/cp] WAKE(1) KEY=1(xiao mei xiao mei)")
        elif event == "ASRWakeDetected":
            lines.append(f"{ts} [COM12/asr] wakeup_callback, keyword: 小美小美")
        elif event == "ASRDetected":
            lines.append(f"{ts} [COM13/asr] online_asr_callback, text: 打开空调")
        elif event == "CommandDetected":
            lines.append(f"{ts} [COM13/asr] offline_asr_callback, text: 打开空调")
        elif event == "TTSStarted":
            lines.append(f"{ts} [COM14/ap] stream_tts simulated")
        elif event == "MediaStarted":
            lines.append(f"{ts} [COM14/ap] soundplayer status: 2")
        elif event == "MediaCompleted":
            lines.append(f"{ts} [COM14/ap] soundplayer status: 6")
        elif event == "NetworkLost":
            lines.append(f"{ts} [COM14/ap] wifi disconnected")
        elif event == "NetworkRecovered":
            lines.append(f"{ts} [COM14/ap] online=true")
        elif event == "RebootDetected":
            lines.append(f"{ts} [COM14/ap] Boot Reason: simulated")
        elif event == "CrashDetected":
            lines.append(f"{ts} [COM14/ap] hardfault simulated")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
