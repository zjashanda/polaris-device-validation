#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Serial/playback log parser for the runtime MVP."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..events import ValidationEvent, make_event, strip_ansi


LINE_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+\[(?P<port>[^/\]]+)\/(?P<role>[^\]]+)\]"
)
CP_KEYWORD_RE = re.compile(r"WAKE\(0\).*?KEY=\d+\((?P<keyword>[^)]*)\)", re.I)
WAKEUP_KEYWORD_RE = re.compile(r"wakeup_callback,\s*keyword:\s*(?P<keyword>.+)$", re.I)
ASR_CALLBACK_TEXT_RE = re.compile(r"(?:online|offline)_asr_callba(?:k|ck),\s*(?:text|keyword):\s*(?P<text>.+)$", re.I)
CLOUD_ASR_TEXT_RE = re.compile(r'"asr"\s*:\s*"(?P<text>[^"]*)"', re.I)
ALGO_KEYWORD_RE = re.compile(r'"keyword"\s*:\s*"(?P<keyword>[^"]*)"', re.I)
ALGO_INTENT_RE = re.compile(r'"intent"\s*:\s*"(?P<intent>[^"]*)"', re.I)
IGNORE_LOCAL_ASR_RE = re.compile(r"ignore local asr\s+(?P<keyword>.+?)\s+when cloud connected", re.I)


def _first_group(pattern: re.Pattern[str], text: str, group: str) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    return (match.group(group) or "").strip()


def infer_source(path: Path, line: str) -> str:
    match = LINE_PREFIX_RE.search(line)
    if match:
        return match.group("role").strip().lower()
    name = path.name.lower()
    for token in ["ap", "cp", "asr", "ws63", "upper", "control"]:
        if token in name:
            return token
    if "play" in name or "soundcard" in name or "audio" in name:
        return "audio"
    return "unknown"


def _payload_marker(clean: str, marker: str) -> Dict[str, Any]:
    return {"marker": marker, "line": clean[:500]}


def classify_events(path: Path, line_no: int, raw: str) -> List[ValidationEvent]:
    clean = strip_ansi(raw.strip()).lstrip("\ufeff")
    if not clean:
        return []
    low = clean.lower()
    source = infer_source(path, clean)
    events: List[ValidationEvent] = []

    def add(event_type: str, marker: str, **payload: Any) -> None:
        data = _payload_marker(clean, marker)
        data.update(payload)
        events.append(
            make_event(
                path=path,
                line_no=line_no,
                raw=clean,
                source=source,
                event_type=event_type,
                payload=data,
            )
        )

    if "play iteration" in low or "audioinjected" in clean:
        add("AudioInjected", "play_iteration")
    if "playback finished" in low or "audiocompleted" in clean:
        add("AudioCompleted", "playback_finished")

    wake_keyword = _first_group(WAKEUP_KEYWORD_RE, clean, "keyword")
    asr_text = _first_group(ASR_CALLBACK_TEXT_RE, clean, "text") or _first_group(CLOUD_ASR_TEXT_RE, clean, "text")
    cp_keyword = _first_group(CP_KEYWORD_RE, clean, "keyword")
    algo_keyword = _first_group(ALGO_KEYWORD_RE, clean, "keyword")
    algo_intent = _first_group(ALGO_INTENT_RE, clean, "intent")
    local_keyword = _first_group(IGNORE_LOCAL_ASR_RE, clean, "keyword")

    if "WAKE(" in clean:
        if "WAKE(1)" in clean:
            add("WakeDetected", "cp_wake")
        else:
            add("CommandDetected", "cp_keyword", recognized_command=cp_keyword, recognized_text=cp_keyword)
    if "Pre Wakeup" in clean:
        add("WakeDetected", "pre_wakeup")
    if "wakeup_callback" in clean:
        add("WakeDetected", "wakeup_callback", wake_keyword=wake_keyword, recognized_text=wake_keyword)
    if "offline_wakeup" in clean or "online_wakeup" in clean:
        add("WakeDetected", "asr_wakeup")
    if re.search(r"\bwakeup,\s*kid\b", low):
        if re.search(r"\bkid:\s*3\b", low):
            add("WakeDetected", "ap_wakeup_kid")
        else:
            add("CommandDetected", "ap_keyword_kid")

    if "cmd: 0x1005" in low or "cmd: 0x1006" in low:
        add("ASRDetected", "asr_cmd")
    if "offline_asr_callbak" in low or "offline_asr_callback" in low:
        add("ASRDetected", "offline_asr_callback", recognized_text=asr_text)
        add("CommandDetected", "offline_asr_callback", recognized_text=asr_text)
    if "online_asr" in low or "asr text" in low:
        add("ASRDetected", "online_asr", recognized_text=asr_text)
        if not re.search(r"(online_asr_callbak|online_asr_callback),\s*text:\s*$", clean):
            add("CommandDetected", "online_asr", recognized_text=asr_text)
    if "cloud.speech.trans.ack" in low and "\"asr\"" in low:
        add("ASRDetected", "cloud_speech_trans_ack", recognized_text=asr_text)
        add("CommandDetected", "cloud_speech_trans_ack", recognized_text=asr_text)
    if "ignore local asr" in low:
        add("CommandDetected", "local_asr_keyword", recognized_command=local_keyword, recognized_text=local_keyword)
    if "algo info" in low and "\"keyword\"" in low and "xiao mei xiao mei" not in low:
        add("CommandDetected", "algo_keyword", recognized_command=algo_keyword, recognized_intent=algo_intent, recognized_text=algo_keyword or algo_intent)

    if "offline_tts_callbak" in low or "offline_tts_callback" in low:
        add("TTSStarted", "offline_tts_callback")
    if "stream_tts" in low or "tts recv" in low or "audiobroadcast" in low:
        add("TTSStarted", "tts_or_audio_broadcast")

    if (
        "play next tone" in low
        or re.search(r"soundplayer status:\s*2\b", low)
        or re.search(r"ttsplayer status:\s*2\b", low)
        or "ttsplayer report state: play" in low
        or "tts playing with" in low
        or "local player status 2 playing" in low
        or "status=play" in low
    ):
        add("MediaStarted", "soundplayer_play")
    if (
        "playback_complete" in low
        or re.search(r"soundplayer status:\s*6\b", low)
        or re.search(r"ttsplayer status:\s*6\b", low)
        or "ttsplayer report state: stop" in low
        or "local player status 6 playback_complete" in low
        or "play complete" in low
    ):
        add("MediaCompleted", "soundplayer_complete")

    if "networklost" in clean or "network lost" in low or "wifi disconnected" in low or "disconnect" in low and "network" in low:
        add("NetworkLost", "network_lost")
    if "networkrecovered" in clean or "network recovered" in low or "online=true" in low or "cloud status :0x04" in low:
        add("NetworkRecovered", "network_recovered")

    if "Boot Reason" in clean or "Boot reason" in clean or "VENUSA BOOT" in clean:
        add("RebootDetected", "boot_reason")
    if re.search(r"\b(watchdog|panic|hardfault|fatal|crash|assert failed|assert_fail)\b", low):
        if "ignore exception" not in low and "player reset" not in low:
            add("CrashDetected", "crash_marker")

    return events


def parse_log_file(path: Path) -> List[ValidationEvent]:
    events: List[ValidationEvent] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        events.extend(classify_events(path, line_no, raw))
    return events


def parse_log_tree(root: Path, *, patterns: Iterable[str] = ("*.log",)) -> List[ValidationEvent]:
    events: List[ValidationEvent] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in root.rglob(pattern):
            if path in seen or not path.is_file():
                continue
            name = path.name.lower()
            if ".clean." in name or name in {"merged.log", "runtime.log"}:
                continue
            seen.add(path)
            events.extend(parse_log_file(path))
    return events
