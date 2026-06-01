#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiny assertion DSL compiler for replay timelines.

Supported examples:
- EXPECT WakeDetected
- EXPECT WakeDetected WITHIN 3000ms AFTER AudioInjected
- FORBID RebootDetected FOR 10000ms AFTER WakeDetected
- EXPECT_SEQUENCE WakeDetected -> ASRDetected -> MediaStarted WITHIN 15000ms
- EXPECT_RESPONSE TTSStarted|MediaStarted WITHIN 15000ms AFTER ASRDetected|CommandDetected
- EXPECT_DURATION MediaStarted TO MediaCompleted >= 500ms
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .assertion_engine import assert_event_exists, assert_event_within_ms, assert_no_event_during
from .events import ValidationEvent, event_time_ms
from .timeline import Timeline


EXPECT_WITHIN_RE = re.compile(r"^EXPECT\s+(?P<event>\w+)\s+WITHIN\s+(?P<ms>\d+)ms(?:\s+AFTER\s+(?P<anchor>\w+))?$", re.I)
EXPECT_RE = re.compile(r"^EXPECT\s+(?P<event>\w+)$", re.I)
FORBID_RE = re.compile(r"^FORBID\s+(?P<event>\w+)\s+FOR\s+(?P<ms>\d+)ms(?:\s+AFTER\s+(?P<anchor>\w+))?$", re.I)
SEQUENCE_RE = re.compile(r"^EXPECT_SEQUENCE\s+(?P<events>[A-Za-z0-9_| ]+(?:\s*->\s*[A-Za-z0-9_| ]+)+)\s+WITHIN\s+(?P<ms>\d+)ms$", re.I)
RESPONSE_RE = re.compile(r"^EXPECT_RESPONSE\s+(?P<response>[A-Za-z0-9_|]+)\s+WITHIN\s+(?P<ms>\d+)ms\s+AFTER\s+(?P<anchor>[A-Za-z0-9_|]+)$", re.I)
DURATION_RE = re.compile(r"^EXPECT_DURATION\s+(?P<start>\w+)\s+TO\s+(?P<end>\w+)\s*>=\s*(?P<ms>\d+)ms$", re.I)


def _choices(value: str) -> List[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


def _matches(event: ValidationEvent, choices: List[str]) -> bool:
    return event.event_type in set(choices)


def _find_after(events: List[ValidationEvent], choices: List[str], after_ms: int | None = None) -> ValidationEvent | None:
    for event in events:
        current = event_time_ms(event)
        if after_ms is not None and current is not None and current < after_ms:
            continue
        if _matches(event, choices):
            return event
    return None


def _find_ordered_chain(timeline: Timeline, chain: List[List[str]], within_ms: int) -> Dict[str, Any]:
    if not chain:
        return {"ok": False, "reason": "empty chain", "events": []}
    for anchor in timeline.events:
        if not _matches(anchor, chain[0]):
            continue
        anchor_ms = event_time_ms(anchor)
        if anchor_ms is None:
            continue
        selected = [anchor]
        cursor_ms = anchor_ms
        ok = True
        for choices in chain[1:]:
            nxt = _find_after(timeline.events, choices, after_ms=cursor_ms)
            if not nxt:
                ok = False
                break
            nxt_ms = event_time_ms(nxt)
            if nxt_ms is None:
                ok = False
                break
            selected.append(nxt)
            cursor_ms = nxt_ms
        if not ok:
            continue
        delta = int(cursor_ms - anchor_ms)
        if delta <= within_ms:
            return {"ok": True, "events": selected, "delta_ms": delta}
    return {"ok": False, "events": [], "delta_ms": None}


def _event_brief(event: ValidationEvent) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "source": event.source,
        "timestamp_ms": event_time_ms(event),
    }


def _assert_sequence(timeline: Timeline, raw_chain: str, within_ms: int) -> Dict[str, Any]:
    chain = [_choices(part.strip()) for part in raw_chain.split("->")]
    result = _find_ordered_chain(timeline, chain, within_ms)
    name = "sequence:" + "->".join("|".join(item) for item in chain)
    if result["ok"]:
        return {
            "name": name,
            "result": "PASS",
            "reason": f"观察到目标事件序列，整体耗时 {result['delta_ms']}ms <= {within_ms}ms。",
            "actual": {"delta_ms": result["delta_ms"], "events": [_event_brief(item) for item in result["events"]]},
        }
    return {
        "name": name,
        "result": "FAIL",
        "reason": f"未在 {within_ms}ms 内观察到目标事件序列。",
        "actual": {"chain": chain, "within_ms": within_ms},
    }


def _assert_response(timeline: Timeline, response: str, anchor: str, within_ms: int) -> Dict[str, Any]:
    anchor_choices = _choices(anchor)
    response_choices = _choices(response)
    for anchor_event in timeline.events:
        if not _matches(anchor_event, anchor_choices):
            continue
        anchor_ms = event_time_ms(anchor_event)
        if anchor_ms is None:
            continue
        response_event = _find_after(timeline.events, response_choices, after_ms=anchor_ms)
        if not response_event:
            continue
        response_ms = event_time_ms(response_event)
        if response_ms is None:
            continue
        delta = int(response_ms - anchor_ms)
        if delta <= within_ms:
            return {
                "name": f"response:{anchor}->{response}",
                "result": "PASS",
                "reason": f"观察到 {anchor} 后 {delta}ms 内出现 {response} 响应。",
                "actual": {"delta_ms": delta, "anchor": _event_brief(anchor_event), "response": _event_brief(response_event)},
            }
    return {
        "name": f"response:{anchor}->{response}",
        "result": "FAIL",
        "reason": f"未在 {within_ms}ms 内观察到 {anchor} 后的 {response} 响应。",
        "actual": {"anchor": anchor_choices, "response": response_choices, "within_ms": within_ms},
    }


def _assert_duration(timeline: Timeline, start: str, end: str, minimum_ms: int) -> Dict[str, Any]:
    for start_event in timeline.find(start):
        start_ms = event_time_ms(start_event)
        if start_ms is None:
            continue
        end_event = _find_after(timeline.events, [end], after_ms=start_ms)
        if not end_event:
            continue
        end_ms = event_time_ms(end_event)
        if end_ms is None:
            continue
        delta = int(end_ms - start_ms)
        if delta >= minimum_ms:
            return {
                "name": f"duration:{start}->{end}",
                "result": "PASS",
                "reason": f"{start} 到 {end} 持续 {delta}ms >= {minimum_ms}ms。",
                "actual": {"duration_ms": delta, "start": _event_brief(start_event), "end": _event_brief(end_event)},
            }
    return {
        "name": f"duration:{start}->{end}",
        "result": "FAIL",
        "reason": f"未观察到 {start} 到 {end} 持续时间 >= {minimum_ms}ms 的窗口。",
        "actual": {"start": start, "end": end, "minimum_ms": minimum_ms},
    }


def evaluate_dsl(lines: List[str], timeline: Timeline) -> Dict[str, Any]:
    assertions = []
    for raw in lines:
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        match = SEQUENCE_RE.match(line)
        if match:
            assertions.append(_assert_sequence(timeline, match.group("events"), int(match.group("ms"))))
            continue
        match = RESPONSE_RE.match(line)
        if match:
            assertions.append(_assert_response(timeline, match.group("response"), match.group("anchor"), int(match.group("ms"))))
            continue
        match = DURATION_RE.match(line)
        if match:
            assertions.append(_assert_duration(timeline, match.group("start"), match.group("end"), int(match.group("ms"))))
            continue
        match = EXPECT_WITHIN_RE.match(line)
        if match:
            assertions.append(
                assert_event_within_ms(
                    timeline,
                    match.group("event"),
                    within_ms=int(match.group("ms")),
                    anchor_event_type=match.group("anchor") or "AudioInjected",
                ).to_dict()
            )
            continue
        match = EXPECT_RE.match(line)
        if match:
            assertions.append(assert_event_exists(timeline, match.group("event")).to_dict())
            continue
        match = FORBID_RE.match(line)
        if match:
            assertions.append(
                assert_no_event_during(
                    timeline,
                    match.group("event"),
                    duration_ms=int(match.group("ms")),
                    anchor_event_type=match.group("anchor"),
                ).to_dict()
            )
            continue
        assertions.append({"name": "dsl_parse", "result": "FAIL", "reason": f"unsupported DSL: {line}", "actual": {"line": line}})
    result = "FAIL" if any(item.get("result") == "FAIL" for item in assertions) else "PASS"
    return {"schema": "polaris.assertion_dsl_result.v1", "result": result, "assertions": assertions}
