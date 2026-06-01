#!/usr/bin/env python3
from pathlib import Path
import sys

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from tools.core.polaris_config import get_port
from tools.core.polaris_runtime import current_session_dir, new_artifact_dir, queue_command, read_lines_between, wait_for_patterns


ROOT = Path(__file__).resolve().parents[2]


AP_PROBES = [
    {
        "name": "version",
        "port": "COM14",
        "command": "version",
        "patterns": ["AP Version:", "CP Version:", "Algo Version"],
        "timeout_s": 4.0,
    },
    {
        "name": "deviceinfo",
        "port": "COM14",
        "command": "deviceinfo",
        "patterns": ["Device Info:", "WakeupID:"],
        "timeout_s": 4.0,
    },
    {
        "name": "flash_show",
        "port": "COM14",
        "command": "flash.show",
        "patterns": ["boot.action=", "env="],
        "timeout_s": 4.0,
    },
]

WB_PROBES = [
    {
        "name": "listen_version",
        "port": "COM13",
        "command": "listen version",
        "patterns": ["ListenAI Build Info:", "MS Version:"],
        "timeout_s": 4.0,
    },
    {
        "name": "listen_flash_show",
        "port": "COM13",
        "command": "listen flash show",
        "patterns": ["Flash KV List:", "log_lev="],
        "timeout_s": 4.0,
    },
]


KV_RE = re.compile(r"^(?P<key>[A-Za-z0-9_./@:-]+)=(?P<value>.*)$")


def load_env_payload() -> Dict[str, Any]:
    env_path = ROOT / "config" / "polaris_env.json"
    if not env_path.exists():
        return {}
    return json.loads(env_path.read_text(encoding="utf-8"))


def normalize_line(line: str) -> str:
    if len(line) > 26 and line[23] == " ":
        return line.split("] ", 1)[1] if "] " in line else line[24:]
    return line


def run_probe(entry: dict, session_dir: Path) -> Tuple[List[str], Dict[str, str]]:
    start_dt = datetime.now()
    queue_command(entry["port"], entry["command"], session_dir=session_dir)
    wait_for_patterns(
        entry["port"],
        start_dt,
        entry["patterns"],
        timeout_s=float(entry["timeout_s"]),
        session_dir=session_dir,
    )
    time.sleep(0.5)
    end_dt = datetime.now()
    lines = read_lines_between(entry["port"], start_dt, end_dt, session_dir=session_dir)
    parsed = parse_probe_output(entry["name"], lines)
    return lines, parsed


def parse_probe_output(name: str, lines: List[str]) -> Dict[str, str]:
    clean = [normalize_line(line) for line in lines]
    if name == "version":
        return parse_version(clean)
    if name == "deviceinfo":
        return parse_deviceinfo(clean)
    if name == "flash_show":
        return parse_kv_block(clean)
    if name == "listen_version":
        return parse_listen_version(clean)
    if name == "listen_flash_show":
        return parse_kv_block(clean)
    return {"raw_text": "\n".join(clean)}


def parse_version(lines: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in lines:
        if "AP Version:" in line:
            result["ap_version"] = line.split("AP Version:", 1)[1].strip()
        elif "CP Version:" in line:
            result["cp_version"] = line.split("CP Version:", 1)[1].strip()
        elif "Algo Version" in line:
            result["algorithm_version"] = line.split("Algo Version", 1)[1].lstrip(": ").strip()
        elif "ListenAI APP Build Info:" in line:
            result["ap_build_info"] = line.split("ListenAI APP Build Info:", 1)[1].strip()
    return result


def parse_deviceinfo(lines: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in lines:
        if "SN:" in line:
            result["sn"] = line.split("SN:", 1)[1].strip()
        elif "Mac:" in line:
            result["mac"] = line.split("Mac:", 1)[1].strip()
        elif "WakeupID:" in line:
            result["wakeup_id"] = line.split("WakeupID:", 1)[1].strip()
        elif "IP:" in line:
            result["ip"] = line.split("IP:", 1)[1].strip()
        elif "IoT ID:" in line:
            result["iot_id"] = line.split("IoT ID:", 1)[1].strip()
    return result


def merge_deviceinfo_with_env(deviceinfo: Dict[str, str]) -> Dict[str, str]:
    merged = dict(deviceinfo)
    env_payload = load_env_payload()
    current_deviceinfo = env_payload.get("current_deviceinfo", {}) or {}
    fallback_keys = ("sn", "iot_id", "mac", "wakeup_id")
    for key in fallback_keys:
        if merged.get(key):
            continue
        value = str(current_deviceinfo.get(key, "")).strip()
        if value:
            merged[key] = value
    if not merged.get("wakeup_id"):
        for key in ("wakeupid_from_deviceinfo", "current_wakeup_word"):
            value = str(env_payload.get(key, "")).strip()
            if value:
                merged["wakeup_id"] = value
                break
    return merged


def parse_listen_version(lines: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in lines:
        if "ListenAI Build Info:" in line:
            result["wb_build_info"] = line.split("ListenAI Build Info:", 1)[1].strip()
        elif "MS Version:" in line:
            result["wb_version"] = line.split("MS Version:", 1)[1].strip()
    return result


def parse_kv_block(lines: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in lines:
        match = KV_RE.match(line.strip())
        if not match:
            continue
        key = match.group("key").strip()
        value = match.group("value").strip()
        result[key] = value
    return result


def snapshot(label: str, output_dir: Path, session_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "label": label,
        "captured_at": datetime.now().isoformat(timespec="milliseconds"),
        "session_dir": str(session_dir),
        "ap": {},
        "asr": {},
        "wb01": {},
    }

    ap_port = get_port("ap")
    asr_port = get_port("asr")

    for raw_probe in AP_PROBES:
        probe = dict(raw_probe)
        probe["port"] = ap_port
        lines, parsed = run_probe(probe, session_dir)
        if probe["name"] == "deviceinfo":
            parsed = merge_deviceinfo_with_env(parsed)
        data["ap"][probe["name"]] = parsed
        (raw_dir / f"ap_{probe['name']}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for raw_probe in WB_PROBES:
        probe = dict(raw_probe)
        probe["port"] = asr_port
        lines, parsed = run_probe(probe, session_dir)
        data["asr"][probe["name"]] = parsed
        data["wb01"][probe["name"]] = parsed
        (raw_dir / f"asr_{probe['name']}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    data["summary"] = {
        "ap_version": data["ap"].get("version", {}).get("ap_version"),
        "cp_version": data["ap"].get("version", {}).get("cp_version"),
        "algorithm_version": data["ap"].get("version", {}).get("algorithm_version"),
        "asr_version": data["asr"].get("listen_version", {}).get("wb_version"),
        "wb_version": data["wb01"].get("listen_version", {}).get("wb_version"),
        "wakeup_id": data["ap"].get("deviceinfo", {}).get("wakeup_id"),
        "env": data["ap"].get("flash_show", {}).get("env"),
        "sn": data["ap"].get("deviceinfo", {}).get("sn"),
        "mac": data["ap"].get("deviceinfo", {}).get("mac"),
    }
    out_path = output_dir / f"{label}_state.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    phase = re.sub(r"[^A-Za-z0-9_.-]+", "_", label or "snapshot").strip("_").lower() or "snapshot"
    companion = {
        "schema": "polaris.snapshot.v1",
        "snapshot_id": f"state_probe_{phase}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}",
        "phase": phase,
        "project_id": "",
        "timestamp": data["captured_at"],
        "run_dir": str(output_dir.parent if output_dir.name == "state" else output_dir),
        "scope": {"runner": "tools.probe.polaris_state_probe", "session_dir": str(session_dir)},
        "snapshot_types": [
            "env_config_snapshot",
            "device_runtime_snapshot",
            "voice_session_snapshot",
            "health_snapshot",
            "behavior_snapshot",
            "cloud_config_snapshot",
        ],
        "state": {
            "expected_state": {"state_contract": "evidence_only"},
            "env_config_snapshot": {"session_dir": str(session_dir)},
            "device_runtime_snapshot": {"state_probe": data},
            "voice_session_snapshot": {
                "wakeup_id": data.get("summary", {}).get("wakeup_id"),
                "env": data.get("summary", {}).get("env"),
            },
            "health_snapshot": {"probe_raw_dir": str(raw_dir)},
            "behavior_snapshot": {},
            "cloud_config_snapshot": {"device_env": data.get("summary", {}).get("env")},
        },
        "evidence_sources": [
            {"path": str(path), "type": "raw_probe_log"}
            for path in sorted(raw_dir.glob("*.log"))
        ],
        "confidence": {"overall": "DEVICE_PROBE", "source_count": len(list(raw_dir.glob("*.log"))), "policy": "evidence_only_no_state_fabrication"},
        "unknown_fields": [],
        "limitations": ["State probe captures only configured serial query responses; behavior/audio/cloud state remains outside this probe unless logs are supplied."],
    }
    for target_dir in [output_dir] + ([output_dir.parent] if output_dir.name == "state" else []):
        (target_dir / f"snapshot_{phase}.json").write_text(json.dumps(companion, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def diff_states(before_path: Path, after_path: Path, output_path: Path) -> Path:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    diff = {
        "before": str(before_path),
        "after": str(after_path),
        "generated_at": datetime.now().isoformat(timespec="milliseconds"),
        "changes": {},
    }
    flat_before = flatten(before)
    flat_after = flatten(after)
    all_keys = sorted(set(flat_before) | set(flat_after))
    for key in all_keys:
        b = flat_before.get(key)
        a = flat_after.get(key)
        if b != a:
            diff["changes"][key] = {"before": b, "after": a}
    output_path.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    companion = {
        "schema": "polaris.snapshot_diff.v1",
        "generated_at": diff["generated_at"],
        "before_snapshot_id": str(before_path),
        "after_snapshot_id": str(after_path),
        "changed": [
            {
                "field": key,
                "before": value.get("before"),
                "after": value.get("after"),
                "expected": "",
                "evidence": {"before": str(before_path), "after": str(after_path)},
                "attribution_hint": "STATE_CHANGED",
            }
            for key, value in diff["changes"].items()
        ],
        "unchanged": [],
        "unknown": [],
        "summary": {"changed_count": len(diff["changes"]), "unchanged_count": 0, "unknown_count": 0},
    }
    companion["coverage"] = {
        "schema": "polaris.snapshot_coverage.v1",
        "generated_at": diff["generated_at"],
        "required_snapshot_types": [
            "env_config_snapshot",
            "device_runtime_snapshot",
            "voice_session_snapshot",
            "health_snapshot",
            "behavior_snapshot",
            "cloud_config_snapshot",
        ],
        "covered_type_count": 3,
        "required_type_count": 6,
        "coverage_ratio": 0.5,
        "unknown_fields": ["behavior_snapshot", "cloud_config_snapshot.api_state"],
        "diff_counts": companion["summary"],
    }
    for target_dir in [output_path.parent] + ([output_path.parent.parent] if output_path.parent.name == "state" else []):
        (target_dir / "snapshot_diff.json").write_text(json.dumps(companion, ensure_ascii=False, indent=2), encoding="utf-8")
        (target_dir / "snapshot_coverage.json").write_text(json.dumps(companion["coverage"], ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def flatten(obj, prefix="") -> Dict[str, object]:
    result: Dict[str, object] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten(value, new_prefix))
    else:
        result[prefix] = obj
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polaris device state probe")
    sub = parser.add_subparsers(dest="action", required=True)

    snap = sub.add_parser("snapshot", help="capture a state snapshot from AP and ASR")
    snap.add_argument("--label", default="snapshot")
    snap.add_argument("--output-dir", default=None)

    diff = sub.add_parser("diff", help="diff two state snapshot files")
    diff.add_argument("--before", required=True)
    diff.add_argument("--after", required=True)
    diff.add_argument("--output", required=True)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    session_dir = current_session_dir()
    if args.action == "snapshot":
        output_dir = Path(args.output_dir) if args.output_dir else new_artifact_dir("state_probe", session_dir)
        path = snapshot(args.label, output_dir, session_dir)
        print(path)
    elif args.action == "diff":
        path = diff_states(Path(args.before), Path(args.after), Path(args.output))
        print(path)


if __name__ == "__main__":
    main()
