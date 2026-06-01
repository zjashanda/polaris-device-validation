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
from typing import Dict, List, Optional

import serial

from tools.core.polaris_config import configured_log_ports, get_baudrate, get_port, resolve_port
from tools.core.polaris_runtime import current_session_dir, ensure_dir, new_artifact_dir, read_lines_between


DEFAULT_CONTROL_PORT = "COM15"
DEFAULT_BAUDRATE = get_baudrate()

COMMANDS = {
    "asr-on": "uut-reset.on",
    "asr-off": "uut-reset.off",
    "wb01-on": "uut-reset.on",
    "wb01-off": "uut-reset.off",
    "csk-on": "uut-csk-reset.on",
    "csk-off": "uut-csk-reset.off",
}

TARGET_CYCLE = {
    "asr": ("asr-on", "asr-off"),
    "wb01": ("wb01-on", "wb01-off"),
    "csk": ("csk-on", "csk-off"),
}

REASON_RE = re.compile(r"(reason|reboot|reset|boot|power)", re.I)
BOOT_RE = re.compile(
    r"(Appliance boot up success\.|bootloader|Running Config|boot\.action=boot_image|reboot_reason|shell_cmd)",
    re.I,
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def resolve_output_dir(value: Optional[str], prefix: str, session_dir: Optional[Path]) -> Path:
    if value:
        path = Path(value)
        return ensure_dir(path if path.is_absolute() else Path.cwd() / path)
    if session_dir is not None:
        return new_artifact_dir(prefix, session_dir=session_dir)
    return ensure_dir(Path.cwd() / prefix)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def capture_after_write(ser: serial.Serial, duration_s: float = 0.35) -> List[str]:
    deadline = time.time() + duration_s
    chunks: List[str] = []
    while time.time() < deadline:
        waiting = ser.in_waiting or 0
        if waiting:
            chunks.append(ser.read(waiting).decode("utf-8", errors="replace"))
        else:
            time.sleep(0.02)
    text = "".join(chunks).replace("\r", "")
    return [line for line in text.split("\n") if line]


def send_control_command(
    command: str,
    port: str,
    baudrate: int,
    output_dir: Path,
) -> Dict[str, object]:
    log_path = output_dir / f"{port}.log"
    with serial.Serial(port, baudrate, timeout=0.2) as ser, log_path.open(
        "a", encoding="utf-8", newline=""
    ) as handle:
        sent_at = now_iso()
        wire = (command.rstrip("\r\n") + "\r\n").encode("utf-8", errors="ignore")
        ser.write(wire)
        ser.flush()
        handle.write(f"{sent_at} [{port}/control] [COMMAND] {command}\n")
        handle.flush()
        echoed = capture_after_write(ser)
        for line in echoed:
            handle.write(f"{now_iso()} [{port}/control] {line}\n")
        handle.flush()
    return {
        "sent_at": sent_at,
        "port": port,
        "baudrate": baudrate,
        "command": command,
        "echo_lines": echoed,
    }


def collect_window_logs(session_dir: Path, start_dt: datetime, end_dt: datetime, output_dir: Path) -> Dict[str, dict]:
    window_dir = ensure_dir(output_dir / "window_logs")
    summary: Dict[str, dict] = {}
    for port in configured_log_ports():
        lines = read_lines_between(port, start_dt, end_dt, session_dir=session_dir)
        log_path = window_dir / f"{port}.log"
        log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        reason_lines = [line for line in lines if REASON_RE.search(line)]
        boot_lines = [line for line in lines if BOOT_RE.search(line)]
        summary[port] = {
            "line_count": len(lines),
            "first_line": lines[0] if lines else None,
            "last_line": lines[-1] if lines else None,
            "reason_lines": reason_lines[:20],
            "boot_lines": boot_lines[:20],
            "log_path": str(log_path),
        }
    for role, canonical_port in (("cp", "COM12"), ("asr", "COM13"), ("ap", "COM14")):
        configured_port = get_port(role)
        if canonical_port not in summary and configured_port in summary:
            alias = dict(summary[configured_port])
            alias["alias_for"] = configured_port
            summary[canonical_port] = alias
    return summary


def infer_cycle(target: str, marker_summary: Dict[str, dict]) -> Dict[str, object]:
    com12 = marker_summary["COM12"]
    com13 = marker_summary["COM13"]
    com14 = marker_summary["COM14"]

    asr_booted = bool(com13["boot_lines"])
    ap_booted = bool(com14["boot_lines"])
    cp_booted = bool(com12["line_count"] or com12["boot_lines"])
    soft_marker = any("shell_cmd" in line.lower() for line in com14["boot_lines"]) or any(
        "reboot_reason:0x3" in line.lower() for line in com13["reason_lines"]
    )

    notes: List[str] = []
    if target in {"asr", "wb01"}:
        label = "ASR" if target == "asr" else "WB01"
        if asr_booted and ap_booted and not soft_marker:
            notes.append(f"{label} power cycle restarted both {label} and CSK/AP, and no soft reboot marker was seen.")
        if soft_marker:
            notes.append("Soft reboot markers were seen in the same window; review the raw lines before treating this as a pure hard reset.")
    if target == "csk":
        if ap_booted and not asr_booted:
            notes.append("CSK power cycle restarted CSK/AP only, while ASR stayed up.")
        if asr_booted:
            notes.append("ASR also showed boot markers in the same window; review whether another reset overlapped.")

    return {
        "target": target,
        "asr_booted": asr_booted,
        "wb01_booted": asr_booted,
        "ap_booted": ap_booted,
        "cp_activity_seen": cp_booted,
        "soft_reboot_markers_present": soft_marker,
        "hard_power_cycle_likely": (asr_booted or ap_booted) and not soft_marker,
        "notes": notes,
    }


def action_send(args: argparse.Namespace) -> int:
    session_dir = None
    try:
        session_dir = current_session_dir()
    except Exception:
        session_dir = None
    output_dir = resolve_output_dir(args.output_dir, "power_control_send", session_dir)
    command = COMMANDS.get(args.command, args.command)
    port = resolve_port("control", args.port, source="polaris_power_control.send")
    baudrate = args.baudrate if args.baudrate is not None else get_baudrate()
    # Control UART baud is independent from DUT log UART baud.  Do not sync
    # an explicit control baud into the global data-log baudrate, otherwise a
    # PA/power precondition can silently break the next AP/ASR logger session.
    payload = send_control_command(command, port, baudrate, output_dir)
    payload["session_dir"] = str(session_dir) if session_dir else None
    write_json(output_dir / "summary.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def action_cycle(args: argparse.Namespace) -> int:
    session_dir = current_session_dir()
    output_dir = resolve_output_dir(args.output_dir, f"power_cycle_{args.target}", session_dir)
    assert_key, release_key = TARGET_CYCLE[args.target]
    port = resolve_port("control", args.port, source="polaris_power_control.cycle")
    baudrate = args.baudrate if args.baudrate is not None else get_baudrate()
    # See action_send: control baud must not overwrite the data-log baudrate.

    start_dt = datetime.now()
    actions = [
        send_control_command(COMMANDS[assert_key], port, baudrate, output_dir),
    ]
    time.sleep(args.off_wait)
    actions.append(send_control_command(COMMANDS[release_key], port, baudrate, output_dir))
    time.sleep(args.observe)
    end_dt = datetime.now()

    marker_summary = collect_window_logs(session_dir, start_dt, end_dt, output_dir)
    inference = infer_cycle(args.target, marker_summary)
    payload = {
        "target": args.target,
        "control_port": port,
        "baudrate": baudrate,
        "session_dir": str(session_dir),
        "output_dir": str(output_dir),
        "start_at": start_dt.isoformat(timespec="milliseconds"),
        "end_at": end_dt.isoformat(timespec="milliseconds"),
        "off_wait_s": args.off_wait,
        "observe_s": args.observe,
        "actions": actions,
        "markers": marker_summary,
        "inference": inference,
    }
    write_json(output_dir / "summary.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="COM15 power-control helper for Polaris DUT")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send", help="send one raw or named COM15 control command")
    send.add_argument("--command", required=True, help="raw command or one of: asr-on, asr-off, wb01-on, wb01-off, csk-on, csk-off")
    send.add_argument("--port", default=None, help="default: configured control port")
    send.add_argument("--baudrate", type=int, default=None, help="default: configured baudrate")
    send.add_argument("--output-dir", default=None)
    send.set_defaults(func=action_send)

    cycle = sub.add_parser("cycle", help="power cycle asr/wb01 or csk and capture serial evidence")
    cycle.add_argument("--target", choices=sorted(TARGET_CYCLE), required=True)
    cycle.add_argument("--port", default=None, help="default: configured control port")
    cycle.add_argument("--baudrate", type=int, default=None, help="default: configured baudrate")
    cycle.add_argument("--off-wait", type=float, default=2.0)
    cycle.add_argument("--observe", type=float, default=20.0)
    cycle.add_argument("--output-dir", default=None)
    cycle.set_defaults(func=action_cycle)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
