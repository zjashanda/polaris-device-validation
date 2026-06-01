#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execute or plan actions from the Device Adapter Registry."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO

from .device_adapter import AdapterAction, AdapterRegistry


PLACEHOLDER_RE = re.compile(r"\{(?P<name>[A-Za-z0-9_.-]+)\}")


@dataclass
class AdapterActionResult:
    adapter_id: str
    action: str
    result: str
    reason: str
    cmd: List[str] = field(default_factory=list)
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    side_effect: bool = False
    dry_run: bool = True
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def find_action(registry: AdapterRegistry, adapter_id: str, action_name: str) -> tuple[Optional[AdapterAction], str]:
    for adapter in registry.adapters:
        if adapter.adapter_id != adapter_id:
            continue
        for action in adapter.actions:
            if action.name == action_name:
                return action, ""
        return None, f"adapter {adapter_id} exists but action {action_name} was not found"
    return None, f"adapter {adapter_id} was not found"


def render_command(template: List[str], params: Dict[str, Any]) -> tuple[List[str], List[str]]:
    missing: List[str] = []
    rendered: List[str] = []
    for raw in template:
        text = str(raw)
        for match in PLACEHOLDER_RE.finditer(text):
            key = match.group("name")
            if key not in params or str(params.get(key, "")).strip() == "":
                missing.append(key)
        for key, value in params.items():
            text = text.replace("{" + key + "}", str(value))
        rendered.append(sys.executable if text.lower() == "python" else text)
    return rendered, sorted(set(missing))


def result_from_returncode(returncode: int) -> str:
    # Exit 3 is reserved by local device helpers for environmental blockers
    # such as a busy/missing serial port. Treat it as BLOCKED, not firmware FAIL.
    return "PASS" if returncode == 0 else ("BLOCKED" if returncode == 3 else "FAIL")


def execute_adapter_action(
    registry: AdapterRegistry,
    *,
    adapter_id: str,
    action_name: str,
    params: Dict[str, Any],
    allow_side_effects: bool = False,
    dry_run: bool = True,
    cwd: Optional[Path] = None,
    timeout_s: int = 120,
    stream_log_path: Optional[Path] = None,
    line_callback: Optional[Callable[[str], None]] = None,
) -> AdapterActionResult:
    action, error = find_action(registry, adapter_id, action_name)
    if action is None:
        return AdapterActionResult(adapter_id, action_name, "BLOCKED", error, dry_run=dry_run)
    cmd, missing = render_command(action.command_template, params)
    if missing:
        return AdapterActionResult(
            adapter_id,
            action_name,
            "BLOCKED",
            f"missing action parameters: {', '.join(missing)}",
            cmd=cmd,
            side_effect=action.side_effect,
            dry_run=dry_run,
        )
    if not cmd:
        return AdapterActionResult(
            adapter_id,
            action_name,
            "BLOCKED",
            "adapter action has no command_template",
            side_effect=action.side_effect,
            dry_run=dry_run,
        )
    if action.side_effect and not allow_side_effects and not dry_run:
        return AdapterActionResult(
            adapter_id,
            action_name,
            "BLOCKED",
            "side-effect action requires --allow-side-effects",
            cmd=cmd,
            side_effect=True,
            dry_run=dry_run,
        )
    if dry_run:
        return AdapterActionResult(
            adapter_id,
            action_name,
            "PLAN_OK",
            "adapter command rendered; dry-run did not execute it",
            cmd=cmd,
            side_effect=action.side_effect,
            dry_run=True,
        )

    started_at = datetime.now().isoformat(timespec="milliseconds")
    if stream_log_path is not None:
        lines: List[str] = []
        stream_log_path.parent.mkdir(parents=True, exist_ok=True)
        with stream_log_path.open("w", encoding="utf-8", newline="") as log:
            log.write("$ " + quote_cmd(cmd) + "\n")
            log.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd) if cwd else None,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert proc.stdout is not None
            lock = threading.Lock()

            def reader() -> None:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = raw.rstrip("\n")
                    with lock:
                        lines.append(line)
                        log.write(line + "\n")
                        log.flush()
                    if line_callback is not None:
                        line_callback(line)

            reader_thread = threading.Thread(target=reader, daemon=True)
            reader_thread.start()
            try:
                returncode = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                returncode = proc.wait()
                timeout_line = f"TIMEOUT after {timeout_s}s"
                with lock:
                    lines.append(timeout_line)
                    log.write(timeout_line + "\n")
                    log.flush()
            reader_thread.join(timeout=2)
        finished_at = datetime.now().isoformat(timespec="milliseconds")
        return AdapterActionResult(
            adapter_id,
            action_name,
            result_from_returncode(returncode),
            f"adapter command exited with returncode={returncode}",
            cmd=cmd,
            returncode=returncode,
            stdout="\n".join(lines) + ("\n" if lines else ""),
            stderr="",
            side_effect=action.side_effect,
            dry_run=False,
            started_at=started_at,
            finished_at=finished_at,
        )

    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    return AdapterActionResult(
        adapter_id,
        action_name,
        result_from_returncode(completed.returncode),
        f"adapter command exited with returncode={completed.returncode}",
        cmd=cmd,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        side_effect=action.side_effect,
        dry_run=False,
        started_at=started_at,
        finished_at=datetime.now().isoformat(timespec="milliseconds"),
    )


@dataclass
class AdapterProcessHandle:
    result: AdapterActionResult
    process: Optional[subprocess.Popen[str]] = None
    log_handle: Optional[TextIO] = None


def start_adapter_action(
    registry: AdapterRegistry,
    *,
    adapter_id: str,
    action_name: str,
    params: Dict[str, Any],
    allow_side_effects: bool = False,
    dry_run: bool = True,
    cwd: Optional[Path] = None,
    log_path: Optional[Path] = None,
) -> AdapterProcessHandle:
    """Start a long-lived adapter action.

    Long runners use this for background serial logging.  Process management is
    still centralized here, so runner code only sees an adapter action result
    and a process handle to stop during cleanup.
    """
    action, error = find_action(registry, adapter_id, action_name)
    if action is None:
        return AdapterProcessHandle(AdapterActionResult(adapter_id, action_name, "BLOCKED", error, dry_run=dry_run))
    cmd, missing = render_command(action.command_template, params)
    if missing:
        return AdapterProcessHandle(
            AdapterActionResult(
                adapter_id,
                action_name,
                "BLOCKED",
                f"missing action parameters: {', '.join(missing)}",
                cmd=cmd,
                side_effect=action.side_effect,
                dry_run=dry_run,
            )
        )
    if action.side_effect and not allow_side_effects and not dry_run:
        return AdapterProcessHandle(
            AdapterActionResult(
                adapter_id,
                action_name,
                "BLOCKED",
                "side-effect action requires --allow-side-effects",
                cmd=cmd,
                side_effect=True,
                dry_run=dry_run,
            )
        )
    if dry_run:
        return AdapterProcessHandle(
            AdapterActionResult(
                adapter_id,
                action_name,
                "PLAN_OK",
                "adapter command rendered; dry-run did not start it",
                cmd=cmd,
                side_effect=action.side_effect,
                dry_run=True,
            )
        )

    log_path = log_path or Path("adapter_process.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8", newline="")
    log_handle.write("$ " + quote_cmd(cmd) + "\n")
    log_handle.flush()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return AdapterProcessHandle(
        AdapterActionResult(
            adapter_id,
            action_name,
            "STARTED",
            f"adapter process started pid={proc.pid}",
            cmd=cmd,
            returncode=None,
            side_effect=action.side_effect,
            dry_run=False,
            started_at=datetime.now().isoformat(timespec="milliseconds"),
        ),
        process=proc,
        log_handle=log_handle,
    )


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
