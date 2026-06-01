#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a causal event graph from an existing runtime replay or run dir."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_RULES = BDD_ROOT / "references" / "optimization" / "event_graph_rules.json"
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.event_graph import build_event_graph, render_event_graph_markdown  # noqa: E402
from runtime.events import ValidationEvent  # noqa: E402
from runtime.parsers import parse_artifact_tree  # noqa: E402
from runtime.timeline import Timeline  # noqa: E402


EVENT_FIELDS = set(ValidationEvent.__dataclass_fields__.keys())


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def timeline_from_json(path: Path) -> Timeline:
    payload = load_json(path)
    events: List[ValidationEvent] = []
    for item in payload.get("events", []):
        if isinstance(item, dict):
            events.append(ValidationEvent(**{key: value for key, value in item.items() if key in EVENT_FIELDS}))
    return Timeline.from_events(events)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Polaris runtime event graph")
    parser.add_argument("--input-dir", default="", help="run_dir or artifact directory")
    parser.add_argument("--timeline", default="", help="existing timeline.json")
    parser.add_argument("--rules", default="", help="optional event graph rule overlay JSON")
    parser.add_argument("--no-default-rules", action="store_true", help="do not load references/optimization/event_graph_rules.json")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    if args.timeline:
        timeline = timeline_from_json(Path(args.timeline))
        default_name = Path(args.timeline).resolve().parent.name
    elif args.input_dir:
        input_dir = Path(args.input_dir)
        if not input_dir.is_absolute():
            input_dir = (WORKSPACE_ROOT / input_dir).resolve()
        timeline = Timeline.from_events(parse_artifact_tree(input_dir))
        default_name = input_dir.name
    else:
        raise SystemExit("--input-dir or --timeline is required")

    rules: Dict[str, Any] = {}
    if args.rules:
        rules_path = Path(args.rules)
        if not rules_path.is_absolute():
            rules_path = (WORKSPACE_ROOT / rules_path).resolve()
        rules = load_json(rules_path)
    elif DEFAULT_RULES.exists() and not args.no_default_rules:
        rules = load_json(DEFAULT_RULES)

    graph = build_event_graph(timeline, rule_overlay=rules)
    out_dir = Path(args.out_dir) if args.out_dir else BDD_ROOT / "debug" / "event_graph" / default_name
    if not out_dir.is_absolute():
        out_dir = (WORKSPACE_ROOT / out_dir).resolve()
    write_json(out_dir / "event_graph.json", graph.to_dict())
    (out_dir / "event_graph.md").write_text(render_event_graph_markdown(graph), encoding="utf-8")
    print(out_dir)
    print(f"nodes={len(graph.nodes)} edges={len(graph.edges)} warnings={len(graph.warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
