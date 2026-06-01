# -*- coding: utf-8 -*-
"""Build interrupt prerequisite candidates.

The script prepares a reviewable list of self-play prerequisites for wake or
recognition interruption tests. It does not assume one source is always
available:

1. Online candidates: weather/music/long TTS style queries.
2. Requirement corpus candidates: records with response/TTS hints.
3. FA2 command list candidates: longest command phrases as a fallback.

Actual duration measurement is left to the runner because it requires hardware.
This script produces the candidate plan that the runner can consume later.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "satellite" / "cucumber-agent-testing"
DEFAULT_CORPUS_ROOT = BASE / "debug" / "requirements_corpus"
DEFAULT_OUTPUT_ROOT = BASE / "debug" / "interrupt_prerequisites"
DEFAULT_FA2 = ROOT / "docs" / "fa2命令词.txt"


ONLINE_CANDIDATES = [
    {
        "id": "online.weather.today",
        "type": "online_weather",
        "phrase": "今天天气怎么样",
        "requires_online": True,
        "expected_self_play": "weather_tts",
        "priority": 10
    },
    {
        "id": "online.weather.tomorrow",
        "type": "online_weather",
        "phrase": "明天天气怎么样",
        "requires_online": True,
        "expected_self_play": "weather_tts",
        "priority": 9
    },
    {
        "id": "online.music.play",
        "type": "online_music",
        "phrase": "播放音乐",
        "requires_online": True,
        "expected_self_play": "music_or_tts",
        "priority": 8
    },
    {
        "id": "online.music.song",
        "type": "online_music",
        "phrase": "播放一首歌",
        "requires_online": True,
        "expected_self_play": "music_or_tts",
        "priority": 8
    }
]


def read_text(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def norm(text: Any) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


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


def load_corpus(corpus_dir: Optional[Path]) -> List[Dict[str, Any]]:
    if not corpus_dir:
        return []
    data = load_json(corpus_dir / "corpus_candidates.json", [])
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def load_fa2_commands(path: Path) -> List[str]:
    if not path.exists():
        return []
    commands: List[str] = []
    for line in read_text(path).splitlines():
        line = norm(line)
        if not line or line.startswith("#"):
            continue
        # Keep both raw short lines and table-like first columns usable.
        parts = [norm(p) for p in re.split(r"\t|,|，|\|", line) if norm(p)]
        phrase = parts[0] if parts else line
        if 1 < len(phrase) <= 80:
            commands.append(phrase)
    return list(dict.fromkeys(commands))


def candidate_score(phrase: str, response: str = "", action: str = "") -> int:
    score = len(phrase)
    if response:
        score += min(len(response), 80)
    if any(key in phrase for key in ["状态", "查询", "天气", "说明", "介绍", "帮助", "模式"]):
        score += 15
    if any(key in response for key in ["正在", "已为", "当前", "天气", "模式", "温度"]):
        score += 20
    if any(key in phrase for key in ["开机", "关机", "停止"]):
        score -= 20
    if action:
        score += 5
    return score


def build_requirement_candidates(records: Iterable[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    candidates = []
    for idx, record in enumerate(records, start=1):
        if record.get("kind") == "error":
            continue
        phrase = norm(record.get("phrase"))
        if not phrase:
            continue
        response = norm(record.get("response"))
        action = norm(record.get("action"))
        score = candidate_score(phrase, response, action)
        candidates.append({
            "id": f"requirements.{idx}",
            "type": "requirements_candidate",
            "phrase": phrase,
            "requires_online": record.get("kind") == "online",
            "expected_self_play": "tts_or_action_feedback",
            "source_file": record.get("source_file", ""),
            "sheet": record.get("sheet", ""),
            "row": record.get("row", ""),
            "response_hint": response,
            "action_hint": action,
            "score": score,
            "priority": 5
        })
    candidates.sort(key=lambda item: (item["score"], len(item["phrase"])), reverse=True)
    return candidates[:limit]


def build_fa2_candidates(commands: List[str], limit: int) -> List[Dict[str, Any]]:
    candidates = []
    for idx, phrase in enumerate(commands, start=1):
        score = candidate_score(phrase)
        candidates.append({
            "id": f"fa2.{idx}",
            "type": "offline_command_candidate",
            "phrase": phrase,
            "requires_online": False,
            "expected_self_play": "offline_tts_or_prompt",
            "score": score,
            "priority": 3
        })
    candidates.sort(key=lambda item: (item["score"], len(item["phrase"])), reverse=True)
    return candidates[:limit]


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def build_report(output_dir: Path, candidates: List[Dict[str, Any]], corpus_dir: str, fa2_path: Path) -> str:
    lines = [
        "# Interrupt Prerequisite Discovery",
        "",
        f"- generated_at: `{_dt.datetime.now().isoformat(timespec='seconds')}`",
        f"- corpus_dir: `{corpus_dir}`",
        f"- fa2_commands: `{fa2_path}`",
        f"- candidates: `{len(candidates)}`",
        "",
        "## Recommended Try Order",
        "",
        "1. Try online weather query when the device is online.",
        "2. Try online music playback when weather does not produce a long enough TTS.",
        "3. Try requirement candidates with response hints.",
        "4. Scan offline FA2 candidates and choose the measured longest TTS.",
        "",
        "## Top Candidates",
        "",
        "| Rank | ID | Type | Phrase | Requires Online | Score |",
        "| --- | --- | --- | --- | --- | --- |"
    ]
    for rank, item in enumerate(candidates[:20], start=1):
        lines.append(
            f"| {rank} | {item.get('id','')} | {item.get('type','')} | "
            f"{item.get('phrase','').replace('|', '/')} | {item.get('requires_online')} | {item.get('score', item.get('priority', ''))} |"
        )
    lines.extend([
        "",
        "## Runner Contract",
        "",
        "- A candidate becomes usable only after hardware measurement observes TTS/audio start and end markers.",
        "- If no candidate produces a measurable self-play window, interruption tests are `BLOCKED`, not `FAIL`.",
        "- The chosen prerequisite should include measured duration, injection point, serial evidence, and playback evidence.",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", default="")
    parser.add_argument("--fa2-commands", default=str(DEFAULT_FA2))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    corpus_dir = Path(args.corpus_dir) if args.corpus_dir else latest_child_dir(DEFAULT_CORPUS_ROOT)
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    records = load_corpus(corpus_dir)
    fa2_path = Path(args.fa2_commands)
    fa2_commands = load_fa2_commands(fa2_path)

    candidates: List[Dict[str, Any]] = []
    candidates.extend(ONLINE_CANDIDATES)
    candidates.extend(build_requirement_candidates(records, args.limit))
    candidates.extend(build_fa2_candidates(fa2_commands, args.limit))
    candidates.sort(key=lambda item: (int(item.get("priority", 0)), int(item.get("score", 0))), reverse=True)

    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "corpus_dir": str(corpus_dir) if corpus_dir else "",
        "fa2_commands": str(fa2_path),
        "selection_policy": {
            "minimum_self_play_duration_ms": "user_or_default_required",
            "online_first": True,
            "fallback": "offline_longest_measured_tts",
            "formal_result_policy": "candidate must be measured on hardware before use"
        },
        "candidates": candidates
    }
    (output_dir / "interrupt_prerequisite_candidates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    write_csv(output_dir / "interrupt_prerequisite_candidates.csv", candidates)
    (output_dir / "interrupt_prerequisite_report.md").write_text(
        build_report(output_dir, candidates, str(corpus_dir) if corpus_dir else "", fa2_path),
        encoding="utf-8"
    )
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

