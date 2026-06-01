#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract wake/recognition/cloud request links from Polaris serial logs."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, Optional

try:
    from pypinyin import lazy_pinyin  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    lazy_pinyin = None  # type: ignore


SOURCE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    r"\[(?P<port>[^/\]]+)/(?P<role>[^\]]+)\]\s*(?P<text>.*)$"
)
INTERNAL_TIME_RE = re.compile(r"\[(?P<time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]")
ONLINE_ASR_RE = re.compile(r"(?:online|offline)_asr_callbak,\s*(?:text|keyword):\s*(.+)$", re.I)
WAKE_HINT_RE = re.compile(r"(Pre Wakeup|wakeup_callback|multi_allow_wakeup_callback|mark has wakeup|online_wakeup|offline_wakeup)", re.I)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
MOJIBAKE_HINT_RE = re.compile(
    r"[\u0080-\u009f\u00a0-\u00ff\ufffd"
    r"\u6c13\u5fd9\u83bd\u76f2\u8305\u732b\u832b\u8302\u951f\u7d94"
    r"\u7481\u93b5\u9477\u93b4\u621e\u935a\u9429\u934b\u93be\u9410\u947f"
    r"\ue11b\ue15e\ue15f\u3126\u6f83\ufe3e\u6b91\u5a42\u3049\u93c8"
    r"\u95ca\u95ae\u93c2\u748b\u7d86]"
    r"|(\u677c\u626e\u77c6|\u5a11\u65bf\u724a|\u59b2\u6401\u5d37|\u9361\u6b91|\u93cd\u3127\u6443|\u8f70\u7c88|\u6d94\u581f|\u69f8\u9366|\u55d9\u6b91|\u6828\u74d5)"
)
URL_RE = re.compile(r'"url"\s*:\s*"(https?://[^"]+)"')
TTS_START_HINT_RE = re.compile(
    r"(offline_tts_callba[ck]|stream_tts|tts recv|tts playing with|ttsplayer report state:\s*play|ttsplayer status:\s*2\b)",
    re.I,
)
MEDIA_START_HINT_RE = re.compile(
    r"(_audio_play_next url=|audio player evt\s*2|audioplayer report state:\s*play|audioplayer state:?\s*2\b|"
    r"play next tone|local player status 2 playing|status=play)",
    re.I,
)
MEDIA_START_STATUS_RE = re.compile(r"(?:soundplayer|ttsplayer|audioplayer)\s+status:\s*2\b", re.I)
MEDIA_COMPLETE_HINT_RE = re.compile(
    r"(playback_complete|ttsplayer report state:\s*stop|audioplayer report state:\s*stop|"
    r"audio player evt\s*6|local player status 6 playback_complete|play complete|audio_player_end)",
    re.I,
)
MEDIA_COMPLETE_STATUS_RE = re.compile(r"(?:soundplayer|ttsplayer|audioplayer)\s+status:\s*6\b", re.I)


def _parse_time_ms(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text]
    if " " in text and "T" not in text:
        candidates.append(text.replace(" ", "T", 1))
    for item in candidates:
        try:
            return int(datetime.fromisoformat(item).timestamp() * 1000)
        except Exception:
            continue
    return None


def _event_time_ms(event: Dict[str, Any]) -> Optional[int]:
    return _parse_time_ms(event.get("time")) or _parse_time_ms(event.get("device_time"))


def _delta_ms(start: Optional[int], end: Optional[int]) -> Optional[int]:
    if start is None or end is None or end < start:
        return None
    return int(end - start)


def _line_context(raw_line: str, fallback_line_no: int) -> Dict[str, Any]:
    line = str(raw_line or "").rstrip("\n")
    match = SOURCE_RE.match(line)
    if match:
        text = match.group("text")
        internal = INTERNAL_TIME_RE.search(text)
        return {
            "time": match.group("time"),
            "device_time": internal.group("time") if internal else "",
            "port": match.group("port"),
            "role": match.group("role"),
            "line_no": fallback_line_no,
            "raw": line,
            "text": text,
        }
    return {
        "time": "",
        "device_time": "",
        "port": "",
        "role": "",
        "line_no": fallback_line_no,
        "raw": line,
        "text": line,
    }


def _iter_json_objects(text: str) -> Iterator[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
        except Exception:
            idx = start + 1
            continue
        if isinstance(obj, dict):
            yield obj
        idx = start + max(end, 1)


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _nested(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def _regex_field(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', text)
    return match.group(1) if match else ""


def _regex_number_field(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(\d+)', text)
    return match.group(1) if match else ""


def _mojibake_variants(text: str) -> List[str]:
    raw = str(text or "")
    variants = [raw]
    for encoding in ("latin1", "cp1252"):
        try:
            recovered = raw.encode(encoding).decode("utf-8")
        except Exception:
            continue
        if recovered and recovered not in variants:
            variants.append(recovered)
    return variants


def best_chinese_text(text: str) -> str:
    variants = _mojibake_variants(text)
    for value in variants:
        if CJK_RE.search(value) and not MOJIBAKE_HINT_RE.search(value):
            return value
    for value in variants:
        if CJK_RE.search(value):
            return value
    return str(text or "").strip()


def _looks_like_mojibake(text: str) -> bool:
    return bool(MOJIBAKE_HINT_RE.search(str(text or "")))


def text_to_pinyin(text: str) -> str:
    clean = best_chinese_text(text)
    if not clean or lazy_pinyin is None or not CJK_RE.search(clean):
        return ""
    try:
        pinyin = " ".join(lazy_pinyin(clean))  # type: ignore[misc]
        if "调" in clean:
            pinyin = re.sub(r"\bdiao\b", "tiao", pinyin)
        return pinyin
    except Exception:
        return ""


def _event_base(kind: str, ctx: Dict[str, Any], obj: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    topic = str(obj.get("topic", "") if isinstance(obj, dict) else "")
    root_session = obj.get("sessionId", "") if isinstance(obj, dict) else ""
    data = obj.get("data", {}) if isinstance(obj, dict) and isinstance(obj.get("data"), dict) else {}
    params = obj.get("params", {}) if isinstance(obj, dict) and isinstance(obj.get("params"), dict) else {}
    request = obj.get("request", {}) if isinstance(obj, dict) and isinstance(obj.get("request"), dict) else {}
    return {
        "kind": kind,
        "time": ctx.get("time", ""),
        "device_time": ctx.get("device_time", ""),
        "port": ctx.get("port", ""),
        "role": ctx.get("role", ""),
        "line_no": ctx.get("line_no", 0),
        "topic": topic,
        "mid": str(obj.get("mid", "") if isinstance(obj, dict) else ""),
        "sessionId": _first_text(root_session, data.get("sessionId"), params.get("sessionId"), request.get("sessionId")),
        "request_timestamp": request.get("timestamp", ""),
        "source_line": ctx.get("raw", ""),
    }


def _wake_event(ctx: Dict[str, Any], obj: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    event = _event_base("wake", ctx, obj)
    params = obj.get("params", {}) if isinstance(obj, dict) and isinstance(obj.get("params"), dict) else {}
    wake_word = _first_text(params.get("currentWakeUpWord"), params.get("wakeWord"))
    if not wake_word:
        wake_info = _nested(params, "afeStatus", "wakeupInfo", "rlt")
        if isinstance(wake_info, list) and wake_info:
            first = wake_info[0] if isinstance(wake_info[0], dict) else {}
            wake_word = _first_text(first.get("intent"), first.get("keyword"))
    if not wake_word and WAKE_HINT_RE.search(str(ctx.get("text", ""))):
        wake_word = "wake_marker"
    event.update({
        "wake_word": best_chinese_text(wake_word),
        "wake_pinyin": text_to_pinyin(wake_word),
        "deviceId": _first_text(params.get("deviceId"), obj.get("deviceId", "") if isinstance(obj, dict) else ""),
    })
    return event


def _recognition_event(ctx: Dict[str, Any], text: str, obj: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    event = _event_base("recognition", ctx, obj)
    text_cn = best_chinese_text(text)
    event.update({
        "asr_text": text_cn,
        "asr_pinyin": text_to_pinyin(text_cn),
        "asr_raw": str(text or ""),
        "asrVendor": _nested(obj or {}, "data", "asrVendor"),
    })
    return event


def _cloud_request_event(ctx: Dict[str, Any], obj: Dict[str, Any]) -> Dict[str, Any]:
    event = _event_base("cloud_request", ctx, obj)
    request = obj.get("request", {}) if isinstance(obj.get("request"), dict) else {}
    event.update({
        "recordId": str(request.get("recordId", "") or ""),
        "deviceId": str(obj.get("id", "") or obj.get("deviceId", "") or ""),
        "sn": str(obj.get("sn", "") or ""),
        "clientId": str(obj.get("clientId", "") or ""),
        "apiVer": str(request.get("apiVer", "") or ""),
    })
    return event


def _cloud_response_event(ctx: Dict[str, Any], obj: Dict[str, Any]) -> Dict[str, Any]:
    event = _event_base("cloud_response", ctx, obj)
    params = obj.get("params", {}) if isinstance(obj.get("params"), dict) else {}
    data = obj.get("data", {}) if isinstance(obj.get("data"), dict) else {}
    content = obj.get("content", []) if isinstance(obj.get("content"), list) else []
    urls: List[str] = []
    texts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "") or "").strip()
        text = str(item.get("text", "") or "").strip()
        if url:
            urls.append(url)
        if text:
            texts.append(best_chinese_text(text))
    skill = data.get("skill", {}) if isinstance(data.get("skill"), dict) else {}
    event.update({
        "mideaSkillId": str(params.get("mideaSkillId", "") or ""),
        "skillId": str(skill.get("skillId", "") or ""),
        "responseType": str(skill.get("responseType", "") or ""),
        "class": str(data.get("class", "") or ""),
        "asr_text": best_chinese_text(str(data.get("asr", "") or "")),
        "asr_pinyin": text_to_pinyin(str(data.get("asr", "") or "")),
        "content_texts": texts,
        "urls": urls,
    })
    return event


def _response_event(ctx: Dict[str, Any], kind: str, marker: str) -> Dict[str, Any]:
    event = _event_base(kind, ctx, None)
    event.update({
        "marker": marker,
        "line_no": ctx.get("line_no", 0),
        "source": f"{ctx.get('port', '')}/{ctx.get('role', '')}".strip("/"),
    })
    return event


def _regex_cloud_event(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text = str(ctx.get("text", "") or "")
    topic = _regex_field(text, "topic")
    mid = _regex_field(text, "mid")
    session_id = _regex_field(text, "sessionId")
    if not topic or not mid:
        return None
    base = {
        "kind": "cloud_response",
        "time": ctx.get("time", ""),
        "device_time": ctx.get("device_time", ""),
        "port": ctx.get("port", ""),
        "role": ctx.get("role", ""),
        "line_no": ctx.get("line_no", 0),
        "topic": topic,
        "mid": mid,
        "sessionId": session_id,
        "request_timestamp": _regex_number_field(text, "timestamp"),
        "source_line": ctx.get("raw", ""),
    }
    if topic == "cloud.speech.trans":
        base.update({
            "kind": "cloud_request",
            "recordId": _regex_field(text, "recordId"),
            "deviceId": _regex_field(text, "id") or _regex_field(text, "deviceId"),
            "sn": _regex_field(text, "sn"),
            "clientId": _regex_field(text, "clientId"),
            "apiVer": _regex_field(text, "apiVer"),
        })
        return base
    if topic == "cloud.speech.trans.ack":
        asr_text = _regex_field(text, "asr")
        base.update({
            "kind": "recognition",
            "asr_text": best_chinese_text(asr_text),
            "asr_pinyin": text_to_pinyin(asr_text),
            "asr_raw": asr_text,
            "asrVendor": _regex_field(text, "asrVendor"),
        })
        return base
    if topic.startswith("cloud.instructions") or topic in {"cloud.speech.reply", "cloud.transmit.classifyResult"}:
        asr_text = _regex_field(text, "asr")
        base.update({
            "mideaSkillId": _regex_field(text, "mideaSkillId"),
            "skillId": _regex_field(text, "skillId"),
            "responseType": _regex_field(text, "responseType"),
            "class": _regex_field(text, "class"),
            "asr_text": best_chinese_text(asr_text),
            "asr_pinyin": text_to_pinyin(asr_text),
            "content_texts": [best_chinese_text(value) for value in re.findall(r'"text"\s*:\s*"([^"]*)"', text) if value],
            "urls": URL_RE.findall(text),
        })
        return base
    return None


def _append_unique(items: List[Dict[str, Any]], event: Dict[str, Any], key_fields: Iterable[str]) -> None:
    signature = tuple(str(event.get(field, "")) for field in key_fields)
    for item in items:
        if tuple(str(item.get(field, "")) for field in key_fields) == signature:
            return
    items.append(event)


def _build_interactions(
    wake_events: List[Dict[str, Any]],
    recognition_events: List[Dict[str, Any]],
    cloud_requests: List[Dict[str, Any]],
    cloud_responses: List[Dict[str, Any]],
    response_events: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    grouped: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def key_for(event: Dict[str, Any]) -> str:
        return str(event.get("sessionId") or event.get("mid") or f"line:{event.get('line_no')}")

    def ensure(event: Dict[str, Any]) -> Dict[str, Any]:
        key = key_for(event)
        if key not in grouped:
            grouped[key] = {
                "sessionId": event.get("sessionId", ""),
                "mid": event.get("mid", ""),
                "recordId": "",
                "wake": {},
                "recognition": {},
                "cloud_request": {},
                "cloud_responses": [],
                "cloud_topics": [],
                "media_urls": [],
                "response_events": [],
            }
        if event.get("sessionId") and not grouped[key].get("sessionId"):
            grouped[key]["sessionId"] = event.get("sessionId")
        if event.get("mid") and not grouped[key].get("mid"):
            grouped[key]["mid"] = event.get("mid")
        return grouped[key]

    for event in wake_events:
        item = ensure(event)
        if not item.get("wake"):
            item["wake"] = {
                "time": event.get("time", ""),
                "device_time": event.get("device_time", ""),
                "wake_word": event.get("wake_word", ""),
                "wake_pinyin": event.get("wake_pinyin", ""),
                "source": f"{event.get('port')}/{event.get('role')}",
                "line_no": event.get("line_no", 0),
            }
    for event in cloud_requests:
        item = ensure(event)
        if event.get("mid"):
            item["mid"] = event.get("mid")
        item["cloud_request"] = {
            "time": event.get("time", ""),
            "topic": event.get("topic", ""),
            "mid": event.get("mid", ""),
            "sessionId": event.get("sessionId", ""),
            "recordId": event.get("recordId", ""),
            "deviceId": event.get("deviceId", ""),
            "sn": event.get("sn", ""),
            "clientId": event.get("clientId", ""),
            "line_no": event.get("line_no", 0),
        }
        item["recordId"] = event.get("recordId", "")
    for event in recognition_events:
        item = ensure(event)
        if event.get("mid"):
            item["mid"] = event.get("mid")
        current_rec = item.get("recognition") if isinstance(item.get("recognition"), dict) else {}
        current_text = str(current_rec.get("asr_text", "") if current_rec else "")
        new_text = str(event.get("asr_text", "") or "")
        should_replace = (
            not current_rec
            or (current_text and _looks_like_mojibake(current_text) and new_text and not _looks_like_mojibake(new_text))
        )
        if should_replace:
            item["recognition"] = {
                "time": event.get("time", ""),
                "topic": event.get("topic", ""),
                "mid": event.get("mid", ""),
                "sessionId": event.get("sessionId", ""),
                "asr_text": event.get("asr_text", ""),
                "asr_pinyin": event.get("asr_pinyin", ""),
                "asr_raw": event.get("asr_raw", ""),
                "asrVendor": event.get("asrVendor", ""),
                "line_no": event.get("line_no", 0),
            }
    for event in cloud_responses:
        item = ensure(event)
        if event.get("mid"):
            item["mid"] = event.get("mid")
        topic = str(event.get("topic", "") or "")
        if topic and topic not in item["cloud_topics"]:
            item["cloud_topics"].append(topic)
        for url in event.get("urls", []) or []:
            if url not in item["media_urls"]:
                item["media_urls"].append(url)
        item["cloud_responses"].append({
            "time": event.get("time", ""),
            "topic": topic,
            "mid": event.get("mid", ""),
            "sessionId": event.get("sessionId", ""),
            "mideaSkillId": event.get("mideaSkillId", ""),
            "skillId": event.get("skillId", ""),
            "asr_text": event.get("asr_text", ""),
            "asr_pinyin": event.get("asr_pinyin", ""),
            "content_texts": event.get("content_texts", []),
            "urls": event.get("urls", []),
            "line_no": event.get("line_no", 0),
        })
    interactions = _merge_interaction_groups(list(grouped.values()))
    _attach_temporal_wakes(interactions, wake_events)
    _attach_temporal_responses(interactions, response_events)
    for item in interactions:
        item["latency"] = _build_latency(item)
    return interactions


def _merge_interaction_groups(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    by_session: Dict[str, Dict[str, Any]] = {}
    by_mid: Dict[str, Dict[str, Any]] = {}

    def merge_into(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        source_has_online_mid = bool(source.get("cloud_request") or source.get("recognition") or source.get("cloud_responses"))
        if source_has_online_mid and source.get("mid"):
            target["mid"] = source.get("mid")
        for key in ("sessionId", "mid", "recordId"):
            if source.get(key) and not target.get(key):
                target[key] = source.get(key)
        for key in ("wake", "recognition", "cloud_request"):
            if source.get(key) and not target.get(key):
                target[key] = source.get(key)
        for topic in source.get("cloud_topics", []) or []:
            if topic not in target["cloud_topics"]:
                target["cloud_topics"].append(topic)
        for url in source.get("media_urls", []) or []:
            if url not in target["media_urls"]:
                target["media_urls"].append(url)
        target["cloud_responses"].extend(source.get("cloud_responses", []) or [])
        target.setdefault("response_events", []).extend(source.get("response_events", []) or [])
        return target

    for item in items:
        session_id = str(item.get("sessionId", "") or "")
        mid = str(item.get("mid", "") or "")
        target: Optional[Dict[str, Any]] = None
        if session_id and session_id != "0" and session_id in by_session:
            target = by_session[session_id]
        elif mid and mid in by_mid:
            target = by_mid[mid]
        if target is None:
            target = item
            merged.append(target)
        else:
            merge_into(target, item)
        if session_id and session_id != "0":
            by_session[session_id] = target
        if mid:
            by_mid[mid] = target
        if target.get("sessionId") and str(target.get("sessionId")) != "0":
            by_session[str(target.get("sessionId"))] = target
        if target.get("mid"):
            by_mid[str(target.get("mid"))] = target
    return _merge_text_only_interactions(merged)


def _merge_text_only_interactions(items: List[Dict[str, Any]], *, max_gap_ms: int = 3000) -> List[Dict[str, Any]]:
    """Fold local ASR callback-only groups into the matching cloud interaction."""
    consumed = set()
    for idx, item in enumerate(items):
        if item.get("mid") or item.get("sessionId") or item.get("cloud_request") or item.get("cloud_responses"):
            continue
        recognition = item.get("recognition", {}) if isinstance(item.get("recognition"), dict) else {}
        rec_text = str(recognition.get("asr_text", "") or "")
        rec_pinyin = str(recognition.get("asr_pinyin", "") or "")
        rec_ms = _event_time_ms(recognition)
        if rec_ms is None:
            continue
        candidates: List[tuple[int, int, Dict[str, Any]]] = []
        for target_idx, target in enumerate(items):
            if target_idx == idx:
                continue
            has_cloud_chain = bool(target.get("mid") or target.get("cloud_request") or target.get("cloud_responses"))
            if not has_cloud_chain:
                continue
            target_rec = target.get("recognition", {}) if isinstance(target.get("recognition"), dict) else {}
            target_text = str(target_rec.get("asr_text", "") or "")
            target_pinyin = str(target_rec.get("asr_pinyin", "") or "")
            target_ms = _event_time_ms(target_rec) or _item_anchor_ms(target)
            if target_ms is None:
                continue
            gap = abs(rec_ms - target_ms)
            if gap > max_gap_ms:
                continue
            item_wake = item.get("wake", {}) if isinstance(item.get("wake"), dict) else {}
            target_wake = target.get("wake", {}) if isinstance(target.get("wake"), dict) else {}
            same_wake = _event_time_ms(item_wake) is not None and _event_time_ms(item_wake) == _event_time_ms(target_wake)
            same_cjk_prefix = bool(rec_text and target_text and rec_text[:3] == target_text[:3])
            text_compatible = (
                not rec_text
                or not target_text
                or rec_text == target_text
                or rec_text in target_text
                or target_text in rec_text
                or _looks_like_mojibake(target_text)
                or (rec_pinyin and target_pinyin and rec_pinyin == target_pinyin)
                or same_wake
                or same_cjk_prefix
            )
            if text_compatible:
                candidates.append((gap, target_idx, target))
        if not candidates:
            continue
        _, _, target = sorted(candidates, key=lambda value: value[0])[0]
        target_rec = target.get("recognition", {}) if isinstance(target.get("recognition"), dict) else {}
        target_text = str(target_rec.get("asr_text", "") or "")
        if rec_text and (not target_text or _looks_like_mojibake(target_text)):
            target["recognition"] = recognition
        if not target.get("wake") and item.get("wake"):
            target["wake"] = item.get("wake")
        consumed.add(idx)
    return [item for idx, item in enumerate(items) if idx not in consumed]


def _event_brief(event: Dict[str, Any], *, include_text: bool = False) -> Dict[str, Any]:
    brief = {
        "time": event.get("time", ""),
        "device_time": event.get("device_time", ""),
        "source": f"{event.get('port', '')}/{event.get('role', '')}".strip("/"),
        "line_no": event.get("line_no", 0),
    }
    for key in ("kind", "marker", "topic", "mid", "sessionId"):
        if event.get(key):
            brief[key] = event.get(key)
    if include_text:
        for key in ("wake_word", "wake_pinyin", "asr_text", "asr_pinyin", "asr_raw"):
            if event.get(key):
                brief[key] = event.get(key)
    return brief


def _item_anchor_ms(item: Dict[str, Any]) -> Optional[int]:
    for key in ("recognition", "cloud_request", "wake"):
        payload = item.get(key)
        if isinstance(payload, dict):
            ms = _event_time_ms(payload)
            if ms is not None:
                return ms
    responses = item.get("cloud_responses", []) if isinstance(item.get("cloud_responses"), list) else []
    values = [_event_time_ms(event) for event in responses if isinstance(event, dict)]
    values = [value for value in values if value is not None]
    return min(values) if values else None


def _attach_temporal_wakes(items: List[Dict[str, Any]], wake_events: List[Dict[str, Any]], *, max_gap_ms: int = 20000) -> None:
    timed_wakes = [(event, _event_time_ms(event)) for event in wake_events]
    timed_wakes = [(event, ms) for event, ms in timed_wakes if ms is not None]
    for item in items:
        if item.get("wake"):
            continue
        anchor_ms = _item_anchor_ms(item)
        if anchor_ms is None:
            continue
        candidates = [(event, ms) for event, ms in timed_wakes if ms <= anchor_ms and anchor_ms - ms <= max_gap_ms]
        if not candidates:
            continue
        event, _ = sorted(candidates, key=lambda pair: int(pair[1] or 0))[-1]
        item["wake"] = _event_brief(event, include_text=True)


def _attach_temporal_responses(items: List[Dict[str, Any]], response_events: List[Dict[str, Any]], *, max_gap_ms: int = 45000) -> None:
    timed = [(event, _event_time_ms(event)) for event in response_events]
    timed = [(event, ms) for event, ms in timed if ms is not None]
    for item in items:
        anchor_ms = _item_anchor_ms(item)
        if anchor_ms is None:
            continue
        next_boundary_ms: Optional[int] = None
        for other in items:
            if other is item:
                continue
            other_has_online_chain = bool(
                other.get("recognition") or other.get("cloud_request") or other.get("cloud_responses")
            )
            if not other_has_online_chain:
                continue
            candidates: List[Optional[int]] = [_item_anchor_ms(other)]
            other_wake = other.get("wake", {}) if isinstance(other.get("wake"), dict) else {}
            candidates.append(_event_time_ms(other_wake))
            for candidate in candidates:
                if candidate is None or candidate <= anchor_ms:
                    continue
                if next_boundary_ms is None or candidate < next_boundary_ms:
                    next_boundary_ms = candidate
        matched = [
            _event_brief(event)
            for event, ms in timed
            if ms >= anchor_ms and ms - anchor_ms <= max_gap_ms
            and (next_boundary_ms is None or ms < next_boundary_ms)
        ]
        if matched:
            item["response_events"] = matched[:20]


def _first_timed(events: Iterable[Dict[str, Any]], *, kind: str = "", topic: str = "", topic_prefix: str = "") -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if kind and str(event.get("kind", "")) != kind:
            continue
        event_topic = str(event.get("topic", "") or "")
        if topic and event_topic != topic:
            continue
        if topic_prefix and not event_topic.startswith(topic_prefix):
            continue
        if _event_time_ms(event) is None:
            continue
        candidates.append(event)
    if not candidates:
        return None
    return sorted(candidates, key=lambda event: int(_event_time_ms(event) or 0))[0]


def _add_latency(latency: Dict[str, Any], key: str, start_ms: Optional[int], end_ms: Optional[int]) -> None:
    value = _delta_ms(start_ms, end_ms)
    if value is not None:
        latency[key] = value


def _build_latency(item: Dict[str, Any]) -> Dict[str, Any]:
    wake = item.get("wake", {}) if isinstance(item.get("wake"), dict) else {}
    recognition = item.get("recognition", {}) if isinstance(item.get("recognition"), dict) else {}
    cloud_request = item.get("cloud_request", {}) if isinstance(item.get("cloud_request"), dict) else {}
    cloud_responses = item.get("cloud_responses", []) if isinstance(item.get("cloud_responses"), list) else []
    response_events = item.get("response_events", []) if isinstance(item.get("response_events"), list) else []

    wake_ms = _event_time_ms(wake)
    rec_ms = _event_time_ms(recognition)
    req_ms = _event_time_ms(cloud_request)
    first_cloud_response = _first_timed(cloud_responses)
    audio_broadcast = _first_timed(cloud_responses, topic_prefix="cloud.instructions")
    speech_reply = _first_timed(cloud_responses, topic="cloud.speech.reply")
    tts_start = _first_timed(response_events, kind="tts_start")
    media_start = _first_timed(response_events, kind="media_start")
    first_response_event = _first_timed(response_events)
    first_response_start = tts_start or media_start or first_response_event
    response_complete = _first_timed(response_events, kind="media_complete")

    latency: Dict[str, Any] = {
        "reference": "outer_log_iso_time_or_device_time",
        "anchors": {
            "wake_time": wake.get("time", ""),
            "recognition_time": recognition.get("time", ""),
            "cloud_request_time": cloud_request.get("time", ""),
            "first_cloud_response_time": first_cloud_response.get("time", "") if first_cloud_response else "",
            "audio_broadcast_time": audio_broadcast.get("time", "") if audio_broadcast else "",
            "speech_reply_time": speech_reply.get("time", "") if speech_reply else "",
            "tts_start_time": tts_start.get("time", "") if tts_start else "",
            "media_start_time": media_start.get("time", "") if media_start else "",
            "media_complete_time": response_complete.get("time", "") if response_complete else "",
        },
        "limitations": [],
    }
    _add_latency(latency, "wake_to_recognition_ms", wake_ms, rec_ms)
    _add_latency(latency, "wake_to_cloud_request_ms", wake_ms, req_ms)
    _add_latency(latency, "wake_to_first_cloud_response_ms", wake_ms, _event_time_ms(first_cloud_response or {}))
    _add_latency(latency, "wake_to_tts_start_ms", wake_ms, _event_time_ms(tts_start or {}))
    _add_latency(latency, "wake_to_media_start_ms", wake_ms, _event_time_ms(media_start or {}))
    _add_latency(latency, "cloud_request_to_recognition_ms", req_ms, rec_ms)
    _add_latency(latency, "recognition_to_cloud_request_ms", rec_ms, req_ms)
    _add_latency(latency, "cloud_request_to_first_cloud_response_ms", req_ms, _event_time_ms(first_cloud_response or {}))
    _add_latency(latency, "cloud_request_to_audio_broadcast_ms", req_ms, _event_time_ms(audio_broadcast or {}))
    _add_latency(latency, "cloud_request_to_speech_reply_ms", req_ms, _event_time_ms(speech_reply or {}))
    _add_latency(latency, "cloud_request_to_tts_start_ms", req_ms, _event_time_ms(tts_start or {}))
    _add_latency(latency, "cloud_request_to_media_start_ms", req_ms, _event_time_ms(media_start or {}))
    _add_latency(latency, "recognition_to_first_cloud_response_ms", rec_ms, _event_time_ms(first_cloud_response or {}))
    _add_latency(latency, "recognition_to_audio_broadcast_ms", rec_ms, _event_time_ms(audio_broadcast or {}))
    _add_latency(latency, "recognition_to_speech_reply_ms", rec_ms, _event_time_ms(speech_reply or {}))
    _add_latency(latency, "recognition_to_tts_start_ms", rec_ms, _event_time_ms(tts_start or {}))
    _add_latency(latency, "recognition_to_media_start_ms", rec_ms, _event_time_ms(media_start or {}))
    _add_latency(latency, "first_cloud_response_to_tts_start_ms", _event_time_ms(first_cloud_response or {}), _event_time_ms(tts_start or {}))
    _add_latency(latency, "first_cloud_response_to_media_start_ms", _event_time_ms(first_cloud_response or {}), _event_time_ms(media_start or {}))
    _add_latency(latency, "audio_broadcast_to_tts_start_ms", _event_time_ms(audio_broadcast or {}), _event_time_ms(tts_start or {}))
    _add_latency(latency, "audio_broadcast_to_media_start_ms", _event_time_ms(audio_broadcast or {}), _event_time_ms(media_start or {}))
    _add_latency(latency, "tts_start_to_media_start_ms", _event_time_ms(tts_start or {}), _event_time_ms(media_start or {}))
    _add_latency(latency, "tts_or_media_play_duration_ms", _event_time_ms(first_response_start or {}), _event_time_ms(response_complete or {}))

    if not wake:
        latency["limitations"].append("missing_wake_event")
    if not recognition:
        latency["limitations"].append("missing_recognition_event")
    if cloud_request and not first_cloud_response:
        latency["limitations"].append("missing_cloud_response_event")
    if not first_response_start:
        latency["limitations"].append("missing_tts_or_media_start_event")
    return latency


def extract_interaction_trace(lines: Iterable[str]) -> Dict[str, Any]:
    wake_events: List[Dict[str, Any]] = []
    recognition_events: List[Dict[str, Any]] = []
    cloud_requests: List[Dict[str, Any]] = []
    cloud_responses: List[Dict[str, Any]] = []
    response_events: List[Dict[str, Any]] = []

    for line_no, raw_line in enumerate(lines, start=1):
        ctx = _line_context(str(raw_line), line_no)
        text = str(ctx.get("text", ""))
        json_objects = list(_iter_json_objects(text))
        for obj in json_objects:
            topic = str(obj.get("topic", "") or "")
            if topic == "device.report.wakeInfo":
                _append_unique(wake_events, _wake_event(ctx, obj), ("line_no", "sessionId", "mid"))
            elif topic == "cloud.speech.trans":
                _append_unique(cloud_requests, _cloud_request_event(ctx, obj), ("mid", "sessionId", "recordId"))
            elif topic == "cloud.speech.trans.ack":
                _append_unique(
                    recognition_events,
                    _recognition_event(ctx, str(_nested(obj, "data", "asr") or ""), obj),
                    ("mid", "sessionId", "asr_text"),
                )
            elif topic.startswith("cloud.instructions") or topic in {"cloud.speech.reply", "cloud.transmit.classifyResult"}:
                _append_unique(cloud_responses, _cloud_response_event(ctx, obj), ("line_no", "mid", "topic"))

        regex_event = _regex_cloud_event(ctx)
        if regex_event:
            kind = str(regex_event.get("kind", ""))
            if kind == "cloud_request":
                _append_unique(cloud_requests, regex_event, ("mid", "sessionId", "recordId"))
            elif kind == "recognition":
                _append_unique(recognition_events, regex_event, ("mid", "sessionId", "asr_text"))
            elif kind == "cloud_response":
                _append_unique(cloud_responses, regex_event, ("line_no", "mid", "topic"))

        match = ONLINE_ASR_RE.search(text)
        if match:
            _append_unique(recognition_events, _recognition_event(ctx, match.group(1), None), ("line_no", "asr_text"))
        if not json_objects and WAKE_HINT_RE.search(text):
            _append_unique(wake_events, _wake_event(ctx, None), ("line_no", "time", "port"))
        if TTS_START_HINT_RE.search(text):
            _append_unique(response_events, _response_event(ctx, "tts_start", "tts_or_audio_broadcast"), ("line_no", "kind", "marker"))
        if MEDIA_START_HINT_RE.search(text) or MEDIA_START_STATUS_RE.search(text):
            _append_unique(response_events, _response_event(ctx, "media_start", "player_start"), ("line_no", "kind", "marker"))
        if MEDIA_COMPLETE_HINT_RE.search(text) or MEDIA_COMPLETE_STATUS_RE.search(text):
            _append_unique(response_events, _response_event(ctx, "media_complete", "player_complete"), ("line_no", "kind", "marker"))

    interactions = _build_interactions(wake_events, recognition_events, cloud_requests, cloud_responses, response_events)
    request_ids = []
    latency_samples = []
    for item in interactions:
        has_online_chain = bool(item.get("cloud_request") or item.get("recognition") or item.get("cloud_responses"))
        latency = item.get("latency", {}) if isinstance(item.get("latency"), dict) else {}
        has_latency = any(str(key).endswith("_ms") for key in latency)
        if has_latency:
            latency_samples.append({
                "mid": item.get("mid", ""),
                "sessionId": item.get("sessionId", ""),
                "recordId": item.get("recordId", ""),
                "asr_text": (item.get("recognition") or {}).get("asr_text", ""),
                "asr_pinyin": (item.get("recognition") or {}).get("asr_pinyin", ""),
                "wake_word": (item.get("wake") or {}).get("wake_word", ""),
                "latency": latency,
            })
        if has_online_chain and (item.get("mid") or item.get("sessionId") or item.get("recordId")):
            request_ids.append({
                "mid": item.get("mid", ""),
                "sessionId": item.get("sessionId", ""),
                "recordId": item.get("recordId", ""),
                "asr_text": (item.get("recognition") or {}).get("asr_text", ""),
                "asr_pinyin": (item.get("recognition") or {}).get("asr_pinyin", ""),
                "wake_word": (item.get("wake") or {}).get("wake_word", ""),
                "wake_pinyin": (item.get("wake") or {}).get("wake_pinyin", ""),
                "cloud_topics": item.get("cloud_topics", []),
                "media_urls": item.get("media_urls", []),
                "latency": latency,
            })
    return {
        "wake_events": wake_events,
        "recognition_events": recognition_events,
        "cloud_requests": cloud_requests,
        "cloud_responses": cloud_responses,
        "response_events": response_events,
        "interactions": interactions,
        "online_request_ids": request_ids,
        "latency_samples": latency_samples,
    }
