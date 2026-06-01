#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the command-control diagnosis engine.

This is a fast offline check for rules that must not depend on a live DUT:
serial coverage classification, FA2 alias matching, and beep expectation table
classification. It intentionally uses temporary heartbeats and does not open
real serial ports or play audio.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_fa2_beep_expectation_table import build_rows, default_command_file  # noqa: E402
from run_command_control_diagnosis import (  # noqa: E402
    evaluate_serial_coverage,
    expand_expected_values,
    text_matches,
)


def assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(value: Any, label: str) -> None:
    if not value:
        raise AssertionError(label)


def make_session(ports: Dict[str, Dict[str, Any]]) -> Path:
    session = Path(tempfile.mkdtemp(prefix="polaris_cmd_ctrl_selftest_"))
    live = session / "logs" / "live"
    live.mkdir(parents=True, exist_ok=True)
    payload = {"ts": "selftest", "ports": ports}
    (live / "heartbeat.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return session


def test_serial_coverage() -> None:
    env = {"serial": {"ports": {"ap": "COM16", "upper": "COM20", "asr": "COM20", "cp": ""}}}
    full = evaluate_serial_coverage(
        env,
        make_session(
            {
                "COM16": {"role": "cskap", "is_open": True, "last_error": None, "bytes_read": 1, "lines_written": 1},
                "COM20": {"role": "asr", "is_open": True, "last_error": None, "bytes_read": 1, "lines_written": 1},
            }
        ),
        required_roles=[],
        wait_s=0,
    )
    assert_equal(full["status"], "FULL", "all configured ports open")

    degraded = evaluate_serial_coverage(
        env,
        make_session(
            {
                "COM16": {"role": "cskap", "is_open": True, "last_error": None, "bytes_read": 1, "lines_written": 1},
                "COM20": {"role": "asr", "is_open": False, "last_error": "busy", "bytes_read": 0, "lines_written": 0},
            }
        ),
        required_roles=[],
        wait_s=0,
    )
    assert_equal(degraded["status"], "COVERAGE_DEGRADED", "upper missing should degrade when not required")

    blocked_required = evaluate_serial_coverage(
        env,
        make_session(
            {
                "COM16": {"role": "cskap", "is_open": True, "last_error": None, "bytes_read": 1, "lines_written": 1},
                "COM20": {"role": "asr", "is_open": False, "last_error": "busy", "bytes_read": 0, "lines_written": 0},
            }
        ),
        required_roles=["upper"],
        wait_s=0,
    )
    assert_equal(blocked_required["status"], "BLOCKED", "required upper missing should block")

    blocked_all = evaluate_serial_coverage(
        env,
        make_session(
            {
                "COM16": {"role": "cskap", "is_open": False, "last_error": "bad", "bytes_read": 0, "lines_written": 0},
                "COM20": {"role": "asr", "is_open": False, "last_error": "busy", "bytes_read": 0, "lines_written": 0},
            }
        ),
        required_roles=[],
        wait_s=0,
    )
    assert_equal(blocked_all["status"], "BLOCKED", "no log port open should block")


def test_aliases() -> None:
    aliases = expand_expected_values(["打开空调", "关闭模式", "打开节能省电", "风向左吹", "打开左风道自动风"])
    for expected in ["空调开机", "取消模式", "打开eco", "向左吹", "左风道打开自动风"]:
        assert_true(expected in aliases, f"alias missing: {expected}")
    assert_true(text_matches("kong tiao kai ji", expand_expected_values(["打开空调"])), "pinyin/local keyword should match 打开空调")
    assert_true(text_matches("tiao gao yi du", ["调高一度"]), "调 should accept tiao pinyin")


def test_beep_expectation_table() -> None:
    rows = build_rows(default_command_file())
    assert_equal(len(rows), 343, "FA2 command count")
    by_command = {row["command"]: row for row in rows}
    assert_equal(by_command["空调开机"]["beep_expectation"], "expected_if_state_changes", "open AC beep expectation")
    assert_equal(by_command["查询空调联网状态"]["beep_expectation"], "not_required", "network query beep expectation")
    assert_true(all(row["beep_expectation"] in {"expected_if_state_changes", "not_required", "unknown_need_project_rule"} for row in rows), "unexpected beep expectation value")


def main() -> int:
    test_serial_coverage()
    test_aliases()
    test_beep_expectation_table()
    print("selftest=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
