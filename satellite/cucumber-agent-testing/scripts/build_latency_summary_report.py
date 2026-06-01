#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build aggregate latency reports from Polaris online request traces."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]


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


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT.resolve()))
    except Exception:
        return str(path)


def resolve_input(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = (WORKSPACE_ROOT / path).resolve()
    return path


def discover_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(path)
    csv_files = sorted(path.rglob("online_request_ids.csv"))
    csv_dirs = {item.parent.resolve() for item in csv_files}
    json_files = [
        item
        for item in sorted(path.rglob("online_request_ids.json"))
        if item.parent.resolve() not in csv_dirs
    ]
    return csv_files + json_files


def project_from_text(text: str) -> str:
    lower = text.lower()
    if "cskwb01" in lower or "wb01" in lower:
        return "cskwb01"
    if "venusws63" in lower or "ws63" in lower:
        return "venusws63"
    return "unknown"


def as_number(value: Any) -> Optional[float]:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row["_trace_file"] = rel(path)
                row.setdefault("project_id", project_from_text(str(row.get("source_log", "")) + " " + str(path)))
                rows.append(dict(row))
        return rows
    payload = read_json(path)
    if isinstance(payload, dict):
        payload = payload.get("rows") or payload.get("online_request_ids") or payload.get("items") or []
    if not isinstance(payload, list):
        return []
    for item in payload:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["_trace_file"] = rel(path)
        row.setdefault("project_id", project_from_text(str(row.get("source_log", "")) + " " + str(path)))
        rows.append(row)
    return rows


def percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def round_ms(value: Optional[float]) -> Any:
    if value is None:
        return ""
    return int(round(value))


def brief_row(row: Dict[str, Any], field: str) -> Dict[str, Any]:
    return {
        "value_ms": row.get(field, ""),
        "project_id": row.get("project_id", ""),
        "asr_text": row.get("asr_text", ""),
        "asr_pinyin": row.get("asr_pinyin", ""),
        "wake_word": row.get("wake_word", ""),
        "mid": row.get("mid", ""),
        "sessionId": row.get("sessionId", ""),
        "recordId": row.get("recordId", ""),
        "source_log": row.get("source_log", ""),
        "trace_file": row.get("_trace_file", ""),
    }


def summarize_field(rows: List[Dict[str, Any]], field: str, top_n: int) -> Dict[str, Any]:
    valued_rows = [(as_number(row.get(field)), row) for row in rows]
    valued_rows = [(value, row) for value, row in valued_rows if value is not None]
    values = [float(value) for value, _ in valued_rows]
    sorted_rows = sorted(valued_rows, key=lambda item: float(item[0]), reverse=True)
    return {
        "sample_count": len(values),
        "missing_count": max(0, len(rows) - len(values)),
        "min_ms": round_ms(min(values) if values else None),
        "avg_ms": round_ms(sum(values) / len(values) if values else None),
        "p50_ms": round_ms(percentile(values, 0.50)),
        "p90_ms": round_ms(percentile(values, 0.90)),
        "p99_ms": round_ms(percentile(values, 0.99)),
        "max_ms": round_ms(max(values) if values else None),
        "top_slow": [brief_row(row, field) for _, row in sorted_rows[:top_n]],
    }


def group_rows(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        value = str(row.get(key, "") or "unknown")
        grouped.setdefault(value, []).append(row)
    return grouped


def any_latency(row: Dict[str, Any], fields: List[str]) -> bool:
    return any(as_number(row.get(field)) is not None for field in fields)


def build_report(rows: List[Dict[str, Any]], fields: List[str], top_n: int) -> Dict[str, Any]:
    by_project = {}
    for project, project_rows in sorted(group_rows(rows, "project_id").items()):
        by_project[project] = {
            "row_count": len(project_rows),
            "fields": {field: summarize_field(project_rows, field, top_n) for field in fields},
        }
    evidence_gap = {
        "rows_without_any_latency": sum(1 for row in rows if not any_latency(row, fields)),
        "rows_without_asr_text": sum(1 for row in rows if not str(row.get("asr_text", "")).strip()),
        "rows_without_mid": sum(1 for row in rows if not str(row.get("mid", "")).strip()),
        "rows_without_record_id": sum(1 for row in rows if not str(row.get("recordId", "")).strip()),
    }
    return {
        "schema": "polaris.latency_summary_report.v1",
        "generated_at": now_iso(),
        "row_count": len(rows),
        "fields": fields,
        "evidence_gap": evidence_gap,
        "overall": {field: summarize_field(rows, field, top_n) for field in fields},
        "by_project": by_project,
    }


def render_field_table(title: str, fields_payload: Dict[str, Any], fields: List[str]) -> List[str]:
    lines = [f"## {title}", "", "| Field | N | Missing | P50 | P90 | P99 | Max |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for field in fields:
        item = fields_payload.get(field, {})
        lines.append(
            "| {field} | {n} | {missing} | {p50} | {p90} | {p99} | {maxv} |".format(
                field=field,
                n=item.get("sample_count", 0),
                missing=item.get("missing_count", 0),
                p50=item.get("p50_ms", ""),
                p90=item.get("p90_ms", ""),
                p99=item.get("p99_ms", ""),
                maxv=item.get("max_ms", ""),
            )
        )
    return lines + [""]


def render_markdown(report: Dict[str, Any], top_n: int) -> str:
    lines = [
        "# Polaris Interaction Latency Summary",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Rows: `{report.get('row_count')}`",
        f"- Evidence gap: `{json.dumps(report.get('evidence_gap', {}), ensure_ascii=False)}`",
        "",
    ]
    fields = list(report.get("fields", []))
    lines.extend(render_field_table("Overall", report.get("overall", {}), fields))
    for project, payload in sorted((report.get("by_project", {}) or {}).items()):
        lines.extend(render_field_table(f"Project: {project}", payload.get("fields", {}), fields))
    lines.extend(["## Top Slow Samples", ""])
    for field in fields:
        top = (report.get("overall", {}).get(field, {}) or {}).get("top_slow", [])[:top_n]
        if not top:
            continue
        lines.extend([f"### {field}", "", "| ms | project | asr_text | pinyin | mid | trace |", "| ---: | --- | --- | --- | --- | --- |"])
        for item in top:
            lines.append(
                "| {value} | {project} | {asr} | {pinyin} | {mid} | {trace} |".format(
                    value=item.get("value_ms", ""),
                    project=item.get("project_id", ""),
                    asr=str(item.get("asr_text", "")).replace("|", "/"),
                    pinyin=str(item.get("asr_pinyin", "")).replace("|", "/"),
                    mid=item.get("mid", ""),
                    trace=item.get("trace_file", ""),
                )
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build aggregate latency summary from online_request_ids traces.")
    parser.add_argument("--input", action="append", required=True, help="online_request_ids csv/json or directory; repeatable")
    parser.add_argument("--out-dir", default="", help="default: debug/latency_summary/<stamp>")
    parser.add_argument("--field", action="append", default=[], help="latency field to summarize; repeatable")
    parser.add_argument("--top-n", type=int, default=10)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    fields = args.field or LATENCY_FIELDS
    out_dir = Path(args.out_dir) if args.out_dir else BDD_ROOT / "debug" / "latency_summary" / now_stamp()
    if not out_dir.is_absolute():
        out_dir = (WORKSPACE_ROOT / out_dir).resolve()
    files: List[Path] = []
    for raw in args.input:
        files.extend(discover_files(resolve_input(raw)))
    rows: List[Dict[str, Any]] = []
    seen = set()
    for path in files:
        for row in load_rows(path):
            key = (
                row.get("source_log", ""),
                row.get("mid", ""),
                row.get("sessionId", ""),
                row.get("recordId", ""),
                row.get("asr_text", ""),
                row.get("_trace_file", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    report = build_report(rows, fields, max(0, args.top_n))
    report["inputs"] = [rel(path) for path in files]
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "latency_summary_report.json", report)
    (out_dir / "latency_summary_report.md").write_text(render_markdown(report, max(0, args.top_n)), encoding="utf-8")
    print(json.dumps({"row_count": report["row_count"], "out_dir": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
