#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Constraint checks for Polaris task preflight.

The first landing step is intentionally small: validate that a task is
executable for the selected project before occupying serial ports, audio or
network resources.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from .resource_runtime import ResourceSnapshot, build_resource_snapshot


@dataclass
class ConstraintResult:
    name: str
    result: str
    reason: str
    severity: str = "info"
    actual: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["actual"] = self.actual or {}
        return payload


def _nested(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def _text(value: Any) -> str:
    return str(value or "").strip()


def _pass(name: str, reason: str, **actual: Any) -> ConstraintResult:
    return ConstraintResult(name=name, result="PASS", reason=reason, actual=actual)


def _warn(name: str, reason: str, **actual: Any) -> ConstraintResult:
    return ConstraintResult(name=name, result="WARN", reason=reason, severity="warn", actual=actual)


def _blocked(name: str, reason: str, **actual: Any) -> ConstraintResult:
    return ConstraintResult(name=name, result="BLOCKED", reason=reason, severity="error", actual=actual)


def _fail(name: str, reason: str, **actual: Any) -> ConstraintResult:
    return ConstraintResult(name=name, result="FAIL", reason=reason, severity="error", actual=actual)


def _probe_serial_availability(ports: Dict[str, Any], *, baudrate: str, control_baudrate: str) -> ConstraintResult:
    """Open configured serial ports briefly so execute mode fails as BLOCKED, not firmware FAIL."""
    try:
        import serial  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on host packages
        return _warn("serial_open_probe", "无法导入 pyserial，跳过串口占用预检。", error=repr(exc))

    role_ports: List[tuple[str, str, str]] = []
    for role in ("ap", "cp", "asr", "upper", "control"):
        port = _text(ports.get(role))
        if not port:
            continue
        selected_baud = control_baudrate if role == "control" else baudrate
        role_ports.append((role, port, selected_baud))

    seen: set[str] = set()
    checked: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for role, port, selected_baud in role_ports:
        key = port.upper()
        if key in seen:
            continue
        seen.add(key)
        try:
            ser = serial.Serial(port, int(selected_baud or "115200"), timeout=0.2)
            ser.close()
            checked.append({"role": role, "port": port, "baudrate": int(selected_baud or "115200"), "result": "OPEN_OK"})
        except Exception as exc:
            failures.append({"role": role, "port": port, "baudrate": selected_baud, "error": repr(exc)})

    if failures:
        return _blocked(
            "serial_open_probe",
            "execute 前发现串口不可打开，通常是端口被 Xshell/串口助手/旧 logger 占用；本轮应判 BLOCKED，不能归因为固件功能失败。",
            checked=checked,
            failures=failures,
        )
    return _pass("serial_open_probe", "execute 前串口可打开，未发现端口占用。", checked=checked)


def is_online_task(task: Dict[str, Any], tag: str = "") -> bool:
    schema = _text(task.get("schema")).lower()
    runner = task.get("runner", {}) if isinstance(task.get("runner"), dict) else {}
    entrypoint = _text(runner.get("entrypoint")).lower()
    tag_text = _text(tag or _nested(task, "scenario", "tag")).lower()
    return "online" in schema or "online" in entrypoint or "online" in tag_text or "network" in tag_text


def is_interrupt_task(task: Dict[str, Any], tag: str = "") -> bool:
    tag_text = _text(tag or _nested(task, "scenario", "tag")).lower()
    return "interrupt" in tag_text


def aggregate_constraint_result(results: List[ConstraintResult]) -> str:
    if any(item.result == "FAIL" for item in results):
        return "FAIL"
    if any(item.result == "BLOCKED" for item in results):
        return "BLOCKED"
    if any(item.result == "WARN" for item in results):
        return "PASS_WITH_WARNINGS"
    return "PASS"


def evaluate_constraints(
    *,
    task: Dict[str, Any],
    env_payload: Dict[str, Any],
    mode: str,
    allow_side_effects: bool,
    tag: str = "",
) -> Dict[str, Any]:
    resource_snapshot = build_resource_snapshot(env_payload, task)
    results: List[ConstraintResult] = []

    project_id = _text(env_payload.get("project_id") or _nested(env_payload, "_config_source", "active_project"))
    if project_id:
        results.append(_pass("project_selected", "已选择 active project。", project_id=project_id))
    else:
        results.append(_blocked("project_selected", "未选择 active project，无法推导串口/能力。"))

    if _text(_nested(task, "scenario", "tag")) or "online-stress" in _text(task.get("schema")):
        results.append(_pass("scenario_selected", "任务已声明场景 tag 或在线压测 schema。", tag=_nested(task, "scenario", "tag"), schema=task.get("schema", "")))
    else:
        results.append(_blocked("scenario_selected", "任务未声明 scenario.tag，也不是在线压测任务。"))

    if mode == "execute" and not allow_side_effects:
        results.append(_blocked("side_effects_allowed", "execute 模式必须显式允许副作用。"))
    else:
        results.append(_pass("side_effects_allowed", "副作用策略满足当前模式。", mode=mode, allow_side_effects=allow_side_effects))

    ports = _nested(env_payload, "serial", "ports")
    ports = ports if isinstance(ports, dict) else {}
    baudrate = _text(_nested(env_payload, "serial", "baudrate") or env_payload.get("baudrate") or "115200")
    control_baudrate = _text(_nested(env_payload, "serial", "control_baudrate") or baudrate)
    project_type = _text(env_payload.get("project_type")).lower()
    missing: List[str] = []
    if project_type in {"wb01", "cskwb01"}:
        required = ["ap", "cp", "asr", "control"]
    elif project_type in {"ws63", "venusws63"}:
        required = ["ap", "control"]
        if not (_text(ports.get("upper")) or _text(ports.get("asr"))):
            missing.append("upper/asr")
    else:
        required = ["ap", "control"]
    missing.extend([role for role in required if not _text(ports.get(role))])
    if missing:
        results.append(_blocked("serial_topology", "串口拓扑缺少必需端口。", missing=missing, ports=ports, project_type=project_type))
    else:
        results.append(_pass("serial_topology", "串口拓扑满足当前项目最小要求。", ports=ports, project_type=project_type))

    if mode == "execute" and allow_side_effects and not missing:
        results.append(_probe_serial_availability(ports, baudrate=baudrate, control_baudrate=control_baudrate))

    if resource_snapshot.conflicts:
        results.append(_blocked("resource_conflicts", "资源存在独占冲突。", conflicts=[item.to_dict() for item in resource_snapshot.conflicts]))
    else:
        results.append(_pass("resource_conflicts", "未发现明显资源冲突。"))

    cloud_api = _text(_nested(env_payload, "cloud", "api_environment"))
    device_env = _text(_nested(env_payload, "cloud", "device_env"))
    if cloud_api and device_env and cloud_api != device_env:
        results.append(_warn("cloud_env_match", "云控 API 环境与设备端声明环境不一致，API 设置可能不生效。", api_environment=cloud_api, device_env=device_env))
    elif cloud_api or device_env:
        results.append(_pass("cloud_env_match", "云控 API 环境与设备端声明环境一致或只配置了一侧。", api_environment=cloud_api, device_env=device_env))

    if is_online_task(task, tag):
        wifi = _text(_nested(env_payload, "network", "wifi_ssid"))
        if wifi:
            results.append(_pass("online_network_config", "在线/联网场景已配置 Wi-Fi SSID。", wifi_ssid=wifi))
        else:
            results.append(_warn("online_network_config", "在线/联网场景未配置 Wi-Fi SSID；如果设备已联网可继续，但归因会降低置信度。"))

    tag_text = _text(tag or _nested(task, "scenario", "tag"))
    if tag_text == "network_recovery_basic" and not bool(_nested(env_payload, "network", "enable_hotspot_control")):
        results.append(_blocked("network_recovery_control", "联网恢复场景需要 enable_hotspot_control=true 或等价网络控制能力。"))

    if is_interrupt_task(task, tag):
        guard_ms = int(_nested(env_payload, "timeouts", "interrupt_guard_ms") or 0)
        if guard_ms > 0:
            results.append(_pass("interrupt_guard_config", "打断场景已配置 guard 时间。", interrupt_guard_ms=guard_ms))
        else:
            results.append(_warn("interrupt_guard_config", "打断场景未配置 interrupt_guard_ms，临界播报窗口可能难以归因。"))

    return {
        "result": aggregate_constraint_result(results),
        "constraints": [item.to_dict() for item in results],
        "resource_snapshot": resource_snapshot.to_dict(),
    }
