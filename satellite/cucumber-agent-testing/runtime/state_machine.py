#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small explicit state machine for runtime replay.

This is intentionally conservative. It is used to make the current device state
visible in replay packages; formal assertions still live in assertion_engine.py.
It also emits transition coverage and guard violations so stability issues such
as crash/reboot during recognition can be separated from normal assertion
failures.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .events import ValidationEvent


TRANSITIONS = {
    "AudioInjected": "WAKE_PENDING",
    "WakeDetected": "LISTENING",
    "ASRDetected": "ASR_PROCESSING",
    "CommandDetected": "ASR_PROCESSING",
    "TTSStarted": "TTS_PLAYING",
    "MediaStarted": "MEDIA_PLAYING",
    "InterruptInjected": "MEDIA_PLAYING",
    "InterruptCompleted": "MEDIA_PLAYING",
    "MediaCompleted": "LISTENING",
    "NetworkLost": "NETWORK_LOST",
    "NetworkRecovered": "IDLE",
    "RebootDetected": "REBOOTING",
    "CrashDetected": "CRASHED",
}


@dataclass
class StateSnapshot:
    state: str
    event_id: str
    event_type: str
    timestamp: str
    timestamp_ms: int | None
    timestamp_wall: str = ""
    timestamp_wall_ms: int | None = None
    timestamp_monotonic_ms: int | None = None
    plugin: str = ""


@dataclass
class StateTransition:
    event_id: str
    event_type: str
    from_state: str
    to_state: str
    from_parallel: Dict[str, str]
    to_parallel: Dict[str, str]
    timestamp_ms: int | None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StateViolation:
    name: str
    severity: str
    event_id: str
    event_type: str
    reason: str
    state: str
    parallel_states: Dict[str, str]
    timestamp_ms: int | None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeStateMachine:
    state: str = "IDLE"
    audio_state: str = "IDLE"
    recognition_state: str = "IDLE"
    media_state: str = "IDLE"
    network_state: str = "UNKNOWN"
    power_state: str = "ON"
    cloud_state: str = "UNKNOWN"
    history: List[StateSnapshot] = field(default_factory=list)
    transitions: List[StateTransition] = field(default_factory=list)
    violations: List[StateViolation] = field(default_factory=list)
    event_counts: Dict[str, int] = field(default_factory=dict)
    visited_states: set[str] = field(default_factory=set)
    visited_parallel_states: Dict[str, set[str]] = field(
        default_factory=lambda: {
            "audio": {"IDLE"},
            "recognition": {"IDLE"},
            "media": {"IDLE"},
            "network": {"UNKNOWN"},
            "power": {"ON"},
            "cloud": {"UNKNOWN"},
        }
    )
    seen_audio_injected: bool = False
    seen_wake: bool = False
    seen_media_started: bool = False
    warned_activity_after_reboot: bool = False

    def __post_init__(self) -> None:
        self.visited_states.add(self.state)

    def apply(self, event: ValidationEvent) -> None:
        self.event_counts[event.event_type] = self.event_counts.get(event.event_type, 0) + 1
        self._check_guards_before(event)
        next_state = TRANSITIONS.get(event.event_type)
        if event.event_type == "AudioInjected" and self._is_playback_progress_marker(event):
            next_state = None
        from_state = self.state
        from_parallel = self._parallel_states()
        if next_state:
            self.state = next_state
        self._apply_parallel_states(event)
        to_parallel = self._parallel_states()
        if not next_state and from_parallel == to_parallel:
            return
        self._record_transition(event, from_state, self.state, from_parallel, to_parallel)
        self.history.append(
            StateSnapshot(
                state=self.state,
                event_id=event.event_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                timestamp_ms=event.timestamp_ms,
                timestamp_wall=event.timestamp_wall,
                timestamp_wall_ms=event.timestamp_wall_ms,
                timestamp_monotonic_ms=event.timestamp_monotonic_ms,
                plugin=event.plugin,
            )
        )

    def _apply_parallel_states(self, event: ValidationEvent) -> None:
        if event.event_type == "AudioInjected":
            self.seen_audio_injected = True
            self.audio_state = "INJECTING"
            if not self._is_playback_progress_marker(event):
                self.recognition_state = "WAKE_PENDING"
        elif event.event_type == "AudioCompleted":
            self.audio_state = "IDLE"
        elif event.event_type == "WakeDetected":
            self.seen_wake = True
            if self.power_state == "REBOOTING":
                self.power_state = "ON"
            self.recognition_state = "LISTENING"
        elif event.event_type in {"ASRDetected", "CommandDetected"}:
            if self.power_state == "REBOOTING":
                self.power_state = "ON"
            self.recognition_state = "RECOGNIZING"
        elif event.event_type == "TTSStarted":
            self.media_state = "TTS_PLAYING"
            self.cloud_state = "RESPONDING" if event.source in {"ap", "asr", "upper", "ws63"} else self.cloud_state
        elif event.event_type == "MediaStarted":
            self.seen_media_started = True
            self.media_state = "MEDIA_PLAYING"
        elif event.event_type in {"MediaCompleted", "InterruptCompleted"}:
            self.media_state = "IDLE"
        elif event.event_type == "NetworkLost":
            self.network_state = "OFFLINE"
            self.cloud_state = "UNAVAILABLE"
        elif event.event_type == "NetworkRecovered":
            self.network_state = "ONLINE"
            self.cloud_state = "AVAILABLE"
            if self.power_state == "REBOOTING":
                self.power_state = "ON"
        elif event.event_type == "RebootDetected":
            self.power_state = "REBOOTING"
            self.recognition_state = "RESETTING"
            self.media_state = "IDLE"
        elif event.event_type == "CrashDetected":
            self.power_state = "CRASHED"
            self.recognition_state = "UNKNOWN"

    @staticmethod
    def _is_playback_progress_marker(event: ValidationEvent) -> bool:
        marker = str((event.payload or {}).get("marker", "") or "").strip().lower()
        return marker in {"play_iteration", "playback_finished"}

    def _parallel_states(self) -> Dict[str, str]:
        return {
            "audio": self.audio_state,
            "recognition": self.recognition_state,
            "media": self.media_state,
            "network": self.network_state,
            "power": self.power_state,
            "cloud": self.cloud_state,
        }

    def _record_transition(
        self,
        event: ValidationEvent,
        from_state: str,
        to_state: str,
        from_parallel: Dict[str, str],
        to_parallel: Dict[str, str],
    ) -> None:
        self.visited_states.add(to_state)
        for region, value in to_parallel.items():
            self.visited_parallel_states.setdefault(region, set()).add(value)
        self.transitions.append(
            StateTransition(
                event_id=event.event_id,
                event_type=event.event_type,
                from_state=from_state,
                to_state=to_state,
                from_parallel=from_parallel,
                to_parallel=to_parallel,
                timestamp_ms=event.timestamp_ms,
            )
        )

    def _check_guards_before(self, event: ValidationEvent) -> None:
        normal_after_crash = {
            "AudioInjected",
            "WakeDetected",
            "ASRDetected",
            "CommandDetected",
            "TTSStarted",
            "MediaStarted",
            "NetworkRecovered",
        }
        if self.power_state == "CRASHED" and event.event_type in normal_after_crash:
            self._violate(
                "normal_activity_after_crash",
                "error",
                event,
                "CrashDetected 后继续出现正常业务事件，需优先按设备/固件稳定性问题分析。",
            )
        if self.power_state == "REBOOTING" and event.event_type in normal_after_crash and not self.warned_activity_after_reboot:
            self.warned_activity_after_reboot = True
            self._violate(
                "activity_after_reboot_without_boot_marker",
                "warn",
                event,
                "RebootDetected 后未观察到明确 boot complete 标记就出现业务事件，记录为重启恢复链路证据不足。",
            )
        if event.event_type == "AudioCompleted" and not self.seen_audio_injected:
            self._violate(
                "audio_completed_without_injected",
                "warn",
                event,
                "AudioCompleted 早于任何 AudioInjected，播放证据顺序不完整。",
            )
        if event.event_type in {"ASRDetected", "CommandDetected"} and not self.seen_wake:
            self._violate(
                "recognition_without_wake_history",
                "warn",
                event,
                "识别事件早于任何 WakeDetected；若场景不是识别模式续测，需排查误识别或日志缺口。",
            )
        if event.event_type in {"TTSStarted", "MediaStarted"} and not (self.seen_wake or self.event_counts.get("ASRDetected") or self.event_counts.get("CommandDetected")):
            self._violate(
                "media_without_wake_or_asr_history",
                "warn",
                event,
                "媒体/播报事件缺少唤醒或识别前因，需结合场景判断是否为自播或日志缺口。",
            )
        if event.event_type == "MediaCompleted" and not self.seen_media_started:
            self._violate(
                "media_completed_without_started",
                "warn",
                event,
                "MediaCompleted 早于任何 MediaStarted，媒体播放证据顺序不完整。",
            )

    def _violate(self, name: str, severity: str, event: ValidationEvent, reason: str) -> None:
        self.violations.append(
            StateViolation(
                name=name,
                severity=severity,
                event_id=event.event_id,
                event_type=event.event_type,
                reason=reason,
                state=self.state,
                parallel_states=self._parallel_states(),
                timestamp_ms=event.timestamp_ms,
            )
        )

    def state_health(self) -> str:
        severities = {item.severity for item in self.violations}
        if "error" in severities or self.power_state == "CRASHED":
            return "FAIL"
        if "warn" in severities:
            return "WARN"
        return "PASS"

    def coverage(self) -> Dict[str, Any]:
        return {
            "transition_count": len(self.transitions),
            "visited_states": sorted(self.visited_states),
            "visited_parallel_states": {
                key: sorted(value)
                for key, value in sorted(self.visited_parallel_states.items())
            },
            "event_type_counts": dict(sorted(self.event_counts.items())),
            "violation_count": len(self.violations),
            "violation_severity_counts": {
                severity: sum(1 for item in self.violations if item.severity == severity)
                for severity in sorted({item.severity for item in self.violations})
            },
        }

    def run(self, events: List[ValidationEvent]) -> "RuntimeStateMachine":
        for event in events:
            self.apply(event)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "final_state": self.state,
            "parallel_states": {
                "audio": self.audio_state,
                "recognition": self.recognition_state,
                "media": self.media_state,
                "network": self.network_state,
                "power": self.power_state,
                "cloud": self.cloud_state,
            },
            "history": [item.__dict__ for item in self.history],
            "transitions": [item.to_dict() for item in self.transitions],
            "state_violations": [item.to_dict() for item in self.violations],
            "coverage": self.coverage(),
            "state_health": self.state_health(),
        }
