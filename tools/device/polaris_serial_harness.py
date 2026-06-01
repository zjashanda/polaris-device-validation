#!/usr/bin/env python3
from pathlib import Path
import sys

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

import argparse
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import serial

from tools.core.polaris_config import (
    get_baudrate,
    get_port,
    resolve_port,
    serial_logger_ports,
    set_baudrate,
)
from tools.core.polaris_runtime import (
    events_path,
    heartbeat_path,
    manifest_path,
    merged_log_path,
    pid_path,
    queue_path,
    runtime_log_path,
    session_live_file,
)


DEFAULT_PORTS = serial_logger_ports()
DEFAULT_BAUDRATE = get_baudrate()
QUEUE_NAME = "control.jsonl"
HEARTBEAT_NAME = "heartbeat.json"
MANIFEST_NAME = "session_manifest.json"
PID_NAME = "logger.pid"
STOP_NAME = "stop.flag"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def smart_decode(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def sanitize_line(text: str) -> str:
    return text.replace("\r", "").replace("\x00", "")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


EVENT_PATTERNS = {
    "wakeup": [
        re.compile(r"唤醒|wakeup|wake\s*up|awake", re.I),
    ],
    "asr_result": [
        re.compile(r"识别|asr|命令词|离线结果|result|nlp|intent|拼音", re.I),
    ],
    "tone_id": [
        re.compile(r"(?:tone|播报|player)[^0-9]{0,16}(?:id)?[^0-9]{0,8}(\d+)", re.I),
    ],
    "tone_start": [
        re.compile(r"开始播报|播报开始|play.*(?:start|begin)|tts.*(?:start|begin)", re.I),
    ],
    "tone_end": [
        re.compile(r"结束播报|播报结束|play.*(?:end|stop|finish|done)|tts.*(?:end|stop|finish|done)", re.I),
    ],
}


@dataclass
class PortState:
    port: str
    role: str
    writable: bool
    log_path: Path
    handle: object = field(init=False)
    serial_obj: Optional[serial.Serial] = None
    bytes_read: int = 0
    lines_written: int = 0
    last_activity: Optional[str] = None
    last_error: Optional[str] = None
    partial: str = ""

    def __post_init__(self) -> None:
        self.handle = self.log_path.open("a", encoding="utf-8", newline="")

    def close(self) -> None:
        try:
            if self.serial_obj and self.serial_obj.is_open:
                self.serial_obj.close()
        finally:
            self.handle.close()


def build_logger_port_map(*, ap_port: str = "", cp_port: str = "", asr_port: str = "") -> dict[str, dict[str, Any]]:
    if not any((ap_port, cp_port, asr_port)):
        return serial_logger_ports()
    result: dict[str, dict[str, Any]] = {}
    cp = str(cp_port or "").strip().upper()
    asr = str(asr_port or "").strip().upper()
    ap = str(ap_port or "").strip().upper()
    if cp:
        result[cp] = {"role": "cskcp", "writable": False}
    if asr:
        result[asr] = {"role": "asr", "writable": True}
    if ap:
        result[ap] = {"role": "cskap", "writable": True}
    return result


class SessionLogger:
    def __init__(self, session_dir: Path, baudrate: int, port_map: Optional[dict[str, dict[str, Any]]] = None) -> None:
        self.session_dir = session_dir
        self.baudrate = baudrate
        self.control_path = queue_path(session_dir, create=True)
        self.heartbeat_path = heartbeat_path(session_dir, create=True)
        self.manifest_path = manifest_path(session_dir, create=True)
        self.pid_path = pid_path(session_dir, create=True)
        self.stop_path = session_live_file(STOP_NAME, session_dir=session_dir, create=True)
        self.merged_path = merged_log_path(session_dir, create=True)
        self.events_path = events_path(session_dir, create=True)
        self.runtime_path = runtime_log_path(session_dir, create=True)
        self._merged_handle = self.merged_path.open("a", encoding="utf-8", newline="")
        self._events_handle = self.events_path.open("a", encoding="utf-8", newline="")
        self._runtime_handle = self.runtime_path.open("a", encoding="utf-8", newline="")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._queue_offset = 0
        self._queue_last_seen_ts: Optional[str] = None
        self._queue_last_payload: Optional[dict] = None
        self._queue_last_result: Optional[str] = None
        self._queue_last_error: Optional[str] = None
        self.port_map = port_map or serial_logger_ports()
        self.ports: Dict[str, PortState] = {}
        for port, meta in self.port_map.items():
            self.ports[port] = PortState(
                port=port,
                role=meta["role"],
                writable=meta["writable"],
                log_path=session_live_file(f"{port}.log", session_dir=session_dir, create=True),
            )

    def _runtime(self, message: str) -> None:
        line = f"{now_iso()} [runtime] {message}\n"
        with self._lock:
            self._runtime_handle.write(line)
            self._runtime_handle.flush()

    def _write_line(self, state: PortState, line: str) -> None:
        ts = now_iso()
        clean = sanitize_line(line)
        if not clean:
            return
        if not clean.endswith("\n"):
            clean = clean + "\n"
        prefixed = f"{ts} [{state.port}/{state.role}] {clean}"
        with self._lock:
            state.handle.write(prefixed)
            state.handle.flush()
            self._merged_handle.write(prefixed)
            self._merged_handle.flush()
        state.lines_written += 1
        state.last_activity = ts
        self._match_events(state, clean.rstrip("\n"))

    def _emit_event(self, state: PortState, event_type: str, line: str, extra: Optional[dict] = None) -> None:
        payload = {
            "ts": now_iso(),
            "port": state.port,
            "role": state.role,
            "event_type": event_type,
            "line": line,
        }
        if extra:
            payload.update(extra)
        with self._lock:
            self._events_handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._events_handle.flush()

    def _match_events(self, state: PortState, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        for event_type, patterns in EVENT_PATTERNS.items():
            for pattern in patterns:
                match = pattern.search(stripped)
                if not match:
                    continue
                extra = {}
                if event_type == "tone_id" and match.groups():
                    extra["tone_id"] = match.group(1)
                self._emit_event(state, event_type, stripped, extra)
                break

    def _open_port(self, state: PortState) -> None:
        try:
            ser = serial.Serial(state.port, self.baudrate, timeout=0.2, write_timeout=1.0)
            state.serial_obj = ser
            self._runtime(f"opened {state.port} ({state.role}) @ {self.baudrate}")
        except Exception as exc:  # pragma: no cover
            state.last_error = str(exc)
            self._runtime(f"failed to open {state.port}: {exc}")
            raise

    def _reader_loop(self, state: PortState) -> None:
        try:
            self._open_port(state)
        except Exception:
            return
        ser = state.serial_obj
        while not self._stop.is_set():
            try:
                chunk = ser.read(ser.in_waiting or 1)
            except Exception as exc:
                state.last_error = str(exc)
                self._runtime(f"read error on {state.port}: {exc}")
                self._stop.set()
                break
            if not chunk:
                continue
            state.bytes_read += len(chunk)
            text = smart_decode(chunk)
            state.partial += text
            while True:
                newline_index = state.partial.find("\n")
                if newline_index == -1:
                    break
                line = state.partial[: newline_index + 1]
                state.partial = state.partial[newline_index + 1 :]
                self._write_line(state, line)
        if state.partial:
            self._write_line(state, state.partial)
            state.partial = ""

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            queue_size = self.control_path.stat().st_size if self.control_path.exists() else 0
            payload = {
                "ts": now_iso(),
                "session_dir": str(self.session_dir),
                "logger_pid": os.getpid(),
                "baudrate": self.baudrate,
                "ports": {
                    port: {
                        "role": state.role,
                        "writable": state.writable,
                        "bytes_read": state.bytes_read,
                        "lines_written": state.lines_written,
                        "last_activity": state.last_activity,
                        "last_error": state.last_error,
                        "is_open": bool(state.serial_obj and state.serial_obj.is_open),
                    }
                    for port, state in self.ports.items()
                },
                "queue": {
                    "offset": self._queue_offset,
                    "size": queue_size,
                    "last_seen_ts": self._queue_last_seen_ts,
                    "last_payload": self._queue_last_payload,
                    "last_result": self._queue_last_result,
                    "last_error": self._queue_last_error,
                },
            }
            self.heartbeat_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            time.sleep(1.0)

    def _queue_loop(self) -> None:
        self.control_path.touch(exist_ok=True)
        while not self._stop.is_set():
            if self.stop_path.exists():
                self._runtime("stop flag detected")
                self._stop.set()
                break
            try:
                with self.control_path.open("r", encoding="utf-8") as handle:
                    handle.seek(self._queue_offset)
                    while True:
                        raw = handle.readline()
                        if not raw:
                            break
                        self._queue_offset = handle.tell()
                        raw = raw.strip()
                        if not raw:
                            continue
                        payload = json.loads(raw)
                        self._queue_last_seen_ts = now_iso()
                        self._queue_last_payload = payload
                        self._queue_last_result = "dequeued"
                        self._queue_last_error = None
                        if payload.get("action") == "stop":
                            self._runtime("stop command received")
                            self._stop.set()
                            break
                        self._dispatch_command(payload)
            except FileNotFoundError:
                self.control_path.touch(exist_ok=True)
            except Exception as exc:
                self._queue_last_result = "queue_error"
                self._queue_last_error = str(exc)
                self._runtime(f"queue processing error: {exc}")
            time.sleep(0.2)

    def _dispatch_command(self, payload: dict) -> None:
        port = payload.get("port")
        command = payload.get("command", "")
        if port not in self.ports:
            self._queue_last_result = f"ignored_unknown_port:{port}"
            self._runtime(f"ignored command for unknown port {port}: {command}")
            return
        state = self.ports[port]
        if not state.writable:
            self._queue_last_result = f"ignored_read_only:{port}"
            self._runtime(f"ignored command for read-only port {port}: {command}")
            return
        if not state.serial_obj or not state.serial_obj.is_open:
            self._queue_last_result = f"ignored_closed_port:{port}"
            self._runtime(f"ignored command for closed port {port}: {command}")
            return
        wire = (command.rstrip("\r\n") + "\r\n").encode("utf-8", errors="ignore")
        try:
            self._queue_last_result = f"dispatching:{port}"
            self._runtime(f"dispatch start {port}: {command}")
            bytes_written = state.serial_obj.write(wire)
            if bytes_written != len(wire):
                raise serial.SerialTimeoutException(
                    f"partial write on {port}: {bytes_written}/{len(wire)} bytes"
                )
            # Avoid an unbounded flush() stall; a short bounded drain is enough for shell commands.
            try:
                deadline = time.time() + 0.5
                while time.time() < deadline:
                    if getattr(state.serial_obj, "out_waiting", 0) <= 0:
                        break
                    time.sleep(0.01)
            except Exception:
                pass
            self._write_line(state, f"[COMMAND] {command}\n")
            self._queue_last_result = f"sent:{port}"
            self._queue_last_error = None
            self._runtime(f"dispatch ok {port}: {command}")
        except Exception as exc:
            state.last_error = str(exc)
            self._queue_last_result = f"write_error:{port}"
            self._queue_last_error = str(exc)
            self._runtime(f"write error on {port}: {exc}")

    def _write_manifest(self) -> None:
        payload = {
            "created_at": now_iso(),
            "session_dir": str(self.session_dir),
            "logger_pid": os.getpid(),
            "baudrate": self.baudrate,
            "ports": self.port_map,
            "files": {
                "merged_log": str(self.merged_path),
                "events": str(self.events_path),
                "runtime": str(self.runtime_path),
                "queue": str(self.control_path),
                "heartbeat": str(self.heartbeat_path),
                "pid": str(self.pid_path),
            },
        }
        self.pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _prime_queue_offset(self) -> None:
        self.control_path.touch(exist_ok=True)
        try:
            self._queue_offset = self.control_path.stat().st_size
        except OSError:
            self._queue_offset = 0
        self._queue_last_result = f"primed_eof:{self._queue_offset}"
        self._queue_last_error = None
        self._runtime(f"queue offset primed to EOF {self._queue_offset}")

    def run(self) -> None:
        self._write_manifest()
        # Treat control.jsonl as a live queue, not a durable replay log.
        # On restart we resume from EOF so stale commands are not replayed.
        self._prime_queue_offset()
        threads = [
            threading.Thread(target=self._reader_loop, args=(state,), daemon=True)
            for state in self.ports.values()
        ]
        threads.append(threading.Thread(target=self._queue_loop, daemon=True))
        threads.append(threading.Thread(target=self._heartbeat_loop, daemon=True))
        for thread in threads:
            thread.start()
        self._runtime("session started")
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self._runtime("keyboard interrupt")
            self._stop.set()
        finally:
            self._runtime("session stopping")
            for thread in threads:
                thread.join(timeout=1.0)
            for state in self.ports.values():
                state.close()
            with self._lock:
                self._merged_handle.close()
                self._events_handle.close()
                self._runtime_handle.close()


def resolve_session_dir(value: Optional[str]) -> Path:
    if value:
        return Path(str(value).lstrip("\ufeff")).resolve()
    marker = Path(".current_result_dir")
    if marker.exists():
        saved = marker.read_text(encoding="utf-8").strip().lstrip("\ufeff")
        if saved:
            return Path(saved).resolve()
    raise SystemExit("session dir not provided and .current_result_dir not found")


def append_queue(session_dir: Path, payload: dict) -> None:
    ensure_dir(session_dir)
    queue_file = queue_path(session_dir, create=True)
    with queue_file.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def capture_after_write(ser: serial.Serial, duration_s: float = 0.35) -> List[str]:
    deadline = time.time() + duration_s
    chunks: List[str] = []
    while time.time() < deadline:
        try:
            waiting = ser.in_waiting or 0
        except Exception:
            waiting = 0
        if waiting:
            chunks.append(smart_decode(ser.read(waiting)))
        else:
            time.sleep(0.02)
    text = "".join(chunks).replace("\r", "")
    return [line for line in text.split("\n") if line]


def direct_send_command(command: str, port: str, baudrate: int, role: str, output_dir: Optional[Path] = None) -> dict:
    payload = {
        "ts": now_iso(),
        "mode": "direct",
        "port": port,
        "role": role,
        "baudrate": baudrate,
        "command": command,
    }
    log_handle = None
    try:
        if output_dir is not None:
            ensure_dir(output_dir)
            log_path = output_dir / f"{port}.log"
            log_handle = log_path.open("a", encoding="utf-8", newline="")
            payload["log_path"] = str(log_path)
        with serial.Serial(port, baudrate, timeout=0.2, write_timeout=1.0) as ser:
            wire = (command.rstrip("\r\n") + "\r\n").encode("utf-8", errors="ignore")
            bytes_written = ser.write(wire)
            if bytes_written != len(wire):
                raise serial.SerialTimeoutException(f"partial write on {port}: {bytes_written}/{len(wire)} bytes")
            try:
                ser.flush()
            except Exception:
                pass
            if log_handle is not None:
                log_handle.write(f"{payload['ts']} [{port}/{role}] [COMMAND] {command}\n")
                log_handle.flush()
            echoed = capture_after_write(ser)
            if log_handle is not None:
                for line in echoed:
                    log_handle.write(f"{now_iso()} [{port}/{role}] {line}\n")
                log_handle.flush()
            payload["echo_lines"] = echoed
            payload["result"] = "PASS"
            return payload
    except Exception as exc:
        payload["result"] = "BLOCKED"
        payload["error"] = str(exc)
        return payload
    finally:
        if log_handle is not None:
            log_handle.close()


def infer_send_role(port: Optional[str], command: str) -> str:
    if port:
        explicit = port.upper()
        for role in ("ap", "asr", "cp", "control"):
            try:
                if explicit == get_port(role):
                    return role
            except Exception:
                continue
    if command.strip().lower().startswith("listen "):
        return "asr"
    return "ap"


def cmd_start(args: argparse.Namespace) -> int:
    session_dir = resolve_session_dir(args.session_dir)
    ensure_dir(session_dir)
    baudrate = args.baudrate if args.baudrate is not None else get_baudrate()
    if args.baudrate is not None and not args.no_sync_config:
        set_baudrate(args.baudrate, source="polaris_serial_harness.start")
    port_map = build_logger_port_map(
        ap_port=args.ap_port or "",
        cp_port=args.cp_port or "",
        asr_port=args.asr_port or "",
    )
    logger = SessionLogger(session_dir=session_dir, baudrate=baudrate, port_map=port_map)
    logger.run()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    role = args.role or infer_send_role(args.port, args.command)
    port = resolve_port(
        role,
        args.port.upper() if args.port else None,
        sync_explicit=not args.no_sync_config,
        source="polaris_serial_harness.send",
    )
    baudrate = args.baudrate if args.baudrate is not None else get_baudrate()
    if args.baudrate is not None and not args.no_sync_config:
        set_baudrate(args.baudrate, source="polaris_serial_harness.send")

    session_dir = None
    if not args.direct:
        try:
            session_dir = resolve_session_dir(args.session_dir)
        except SystemExit:
            if args.require_session:
                raise
            session_dir = None

    payload = {
        "ts": now_iso(),
        "port": port,
        "role": role,
        "command": args.command,
    }
    if session_dir is not None:
        payload["mode"] = "session_queue"
        payload["session_dir"] = str(session_dir)
        append_queue(session_dir, payload)
        print(json.dumps(payload, ensure_ascii=True))
        return 0

    output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    direct_payload = direct_send_command(args.command, port, baudrate, role, output_dir=output_dir)
    print(json.dumps(direct_payload, ensure_ascii=True))
    if direct_payload.get("result") == "PASS":
        return 0
    return 3
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    session_dir = resolve_session_dir(args.session_dir)
    append_queue(session_dir, {"ts": now_iso(), "action": "stop"})
    print(f"stop queued for {session_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuous serial logger for Polaris DUT")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="start a continuous logging session")
    start.add_argument("--session-dir", default=None)
    start.add_argument("--baudrate", type=int, default=None, help="default: config/polaris_local_ports.json baudrate")
    start.add_argument("--no-sync-config", action="store_true", help="use explicit session settings without updating local config")
    start.add_argument("--ap-port", default="", help="explicit AP log port for this session")
    start.add_argument("--cp-port", default="", help="explicit CP log port for this session; empty disables CP")
    start.add_argument("--asr-port", default="", help="explicit ASR/upper log port for this session")
    start.set_defaults(func=cmd_start)

    send = sub.add_parser("send", help="queue a command to a writable serial port")
    send.add_argument("--session-dir", default=None)
    send.add_argument("--role", default=None, help="port role to use when --port is omitted; inferred when possible, default: ap")
    send.add_argument("--port", default=None, help="explicit port; synced to local config unless --no-sync-config is set")
    send.add_argument("--baudrate", type=int, default=None, help="default: configured baudrate")
    send.add_argument("--no-sync-config", action="store_true", help="do not sync explicit --port/--baudrate into local config")
    send.add_argument("--direct", action="store_true", help="bypass live session queue and write the serial port directly")
    send.add_argument("--require-session", action="store_true", help="fail when no --session-dir or .current_result_dir is available")
    send.add_argument("--output-dir", default="", help="optional evidence directory for direct serial send")
    send.add_argument("--command", required=True)
    send.set_defaults(func=cmd_send)

    stop = sub.add_parser("stop", help="stop a running session")
    stop.add_argument("--session-dir", default=None)
    stop.set_defaults(func=cmd_stop)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
