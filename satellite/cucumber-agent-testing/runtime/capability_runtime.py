#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Project capability matrix for deterministic validation planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .device_adapter import build_adapter_registry


def _text(value: Any) -> str:
    return str(value or "").strip()


def _nested(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


@dataclass
class CapabilityItem:
    name: str
    status: str
    evidence: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CapabilityMatrix:
    project_id: str
    project_type: str
    capabilities: List[CapabilityItem]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "polaris.capability_matrix.v1",
            "project_id": self.project_id,
            "project_type": self.project_type,
            "summary": self.summary(),
            "capabilities": [item.to_dict() for item in self.capabilities],
        }

    def summary(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for item in self.capabilities:
            result[item.status] = result.get(item.status, 0) + 1
        return result


def _cap(name: str, supported: bool, evidence: List[str], gap: str = "", *, notes: List[str] | None = None) -> CapabilityItem:
    return CapabilityItem(
        name=name,
        status="supported" if supported else "config_required",
        evidence=evidence if supported else [],
        gaps=[] if supported or not gap else [gap],
        notes=notes or [],
    )


def _cloud_permission_cap(name: str, permission: str, api_env: str, cloud_permissions: List[str], gap: str) -> CapabilityItem:
    supported = bool(api_env and permission in set(cloud_permissions))
    return CapabilityItem(
        name=name,
        status="supported" if supported else "config_required",
        evidence=[f"api_environment={api_env}", f"permission={permission}"] if supported else [],
        gaps=[] if supported else [gap],
        notes=[
            "如项目有本地串口等价命令，也可在 intake/feature contract 中声明后接入 adapter action。"
        ],
    )


def build_capability_matrix(env_payload: Dict[str, Any]) -> CapabilityMatrix:
    project_id = _text(env_payload.get("project_id") or _nested(env_payload, "_config_source", "active_project"))
    project_type = _text(env_payload.get("project_type"))
    ports = _nested(env_payload, "serial", "ports")
    ports = ports if isinstance(ports, dict) else {}
    adapters = build_adapter_registry(env_payload)
    adapter_caps = {cap for adapter in adapters.adapters for cap in adapter.capabilities}

    ap = _text(ports.get("ap"))
    cp = _text(ports.get("cp"))
    asr = _text(ports.get("asr") or ports.get("upper"))
    control = _text(ports.get("control"))
    audio_key = _text(_nested(env_payload, "audio", "default_playback_device_key"))
    audio_capture_key = _text(_nested(env_payload, "audio", "capture_device_key") or _nested(env_payload, "audio", "loopback_device_key"))
    wifi_ssid = _text(_nested(env_payload, "network", "wifi_ssid"))
    hotspot = bool(_nested(env_payload, "network", "enable_hotspot_control"))
    api_env = _text(_nested(env_payload, "cloud", "api_environment"))
    device_env_cmd = _text(_nested(env_payload, "cloud", "device_env_command"))
    cloud_permissions_raw = _nested(env_payload, "cloud", "capabilities") or _nested(env_payload, "cloud", "permissions")
    cloud_permissions = [str(item).strip() for item in cloud_permissions_raw] if isinstance(cloud_permissions_raw, list) else []
    boot_reason_patterns = _nested(env_payload, "reboot", "boot_reason_patterns") or _nested(env_payload, "diagnostics", "boot_reason_patterns")
    boot_reason_patterns = boot_reason_patterns if isinstance(boot_reason_patterns, list) else []
    preconditions = _nested(env_payload, "serial", "control_preconditions")
    preconditions = preconditions if isinstance(preconditions, list) else []

    first_wake_ready = bool(ap and asr and "audio_playback" in adapter_caps)
    three_port_ready = bool(ap and cp and asr)
    online_ready = bool(wifi_ssid and api_env)
    control_ready = bool(control)
    pa_ready = bool(control and preconditions)

    items = [
        _cap("serial.ap_log", bool(ap), [f"ap={ap}"], "配置 serial.ports.ap"),
        CapabilityItem(
            name="serial.cp_log",
            status="supported" if cp else "not_applicable",
            evidence=[f"cp={cp}"] if cp else [],
            gaps=[],
            notes=["WS63/AP+WiFi 项目 cp 留空是正常能力降级。"] if not cp else [],
        ),
        _cap("serial.asr_log", bool(asr), [f"asr={asr}"], "配置 serial.ports.asr 或 serial.ports.upper"),
        _cap("serial.control", control_ready, [f"control={control}"], "配置 serial.ports.control"),
        _cap("audio.playback", True, [f"device_key={audio_key or 'DEFAULT_RENDER_DEVICE'}"], ""),
        _cap(
            "audio.loopback_oracle",
            bool(audio_capture_key),
            [f"capture_device_key={audio_capture_key}"],
            "如需真实出声/回采判定，配置 audio.capture_device_key 或 audio.loopback_device_key；否则只能依赖设备日志判断播报。",
        ),
        _cap("power.pa_control", pa_ready, [f"control={control}", f"preconditions={len(preconditions)}"], "配置控制口和 serial.control_preconditions"),
        _cap("network.wifi_config", bool(wifi_ssid), [f"ssid={wifi_ssid}"], "配置 network.wifi_ssid"),
        _cap("network.hotspot_control", hotspot, ["enable_hotspot_control=true"], "配置 network.enable_hotspot_control=true"),
        _cap("cloud.api", bool(api_env), [f"api_environment={api_env}"], "配置 cloud.api_environment"),
        _cap("cloud.device_env_switch", bool(api_env and device_env_cmd), [f"env={api_env}", f"command={device_env_cmd}"], "配置 cloud.device_env_command"),
        _cloud_permission_cap("cloud.volume_control", "volume_control", api_env, cloud_permissions, "需要 cloud.capabilities/permissions 声明 volume_control，或提供本地等价命令。"),
        _cloud_permission_cap("cloud.night_mode", "night_mode", api_env, cloud_permissions, "需要 cloud.capabilities/permissions 声明 night_mode，或提供本地等价命令。"),
        _cloud_permission_cap("cloud.wake_word_config", "wake_word_config", api_env, cloud_permissions, "需要 cloud.capabilities/permissions 声明 wake_word_config，或提供本地等价命令。"),
        _cloud_permission_cap("cloud.wake_threshold", "wake_threshold", api_env, cloud_permissions, "需要 cloud.capabilities/permissions 声明 wake_threshold，或提供本地等价命令。"),
        _cloud_permission_cap("cloud.multi_wake", "multi_wake", api_env, cloud_permissions, "需要 cloud.capabilities/permissions 声明 multi_wake，或提供本地等价命令。"),
        _cap("wake.first_wake", first_wake_ready, [f"ap={ap}", f"asr={asr}"], "需要 AP/ASR 日志和音频播放"),
        _cap("wake.three_port_closed_loop", three_port_ready, [f"ap={ap}", f"cp={cp}", f"asr={asr}"], "需要 AP/CP/ASR 三端日志"),
        _cap("wake.recognition_mode_wake", first_wake_ready, ["first_wake prerequisites", f"timeout={_nested(env_payload, 'timeouts', 'recognition_timeout_s') or 15}s"], "先满足 wake.first_wake"),
        _cap("recognition.basic_command", first_wake_ready, ["wake + ASR/command parser"], "先满足 wake.first_wake"),
        CapabilityItem(
            name="recognition.half_duplex",
            status="supported" if api_env else "config_required",
            evidence=[f"api_environment={api_env}"] if api_env else [],
            gaps=[] if api_env else ["需要 App/cloud API 或本地等价命令设置半双工。"],
            notes=["已接 Runtime profile；具体能否执行取决于项目 API/命令是否可用。"],
        ),
        CapabilityItem(
            name="recognition.full_duplex",
            status="supported" if api_env else "config_required",
            evidence=[f"api_environment={api_env}"] if api_env else [],
            gaps=[] if api_env else ["需要 App/cloud API 或本地等价命令设置全双工。"],
            notes=["已接 Runtime profile；具体能否执行取决于项目 API/命令是否可用。"],
        ),
        _cap("media.online_interaction", online_ready, [f"ssid={wifi_ssid}", f"api_environment={api_env}"], "需要 Wi-Fi 与 cloud.api_environment"),
        _cap("media.response_log_oracle", bool(ap or asr), [f"ap={ap}", f"asr={asr}"], "需要 AP 或 ASR/上位日志解析 TTSStarted/MediaStarted/MediaCompleted。"),
        _cap(
            "media.acoustic_response_oracle",
            bool(audio_capture_key),
            [f"capture_device_key={audio_capture_key}"],
            "需要音频回采设备或声学 loopback；否则只能证明设备日志说播了，不能证明真实出声质量。",
        ),
        _cap("interrupt.wake_interrupt", first_wake_ready and pa_ready, ["wake prerequisites", "pa/control available"], "需要首唤醒能力和自播/PA 前置"),
        _cap("interrupt.command_interrupt", first_wake_ready and online_ready, ["wake prerequisites", "online/media available"], "需要首唤醒能力和可触发自播/在线媒体"),
        _cap("network.recovery_basic", bool(hotspot and wifi_ssid), ["hotspot control", f"ssid={wifi_ssid}"], "需要 PC 热点控制和 Wi-Fi 配置"),
        _cap(
            "reboot.boot_reason_oracle",
            bool(ap and boot_reason_patterns),
            [f"ap={ap}", f"patterns={len(boot_reason_patterns)}"],
            "需要项目提供 boot reason/reset reason/watchdog/crash 原因日志模式，当前只能检测 RebootDetected/CrashDetected 事件。",
        ),
    ]
    return CapabilityMatrix(project_id=project_id, project_type=project_type, capabilities=items)


def render_capability_markdown(matrix: CapabilityMatrix) -> str:
    lines = [
        "# Polaris Capability Matrix",
        "",
        f"- project_id: `{matrix.project_id}`",
        f"- project_type: `{matrix.project_type}`",
        f"- summary: `{matrix.summary()}`",
        "",
        "| Capability | Status | Evidence | Gaps |",
        "|---|---|---|---|",
    ]
    for item in matrix.capabilities:
        evidence = "<br>".join(item.evidence) if item.evidence else ""
        gaps = "<br>".join(item.gaps) if item.gaps else ""
        lines.append(f"| `{item.name}` | `{item.status}` | {evidence} | {gaps} |")
    lines.append("")
    return "\n".join(lines)
