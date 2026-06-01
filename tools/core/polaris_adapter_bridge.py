#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility helpers that route legacy runners through Adapter Executor.

Long-flow scripts should import this module instead of calling serial/audio/
network/power helper tools directly.  The low-level command is still rendered
by the Device Adapter Registry and executed by runtime.adapter_executor.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tools.core.polaris_config import normalize_env_payload, read_env_config, read_json
from tools.core.polaris_runtime import heartbeat_path, workspace_root


ROOT = workspace_root()
BDD_ROOT = ROOT / "satellite" / "cucumber-agent-testing"
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from runtime.adapter_executor import (  # noqa: E402
    AdapterActionResult,
    AdapterProcessHandle,
    execute_adapter_action,
    start_adapter_action,
)
from runtime.device_adapter import build_adapter_registry  # noqa: E402


@dataclass
class PlaybackCapture:
    completed: subprocess.CompletedProcess[str]
    action_result: AdapterActionResult
    started_at: datetime
    playback_started_at: datetime
    finished_at: datetime
    stdout_lines: List[str]


@dataclass
class ManagedAdapterSession:
    session_dir: Path
    process_handle: AdapterProcessHandle
    env_file: str = ""


def load_env_for_adapter(env_file: str = "", *, device_key: str = "") -> Dict[str, Any]:
    if env_file:
        path = Path(env_file)
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        payload = normalize_env_payload(read_json(path))
    else:
        payload = read_env_config()
    if device_key:
        payload = dict(payload)
        audio = dict(payload.get("audio", {}) if isinstance(payload.get("audio"), dict) else {})
        audio["default_playback_device_key"] = device_key
        payload["audio"] = audio
        payload["default_playback_device_key"] = device_key
    return payload


def quote_cmd(args: List[str]) -> str:
    rendered: List[str] = []
    for arg in args:
        text = str(arg)
        if not text:
            rendered.append('""')
        elif any(ch.isspace() for ch in text) or any(ch in text for ch in ['"', "'", "&"]):
            rendered.append('"' + text.replace('"', '\\"') + '"')
        else:
            rendered.append(text)
    return " ".join(rendered)


def run_adapter_action_capture(
    *,
    adapter_id: str,
    action: str,
    params: Optional[Dict[str, Any]] = None,
    env_file: str = "",
    device_key: str = "",
    timeout_s: int = 120,
    execute: bool = True,
    allow_side_effects: bool = True,
    log_path: Optional[Path] = None,
    line_callback: Optional[Callable[[str], None]] = None,
) -> AdapterActionResult:
    payload = load_env_for_adapter(env_file, device_key=device_key)
    registry = build_adapter_registry(payload)
    return execute_adapter_action(
        registry,
        adapter_id=adapter_id,
        action_name=action,
        params=params or {},
        allow_side_effects=allow_side_effects,
        dry_run=not execute,
        cwd=ROOT,
        timeout_s=timeout_s,
        stream_log_path=log_path,
        line_callback=line_callback,
    )


def action_result_to_step(name: str, result: AdapterActionResult, started_at: Optional[datetime] = None) -> Dict[str, Any]:
    if started_at is not None:
        started = started_at
    elif result.started_at:
        started = datetime.fromisoformat(result.started_at)
    else:
        started = datetime.now()
    return {
        "name": name,
        "result": result.result,
        "reason": result.reason,
        "cmd": result.cmd,
        "returncode": result.returncode if result.returncode is not None else (-1 if result.result in {"FAIL", "BLOCKED"} else 0),
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": (result.finished_at or datetime.now().isoformat(timespec="milliseconds"))[:19],
        "log_path": "",
        "stdout_tail": (result.stdout or "").splitlines()[-20:],
        "adapter": result.to_dict(),
    }


def run_audio_playback_adapter(
    audio_file: Path,
    device_key: str,
    *,
    skip_probe: bool = False,
    low_latency: bool = False,
    timeout_s: int = 120,
    env_file: str = "",
    stream_log_path: Optional[Path] = None,
) -> PlaybackCapture:
    started_at = datetime.now()
    playback_started_at: Optional[datetime] = None
    stdout_lines: List[str] = []

    def on_line(line: str) -> None:
        nonlocal playback_started_at
        stdout_lines.append(line)
        if playback_started_at is None and (line.startswith("Play iteration ") or "internal-play" in line.lower()):
            playback_started_at = datetime.now()

    if low_latency:
        action = "play_low_latency"
    else:
        action = "play_skip_probe" if skip_probe else "play"
    result = run_adapter_action_capture(
        adapter_id="audio.playback",
        action=action,
        params={"audio_file": str(audio_file)},
        env_file=env_file,
        device_key=device_key,
        timeout_s=timeout_s,
        execute=True,
        allow_side_effects=True,
        log_path=stream_log_path,
        line_callback=on_line if stream_log_path is not None else None,
    )
    if not stdout_lines:
        stdout_lines = (result.stdout or "").splitlines()
    finished_at = datetime.now()
    completed = subprocess.CompletedProcess(
        args=result.cmd,
        returncode=int(result.returncode if result.returncode is not None else -1),
        stdout=result.stdout,
        stderr=result.stderr,
    )
    return PlaybackCapture(
        completed=completed,
        action_result=result,
        started_at=started_at,
        playback_started_at=playback_started_at or started_at,
        finished_at=finished_at,
        stdout_lines=stdout_lines,
    )


def start_serial_logger_adapter(session_dir: Path, *, env_file: str = "", log_path: Optional[Path] = None) -> ManagedAdapterSession:
    payload = load_env_for_adapter(env_file)
    registry = build_adapter_registry(payload)
    session_dir = session_dir.resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_path or (session_dir / "adapter_serial_logger.log")
    handle = start_adapter_action(
        registry,
        adapter_id="serial.logger",
        action_name="start",
        params={"session_dir": str(session_dir)},
        allow_side_effects=True,
        dry_run=False,
        cwd=ROOT,
        log_path=log_path,
    )
    if handle.result.result != "STARTED":
        raise RuntimeError(f"serial logger adapter did not start: {handle.result.result} {handle.result.reason}")
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if handle.process is not None and handle.process.poll() is not None:
            raise RuntimeError(f"serial logger adapter exited early: rc={handle.process.returncode}, log={log_path}")
        heartbeat = heartbeat_path(session_dir=session_dir)
        if heartbeat.exists():
            try:
                payload = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
            except Exception:
                payload = {}
            if payload.get("ports"):
                return ManagedAdapterSession(session_dir=session_dir, process_handle=handle, env_file=env_file)
        time.sleep(0.25)
    raise RuntimeError(f"serial logger adapter heartbeat not ready: {heartbeat_path(session_dir=session_dir)}")


def stop_serial_logger_adapter(session: ManagedAdapterSession, *, timeout_s: int = 10) -> AdapterActionResult:
    result = run_adapter_action_capture(
        adapter_id="serial.logger",
        action="stop",
        params={"session_dir": str(session.session_dir)},
        env_file=session.env_file,
        timeout_s=timeout_s,
        execute=True,
        allow_side_effects=True,
    )
    process = session.process_handle.process
    if process is not None:
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    handle = session.process_handle.log_handle
    if handle is not None:
        try:
            handle.close()
        except Exception:
            pass
    return result
