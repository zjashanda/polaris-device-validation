#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capability inference for project-specific runtime assertions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple


def infer_from_project_name(project: str) -> Dict[str, Any]:
    text = (project or "").lower()
    if "ws63" in text or "venus" in text:
        return {"cp_log": False, "asr_log": True}
    return {"cp_log": True, "asr_log": True}


def infer_from_env_file(env_file: Path, project: str = "") -> Tuple[str, Dict[str, Any]]:
    payload = json.loads(env_file.read_text(encoding="utf-8-sig"))
    active = project or str(payload.get("active_project", "") or "")
    projects = payload.get("projects", {}) if isinstance(payload, dict) else {}
    project_payload = projects.get(active, {}) if isinstance(projects, dict) else {}
    serial = project_payload.get("serial", {}) if isinstance(project_payload, dict) else {}
    ports = serial.get("ports", {}) if isinstance(serial, dict) else {}
    caps = infer_from_project_name(active)
    if isinstance(ports, dict):
        caps["cp_log"] = bool(str(ports.get("cp", "") or "").strip())
        caps["asr_log"] = bool(str(ports.get("asr", "") or ports.get("upper", "") or "").strip())
    common = payload.get("common", {}) if isinstance(payload, dict) else {}
    timeouts = common.get("timeouts", {}) if isinstance(common, dict) else {}
    if isinstance(timeouts, dict):
        for key in (
            "recognition_timeout_s",
            "timing_guard_ms",
            "wake_cluster_gap_ms",
            "interrupt_guard_ms",
            "post_injection_ms",
            "post_recovery_ms",
        ):
            if key in timeouts:
                caps[key] = timeouts.get(key)
    profile = str(project_payload.get("assertion_profile", "") or "")
    if profile in {"ap_upper_no_cp", "ap_asr_no_cp", "ap_asr_cp_optional"}:
        caps["cp_log"] = False
    if profile == "cp_ap_asr_three_port":
        caps["cp_log"] = True
        caps["asr_log"] = True
    return active, caps
