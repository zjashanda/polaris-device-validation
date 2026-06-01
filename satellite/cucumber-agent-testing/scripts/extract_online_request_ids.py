#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract online ASR request IDs from Polaris serial logs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.logs.polaris_interaction_trace import extract_interaction_trace  # noqa: E402


LATENCY_FIELDS = [
    "wake_to_recognition_ms",
    "wake_to_cloud_request_ms",
    "wake_to_first_cloud_response_ms",
    "wake_to_tts_start_ms",
    "wake_to_media_start_ms",
    "cloud_request_to_recognition_ms",
    "recognition_to_cloud_request_ms",
    "cloud_request_to_first_cloud_response_ms",
    "cloud_request_to_audio_broadcast_ms",
    "cloud_request_to_speech_reply_ms",
    "cloud_request_to_tts_start_ms",
    "cloud_request_to_media_start_ms",
    "recognition_to_first_cloud_response_ms",
    "recognition_to_audio_broadcast_ms",
    "recognition_to_speech_reply_ms",
    "recognition_to_tts_start_ms",
    "recognition_to_media_start_ms",
    "first_cloud_response_to_tts_start_ms",
    "first_cloud_response_to_media_start_ms",
    "audio_broadcast_to_tts_start_ms",
    "audio_broadcast_to_media_start_ms",
    "tts_start_to_media_start_ms",
    "tts_or_media_play_duration_ms",
]


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_lines(path: Path) -> List[str]:
    # Do not use splitlines(): mojibake may contain C1 NEL (0x85) and would
    # split one JSON cloud response before its media URL.
    return path.read_text(encoding="utf-8", errors="replace").split("\n")


def unique_key(row: Dict[str, Any]) -> str:
    return "|".join(str(row.get(key, "")) for key in ("source_log", "mid", "sessionId", "recordId", "asr_text"))


def flatten(log_path: Path, trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in trace.get("interactions", []) or []:
        if not isinstance(item, dict):
            continue
        wake = item.get("wake", {}) if isinstance(item.get("wake"), dict) else {}
        rec = item.get("recognition", {}) if isinstance(item.get("recognition"), dict) else {}
        req = item.get("cloud_request", {}) if isinstance(item.get("cloud_request"), dict) else {}
        latency = item.get("latency", {}) if isinstance(item.get("latency"), dict) else {}
        row = {
            "source_log": str(log_path),
            "mid": item.get("mid") or rec.get("mid") or req.get("mid") or "",
            "sessionId": item.get("sessionId") or rec.get("sessionId") or req.get("sessionId") or "",
            "recordId": item.get("recordId") or req.get("recordId") or "",
            "wake_time": wake.get("time", ""),
            "wake_word": wake.get("wake_word", ""),
            "wake_pinyin": wake.get("wake_pinyin", ""),
            "asr_time": rec.get("time", ""),
            "asr_text": rec.get("asr_text", ""),
            "asr_pinyin": rec.get("asr_pinyin", ""),
            "asrVendor": rec.get("asrVendor", ""),
            "deviceId": req.get("deviceId", ""),
            "sn": req.get("sn", ""),
            "clientId": req.get("clientId", ""),
            "cloud_topics": "|".join(str(topic) for topic in item.get("cloud_topics", []) or []),
            "media_urls": "|".join(str(url) for url in item.get("media_urls", []) or []),
        }
        for field in LATENCY_FIELDS:
            row[field] = latency.get(field, "")
        has_id = row["mid"] or row["sessionId"] or row["recordId"]
        has_online_evidence = row["recordId"] or row["asr_text"] or row["cloud_topics"]
        if has_id and has_online_evidence:
            rows.append(row)
    return rows


def write_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    fields = [
        "source_log",
        "mid",
        "sessionId",
        "recordId",
        "wake_time",
        "wake_word",
        "wake_pinyin",
        "asr_time",
        "asr_text",
        "asr_pinyin",
        "asrVendor",
        "deviceId",
        "sn",
        "clientId",
        "cloud_topics",
        "media_urls",
        *LATENCY_FIELDS,
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract mid/sessionId/recordId from online ASR logs")
    parser.add_argument("--log", action="append", required=True, help="serial log file; can be repeated")
    parser.add_argument("--out-dir", default="", help="default: debug/request_id_extract/<stamp>")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    out_dir = Path(args.out_dir) if args.out_dir else SCRIPT_DIR.parents[0] / "debug" / "request_id_extract" / stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows: List[Dict[str, Any]] = []
    traces: Dict[str, Any] = {}
    seen = set()
    for raw in args.log:
        path = Path(raw)
        if not path.is_absolute():
            path = (WORKSPACE_ROOT / path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        trace = extract_interaction_trace(read_lines(path))
        traces[str(path)] = {
            "wake_count": len(trace.get("wake_events", []) or []),
            "recognition_count": len(trace.get("recognition_events", []) or []),
            "cloud_request_count": len(trace.get("cloud_requests", []) or []),
            "cloud_response_count": len(trace.get("cloud_responses", []) or []),
            "interaction_count": len(trace.get("interactions", []) or []),
        }
        for row in flatten(path, trace):
            key = unique_key(row)
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(row)
    csv_path = out_dir / "online_request_ids.csv"
    json_path = out_dir / "online_request_ids.json"
    summary_path = out_dir / "summary.json"
    write_csv(csv_path, all_rows)
    write_json(json_path, all_rows)
    write_json(summary_path, {"row_count": len(all_rows), "logs": traces, "csv": str(csv_path), "json": str(json_path)})
    print(json.dumps({"row_count": len(all_rows), "csv": str(csv_path), "json": str(json_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
