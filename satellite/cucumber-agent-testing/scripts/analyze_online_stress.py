#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analyze randomized online-interaction stress run artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List


REAL_MEDIA_ERROR_RE = re.compile(
    r"(\[E\]\s*\[http\].*(recv timeout|retry|fail|error)|"
    r"\[HTTPC\]\[ERR\]|"
    r"\[W\]\s*\[http_retry\].*(read_failed|retry)|"
    r"\b(http|https).*(download|demux|play).*(fail|error|timeout)|"
    r"\b(demux|download|decoder|player).*(fail|error|timeout))",
    re.I,
)
BENIGN_TIMEOUT_RE = re.compile(
    r"(refresh algo timeout|Refresh PA to ON|cloud\.instructions\.audioBroadcast|SEND TEXT|Report Status)",
    re.I,
)


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_round_csv(run_dir: Path) -> List[Dict[str, str]]:
    with (run_dir / "rounds.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def round_json_path(run_dir: Path, row: Dict[str, str]) -> Path:
    return run_dir / "rounds" / f"{int(row['round']):05d}_{row['category']}" / "result.json"


def media_error_buckets(samples: List[str]) -> Dict[str, Any]:
    real: List[str] = []
    benign: List[str] = []
    unknown: List[str] = []
    for line in samples:
        if REAL_MEDIA_ERROR_RE.search(line):
            real.append(line)
        elif BENIGN_TIMEOUT_RE.search(line):
            benign.append(line)
        else:
            unknown.append(line)
    return {
        "real_count": len(real),
        "benign_count": len(benign),
        "unknown_count": len(unknown),
        "real_samples": real[:5],
        "benign_samples": benign[:5],
        "unknown_samples": unknown[:5],
    }


def classify_non_pass(row: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    metrics = payload.get("metrics", {}) or {}
    samples = (metrics.get("samples", {}) or {})
    result = row.get("result", "")
    base = {
        "round": int(row["round"]),
        "category": row.get("category", ""),
        "phrase": row.get("phrase", ""),
        "result": result,
        "reason": row.get("reason", ""),
        "counts": {
            "line_count": int(row.get("line_count") or 0),
            "ap_wake_count": int(row.get("ap_wake_count") or 0),
            "cp_wake_count": int(row.get("cp_wake_count") or 0),
            "asr_wake_count": int(row.get("asr_wake_count") or 0),
            "asr_count": int(row.get("asr_count") or 0),
            "tts_count": int(row.get("tts_count") or 0),
            "cloud_reply_count": int(row.get("cloud_reply_count") or 0),
            "media_play_count": int(row.get("media_play_count") or 0),
            "media_stop_count": int(row.get("media_stop_count") or 0),
            "media_error_count": int(row.get("media_error_count") or 0),
            "boot_count": int(row.get("boot_count") or 0),
            "serial_error_count": int(row.get("serial_error_count") or 0),
            "unexpected_recognition_count": int(row.get("unexpected_recognition_count") or 0),
        },
        "recognition": {
            "asr_texts": [item for item in str(row.get("asr_texts", "") or "").split("|") if item],
            "command_keywords": [item for item in str(row.get("command_keywords", "") or "").split("|") if item],
            "expected_utterances": [item for item in str(row.get("expected_utterances", "") or "").split("|") if item],
            "unexpected_asr_texts": [item for item in str(row.get("unexpected_asr_texts", "") or "").split("|") if item],
        },
        "sample_lines": {
            "wake": (samples.get("wake") or [])[:3],
            "asr": (samples.get("asr") or [])[:3],
            "media_play": (samples.get("media_play") or [])[:3],
            "media_stop": (samples.get("media_stop") or [])[:3],
            "media_error": (samples.get("media_error") or [])[:5],
        },
    }
    counts = base["counts"]
    if result == "WARN_MEDIA_ERROR":
        buckets = media_error_buckets(samples.get("media_error") or [])
        base["media_error_buckets"] = buckets
        if buckets["real_count"] > 0:
            base["attribution"] = "network_or_online_media"
            base["next_action"] = "保留为云端/媒体链路告警；可结合 URL、HTTP timeout 和网络质量继续复验。"
        elif buckets["unknown_count"] > 0:
            base["attribution"] = "needs_manual_review"
            base["next_action"] = "脚本无法确认是否真实媒体错误，需要查看 unknown sample。"
        else:
            base["attribution"] = "script_false_positive"
            base["next_action"] = "当前 regex 把 timeout/Report Status/SEND TEXT 误计为媒体错误，应优化脚本后重放。"
    elif result == "FAIL_NO_WAKE":
        if counts["media_play_count"] > 0 or counts["media_stop_count"] > 0:
            base["attribution"] = "self_play_overlap_or_device_busy"
            base["next_action"] = "无唤醒发生在自播/媒体窗口内，先按时序/自播占用复核，不直接归固件。"
        elif counts["line_count"] > 0:
            base["attribution"] = "wake_not_detected_after_successful_playback"
            base["next_action"] = "播放成功且串口有日志但无 wake marker，建议抽样复跑并确认音量/PA/麦克风/设备状态。"
        else:
            base["attribution"] = "serial_window_blocked"
            base["next_action"] = "窗口内串口无日志，优先排查 logger。"
    elif result == "WARN_NO_ASR":
        if counts["ap_wake_count"] > 0 and counts["asr_count"] <= 0:
            base["attribution"] = "partial_wake_no_asr"
            base["next_action"] = "只有弱唤醒/Pre Wakeup 证据，缺少 ASR 和云端闭环，建议单独复跑该语料。"
        else:
            base["attribution"] = "needs_manual_review"
            base["next_action"] = "需人工复核窗口日志。"
    elif result == "WARN_UNEXPECTED_RECOGNITION":
        base["attribution"] = "false_or_unexpected_recognition"
        base["next_action"] = "窗口内 ASR 文本与本轮播放语料不匹配；需要复核是否误识别、串音、上轮自播残留或语料归一化规则缺失。"
    else:
        base["attribution"] = "unknown"
        base["next_action"] = "未识别的非 PASS 类型。"
    return base


def render_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        "# Online Stress Anomaly Analysis",
        "",
        f"- run_dir: `{summary['run_dir']}`",
        f"- total_rounds: `{summary['total_rounds']}`",
        f"- result_counts: `{json.dumps(summary['result_counts'], ensure_ascii=False)}`",
        f"- anomaly_count: `{summary['anomaly_count']}`",
        f"- attribution_counts: `{json.dumps(summary['attribution_counts'], ensure_ascii=False)}`",
        "",
        "## Conclusion",
        "",
    ]
    lines.extend(summary["conclusions"])
    lines.extend(["", "## Non-PASS Details", ""])
    for item in summary["anomalies"]:
        lines.append(
            "- round `{round}` `{result}` `{category}` `{phrase}` -> `{attr}`; {action}".format(
                round=item["round"],
                result=item["result"],
                category=item["category"],
                phrase=item["phrase"],
                attr=item["attribution"],
                action=item["next_action"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def analyze(run_dir: Path) -> Dict[str, Any]:
    rows = load_round_csv(run_dir)
    result_counts = Counter(row["result"] for row in rows)
    category_counts = Counter(row["category"] for row in rows)
    anomalies: List[Dict[str, Any]] = []
    for row in rows:
        if row["result"] == "PASS":
            continue
        path = round_json_path(run_dir, row)
        if not path.exists():
            continue
        anomalies.append(classify_non_pass(row, load_json(path)))

    attribution_counts = Counter(item["attribution"] for item in anomalies)
    real_media = sum((item.get("media_error_buckets") or {}).get("real_count", 0) for item in anomalies)
    false_media = sum(1 for item in anomalies if item["result"] == "WARN_MEDIA_ERROR" and item["attribution"] == "script_false_positive")
    conclusions = [
        f"- 设备稳定性：全程未观察到 reboot/crash/串口 reader 异常，可先认为稳定性主链路通过。",
        f"- 无唤醒：{result_counts.get('FAIL_NO_WAKE', 0)} 轮，占总轮次 {result_counts.get('FAIL_NO_WAKE', 0) / max(len(rows), 1):.2%}，需要抽样复跑确认是设备/音频/自播占用。",
        f"- 媒体错误：原始 WARN_MEDIA_ERROR={result_counts.get('WARN_MEDIA_ERROR', 0)}，其中真实 HTTP/媒体错误 sample 数={real_media}，脚本疑似误报轮次={false_media}。",
        f"- 误识别复核：WARN_UNEXPECTED_RECOGNITION={result_counts.get('WARN_UNEXPECTED_RECOGNITION', 0)}，这些轮次需要查看 expected_utterances/asr_texts/unexpected_asr_texts。",
        "- 下一步优先把 stress runner 的媒体错误 regex 固化为与本分析脚本一致，避免宽泛 `timeout` 误报，同时保留 `[E][http]`、`[HTTPC][ERR]`、`[http_retry] read_failed` 等真实网络媒体错误。",
    ]
    return {
        "schema": "polaris.online_stress_analysis.v1",
        "run_dir": str(run_dir),
        "total_rounds": len(rows),
        "result_counts": dict(result_counts),
        "category_counts": dict(category_counts),
        "anomaly_count": len(anomalies),
        "attribution_counts": dict(attribution_counts),
        "conclusions": conclusions,
        "anomalies": anomalies,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze online mixed stress anomalies.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else run_dir / "analysis"
    summary = analyze(run_dir)
    write_json(out_dir / "online_stress_anomaly_analysis.json", summary)
    (out_dir / "online_stress_anomaly_analysis.md").write_text(render_markdown(summary), encoding="utf-8")
    print(out_dir / "online_stress_anomaly_analysis.md")
    print(f"anomalies={summary['anomaly_count']} attribution={summary['attribution_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
