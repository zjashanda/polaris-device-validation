#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-contained `laid` checker/installer for Polaris audio device keys.

New machines can run:

    python tools/audio/polaris_laid.py ensure
    python tools/audio/polaris_laid.py list

The installer scripts live under tools/audio/laid/ so the project does not
depend on a separate local Codex skill being present.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
LAID_DIR = SCRIPT_DIR / "laid"
WINDOWS_INSTALLER = LAID_DIR / "install_laid_windows.ps1"
LINUX_INSTALLER = LAID_DIR / "install_laid_linux.sh"


def run_cmd(cmd: List[str], *, timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
        check=False,
    )


def is_windows() -> bool:
    return platform.system().lower().startswith("win")


def powershell_profile_loader(command: str) -> List[str]:
    script = rf"""
$ErrorActionPreference='SilentlyContinue'
$paths=@($PROFILE.CurrentUserAllHosts,$PROFILE.CurrentUserCurrentHost) | Where-Object {{ $_ -and (Test-Path -LiteralPath $_) }}
foreach($p in $paths) {{ . $p }}
{command}
""".strip()
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def bash_login_command(command: str) -> List[str]:
    script = (
        "set +e; "
        "[ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\"; "
        "[ -f \"$HOME/.zshrc\" ] && . \"$HOME/.zshrc\"; "
        + command
    )
    return ["bash", "-lc", script]


def check_laid() -> Dict[str, Any]:
    if is_windows():
        completed = run_cmd(
            powershell_profile_loader(
                "if (Get-Command laid -ErrorAction SilentlyContinue) { 'INSTALLED'; exit 0 } else { 'MISSING'; exit 1 }"
            ),
            timeout_s=30,
        )
    else:
        completed = run_cmd(bash_login_command("command -v laid >/dev/null 2>&1 && echo INSTALLED || { echo MISSING; exit 1; }"), timeout_s=30)
    return {
        "installed": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def install_laid(scope: str = "CurrentUserAllHosts", shell_targets: List[str] | None = None) -> Dict[str, Any]:
    if is_windows():
        if not WINDOWS_INSTALLER.exists():
            return {"installed": False, "returncode": 2, "stdout": "", "stderr": f"missing installer: {WINDOWS_INSTALLER}"}
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_INSTALLER),
            "-Scope",
            scope,
        ]
    else:
        if not LINUX_INSTALLER.exists():
            return {"installed": False, "returncode": 2, "stdout": "", "stderr": f"missing installer: {LINUX_INSTALLER}"}
        cmd = ["bash", str(LINUX_INSTALLER)] + list(shell_targets or [])
    completed = run_cmd(cmd, timeout_s=120)
    after = check_laid()
    return {
        "installed": bool(after.get("installed")),
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "post_check": after,
    }


def list_laid(direction: str = "All", json_output: bool = False) -> Dict[str, Any]:
    if is_windows():
        command = f"laid -Direction {direction}" + (" -Json" if json_output else "")
        completed = run_cmd(powershell_profile_loader(command), timeout_s=60)
    else:
        completed = run_cmd(bash_login_command("laid"), timeout_s=60)
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout.rstrip(),
        "stderr": completed.stderr.rstrip(),
        "json_requested": json_output,
    }


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check/install/list Polaris ListenAI audio device keys via laid.")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("check", help="Check whether laid is available from the user's shell profile")

    install = sub.add_parser("install", help="Install or refresh laid into the user's shell profile")
    install.add_argument("--scope", default="CurrentUserAllHosts", choices=["CurrentUserAllHosts", "CurrentUserCurrentHost"])
    install.add_argument("--shell", action="append", default=[], choices=["bash", "zsh"], help="Linux only; default updates bash and zsh")

    ensure = sub.add_parser("ensure", help="Install laid only when it is missing")
    ensure.add_argument("--scope", default="CurrentUserAllHosts", choices=["CurrentUserAllHosts", "CurrentUserCurrentHost"])
    ensure.add_argument("--shell", action="append", default=[], choices=["bash", "zsh"], help="Linux only; default updates bash and zsh")

    list_cmd = sub.add_parser("list", help="List stable ListenAI device keys")
    list_cmd.add_argument("--direction", default="All", choices=["All", "Render", "Capture"])
    list_cmd.add_argument("--json", action="store_true", help="Windows only: return laid -Json output")
    list_cmd.add_argument("--install-if-missing", action="store_true")

    args = parser.parse_args()
    if args.action == "check":
        payload = check_laid()
        print_json(payload)
        return 0 if payload["installed"] else 1
    if args.action == "install":
        payload = install_laid(scope=args.scope, shell_targets=args.shell)
        print_json(payload)
        return 0 if payload["installed"] else 2
    if args.action == "ensure":
        before = check_laid()
        if before["installed"]:
            print_json({"result": "PASS", "changed": False, "check": before})
            return 0
        install_result = install_laid(scope=args.scope, shell_targets=args.shell)
        print_json({"result": "PASS" if install_result["installed"] else "FAIL", "changed": True, "install": install_result})
        return 0 if install_result["installed"] else 2
    if args.action == "list":
        before = check_laid()
        if not before["installed"] and args.install_if_missing:
            install_laid()
            before = check_laid()
        if not before["installed"]:
            print_json({"result": "BLOCKED", "reason": "laid is not installed", "check": before})
            return 2
        payload = list_laid(direction=args.direction, json_output=args.json)
        if args.json and is_windows() and payload["returncode"] == 0:
            print(payload["stdout"])
        else:
            print(payload["stdout"])
            if payload["stderr"]:
                print(payload["stderr"], file=sys.stderr)
        return 0 if payload["returncode"] == 0 else payload["returncode"]
    raise AssertionError(args.action)


if __name__ == "__main__":
    raise SystemExit(main())
