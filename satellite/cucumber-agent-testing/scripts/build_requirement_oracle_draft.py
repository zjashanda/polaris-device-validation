# -*- coding: utf-8 -*-
"""Build reviewable oracle drafts from extracted requirement corpus."""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "satellite" / "cucumber-agent-testing"
DEFAULT_CORPUS_ROOT = BASE / "debug" / "requirements_corpus"
DEFAULT_OUTPUT_ROOT = BASE / "debug" / "oracle_drafts"


def latest_child_dir(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    dirs = [p for p in path.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def normalize_record(record: Dict[str, Any], index: int) -> Dict[str, Any]:
    phrase = str(record.get("phrase", "")).strip()
    kind = str(record.get("kind", "unknown")).strip()
    intent = str(record.get("intent", "")).strip()
    action = str(record.get("action", "")).strip()
    response = str(record.get("response", "")).strip()
    semantic = str(record.get("semantic", "")).strip()
    ready_reasons = []
    needs_review = []
    if phrase:
        ready_reasons.append("has_phrase")
    else:
        needs_review.append("missing_phrase")
    if kind in {"command", "free_speech", "online", "wake"}:
        ready_reasons.append("known_kind")
    else:
        needs_review.append("unknown_kind")
    if intent or action or semantic:
        ready_reasons.append("has_intent_or_action")
    else:
        needs_review.append("missing_intent_or_action")
    if kind in {"command", "free_speech", "online"} and not needs_review:
        status = "formal_candidate"
    elif kind == "wake" and phrase:
        status = "precondition_or_wake_candidate"
    else:
        status = "needs_review"
    return {
        "oracle_id": f"oracle.{index:05d}",
        "kind": kind,
        "phrase": phrase,
        "expected_text": record.get("expected_text", phrase),
        "expected_intent": intent,
        "expected_action": action or intent,
        "expected_semantic": semantic,
        "expected_response_hint": response,
        "source_file": str(record.get("source_file", "")).strip(),
        "sheet": str(record.get("sheet", "")).strip(),
        "row": record.get("row", ""),
        "type": record.get("type", ""),
        "status": status,
        "ready_reasons": ";".join(ready_reasons),
        "needs_review": ";".join(needs_review),
        "formal_assertion_policy": "text_or_intent_match" if status == "formal_candidate" else "do_not_formal_fail",
    }


def build_smoke_selection(oracles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    quotas = {"command": 30, "free_speech": 30, "online": 10, "wake": 4}
    counts = {key: 0 for key in quotas}

    def priority(item: Dict[str, Any]) -> Tuple[int, int]:
        sheet = item.get("sheet", "")
        score = 0
        if sheet == "命令词表":
            score += 50
        if sheet == "高频词":
            score += 50
        if item.get("status") == "formal_candidate":
            score += 20
        if item.get("expected_response_hint"):
            score += 10
        return (score, -len(str(item.get("phrase", ""))))

    for item in sorted(oracles, key=priority, reverse=True):
        kind = item.get("kind", "unknown")
        if kind not in quotas or counts[kind] >= quotas[kind]:
            continue
        key = (kind, item.get("phrase", ""))
        if key in seen:
            continue
        seen.add(key)
        counts[kind] += 1
        selected.append({
            **item,
            "smoke_order": len(selected) + 1,
            "smoke_status": "ready_for_runner" if item.get("status") == "formal_candidate" else "review_before_runner"
        })
    return selected


def build_report(oracles: List[Dict[str, Any]], smoke: List[Dict[str, Any]], corpus_dir: Path) -> str:
    counts: Dict[str, int] = {}
    statuses: Dict[str, int] = {}
    for item in oracles:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        statuses[item["status"]] = statuses.get(item["status"], 0) + 1
    lines = [
        "# Requirement Oracle Draft",
        "",
        f"- generated_at: `{_dt.datetime.now().isoformat(timespec='seconds')}`",
        f"- corpus_dir: `{corpus_dir}`",
        f"- oracle_records: `{len(oracles)}`",
        f"- smoke_selection: `{len(smoke)}`",
        "",
        "## Counts By Kind",
        ""
    ]
    for key in sorted(counts):
        lines.append(f"- {key}: {counts[key]}")
    lines.extend(["", "## Counts By Status", ""])
    for key in sorted(statuses):
        lines.append(f"- {key}: {statuses[key]}")
    lines.extend([
        "",
        "## Smoke Selection Preview",
        "",
        "| Order | Kind | Phrase | Intent/Action | Status |",
        "| --- | --- | --- | --- | --- |"
    ])
    for item in smoke[:40]:
        phrase = str(item.get("phrase", "")).replace("|", "/")
        intent = str(item.get("expected_action") or item.get("expected_intent") or "").replace("|", "/")
        lines.append(f"| {item.get('smoke_order')} | {item.get('kind')} | {phrase} | {intent} | {item.get('smoke_status')} |")
    lines.extend([
        "",
        "## Policy",
        "",
        "- `formal_candidate` can be used for formal PASS/FAIL after runner mapping is available.",
        "- `needs_review` can be executed only as exploratory; it cannot produce formal firmware FAIL.",
        "- Free-speech synthetic variants still require semantic acceptance confirmation before formal failure attribution.",
    ])
    return "\n".join(lines)


def build_feature_draft(smoke: List[Dict[str, Any]]) -> str:
    lines = [
        "# Draft generated from requirement oracle smoke selection",
        "@sedimentation @requirements @smoke",
        "Feature: 需求文档语料 smoke",
        "",
        "  Background:",
        "    Given 使用本地 Polaris 串口配置",
        "    And 使用指定声卡播放测试音频",
        "    And 开启串口日志采集",
        ""
    ]
    for item in smoke[:30]:
        tag = item["kind"].replace("_", "")
        phrase = item["phrase"].replace("\n", " ")
        lines.extend([
            f"  @{tag}",
            f"  Scenario: 需求语料 {item['oracle_id']} {phrase}",
            f"    When 执行需求语料 `{item['oracle_id']}`",
            f"    Then 验证需求语料 `{item['oracle_id']}` 的期望结果",
            ""
        ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", default="")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    corpus_dir = Path(args.corpus_dir) if args.corpus_dir else latest_child_dir(DEFAULT_CORPUS_ROOT)
    if not corpus_dir:
        raise SystemExit("No requirements corpus directory found.")
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    records = [r for r in load_json(corpus_dir / "corpus_candidates.json", []) if isinstance(r, dict)]
    records = [r for r in records if r.get("kind") != "error"]
    oracles = [normalize_record(record, index) for index, record in enumerate(records, start=1)]
    smoke = build_smoke_selection(oracles)

    (output_dir / "requirement_oracle_draft.json").write_text(json.dumps(oracles, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "requirement_smoke_selection.json").write_text(json.dumps(smoke, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(output_dir / "requirement_oracle_draft.csv", oracles)
    write_csv(output_dir / "requirement_smoke_selection.csv", smoke)
    (output_dir / "requirement_oracle_report.md").write_text(build_report(oracles, smoke, corpus_dir), encoding="utf-8")
    (output_dir / "requirement_smoke.feature.draft").write_text(build_feature_draft(smoke), encoding="utf-8")
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

