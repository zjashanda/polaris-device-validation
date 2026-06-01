#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Device adapter registry for the local Polaris runtime.

This module does not replace the existing tools.  It gives the runtime a stable
description of the adapters that those tools already provide, so schedulers,
constraints and reports can reason about device access without hard-coded
project branches.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


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
class AdapterAction:
    name: str
    kind: str
    command_template: List[str] = field(default_factory=list)
    side_effect: bool = False
    resources: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeviceAdapter:
    adapter_id: str
    kind: str
    status: str
    resources: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    actions: List[AdapterAction] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        item = asdict(self)
        item["actions"] = [action.to_dict() for action in self.actions]
        return item


@dataclass
class AdapterRegistry:
    project_id: str
    project_type: str
    adapters: List[DeviceAdapter]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "polaris.adapter_registry.v1",
            "project_id": self.project_id,
            "project_type": self.project_type,
            "adapter_count": len(self.adapters),
            "warnings": self.warnings,
            "adapters": [adapter.to_dict() for adapter in self.adapters],
        }


def _serial_adapter(role: str, port: str, baudrate: str, *, writable: bool) -> DeviceAdapter:
    role_label = role.lower()
    status = "available" if port else "disabled"
    actions = [
        AdapterAction(
            name="log",
            kind="serial_log",
            command_template=[
                "python",
                "tools/device/polaris_serial_harness.py",
                "start",
                f"--{role_label}-port",
                port,
                "--baudrate",
                baudrate,
            ],
            side_effect=False,
            resources=[f"serial:{port}"] if port else [],
        )
    ]
    if writable:
        actions.append(
            AdapterAction(
                name="send",
                kind="serial_write",
                command_template=[
                    "python",
                    "tools/device/polaris_serial_harness.py",
                    "send",
                    "--role",
                    role_label,
                    "--port",
                    port,
                    "--baudrate",
                    baudrate,
                    "--no-sync-config",
                    "--command",
                    "{command}",
                ],
                side_effect=True,
                resources=[f"serial:{port}"] if port else [],
            )
        )
    return DeviceAdapter(
        adapter_id=f"serial.{role_label}",
        kind="serial",
        status=status,
        resources=[f"serial:{port}"] if port else [],
        capabilities=[f"{role_label}_log"] + ([f"{role_label}_write"] if writable else []),
        actions=actions if port else [],
        config={"role": role_label, "port": port, "baudrate": baudrate, "writable": writable},
        warnings=[] if port else [f"{role_label} port is empty"],
    )


def build_adapter_registry(env_payload: Dict[str, Any]) -> AdapterRegistry:
    project_id = _text(env_payload.get("project_id") or _nested(env_payload, "_config_source", "active_project"))
    project_type = _text(env_payload.get("project_type"))
    ports = _nested(env_payload, "serial", "ports")
    ports = ports if isinstance(ports, dict) else {}
    baudrate = _text(_nested(env_payload, "serial", "baudrate") or env_payload.get("baudrate") or "115200")
    control_baudrate = _text(_nested(env_payload, "serial", "control_baudrate") or baudrate)

    adapters: List[DeviceAdapter] = [
        _serial_adapter("ap", _text(ports.get("ap")), baudrate, writable=True),
        _serial_adapter("cp", _text(ports.get("cp")), baudrate, writable=False),
        _serial_adapter("asr", _text(ports.get("asr") or ports.get("upper")), baudrate, writable=True),
    ]
    device_env_command = _text(_nested(env_payload, "cloud", "device_env_command"))
    if _text(ports.get("ap")) and device_env_command:
        adapters[0].actions.append(
            AdapterAction(
                name="set_device_env",
                kind="serial_config",
                command_template=[
                    "python",
                    "tools/device/polaris_serial_harness.py",
                    "send",
                    "--role",
                    "ap",
                    "--port",
                    _text(ports.get("ap")),
                    "--baudrate",
                    baudrate,
                    "--no-sync-config",
                    "--command",
                    device_env_command,
                ],
                side_effect=True,
                resources=[f"serial:{_text(ports.get('ap'))}"],
                notes=["API/云控前先确认设备端环境与 cloud.api_environment 一致。"],
            )
        )
        adapters[0].capabilities.append("device_env_write")

    control_port = _text(ports.get("control"))
    power_on_command = _text(_nested(env_payload, "serial", "power_on_command") or "uut-switch1.on")
    power_off_command = _text(_nested(env_payload, "serial", "power_off_command") or "uut-switch1.off")
    control_actions = [
        AdapterAction(
            name="send_control",
            kind="serial_control",
            command_template=[
                "python",
                "tools/device/polaris_power_control.py",
                "send",
                "--port",
                control_port,
                "--baudrate",
                control_baudrate,
                "--command",
                "{command}",
            ],
            side_effect=True,
            resources=[f"serial:{control_port}"] if control_port else [],
            notes=["PA and power-control commands must be sent through the control port."],
        )
    ]
    for action_name, command, note in [
        ("pa_on", "uut-pa.on", "打开 PA；声卡播放有声但设备无唤醒时优先尝试。"),
        ("pa_persist", "pa-enable.set 0 17 0 1", "保存 PA 使能配置；必须发到控制口。"),
        ("power_on", power_on_command, "项目上电控制命令，可通过 serial.power_on_command 覆盖。"),
        ("power_off", power_off_command, "项目掉电控制命令，可通过 serial.power_off_command 覆盖。"),
    ]:
        if command:
            control_actions.append(
                AdapterAction(
                    name=action_name,
                    kind="serial_control",
                    command_template=[
                        "python",
                        "tools/device/polaris_power_control.py",
                        "send",
                        "--port",
                        control_port,
                        "--baudrate",
                        control_baudrate,
                        "--command",
                        command,
                    ],
                    side_effect=True,
                    resources=[f"serial:{control_port}"] if control_port else [],
                    notes=[note],
                )
            )
    if control_port:
        control_actions.append(
            AdapterAction(
                name="cycle_target",
                kind="power_cycle",
                command_template=[
                    "python",
                    "tools/device/polaris_power_control.py",
                    "cycle",
                    "--target",
                    "{target}",
                    "--port",
                    control_port,
                    "--baudrate",
                    control_baudrate,
                ],
                side_effect=True,
                resources=[f"serial:{control_port}"],
                notes=["target 取 asr/wb01/csk；会真实上下电并采集窗口日志。"],
            )
        )
        control_actions.append(
            AdapterAction(
                name="cycle_target_window",
                kind="power_cycle",
                command_template=[
                    "python",
                    "tools/device/polaris_power_control.py",
                    "cycle",
                    "--target",
                    "{target}",
                    "--port",
                    control_port,
                    "--baudrate",
                    control_baudrate,
                    "--off-wait",
                    "{off_wait}",
                    "--observe",
                    "{observe}",
                    "--output-dir",
                    "{output_dir}",
                ],
                side_effect=True,
                resources=[f"serial:{control_port}"],
                notes=["Adapter-only power cycle with explicit evidence output directory."],
            )
        )
    adapters.append(
        DeviceAdapter(
            adapter_id="control.serial",
            kind="power_control",
            status="available" if control_port else "disabled",
            resources=[f"serial:{control_port}"] if control_port else [],
            capabilities=["power_control", "pa_control"] if control_port else [],
            actions=control_actions if control_port else [],
            config={
                "port": control_port,
                "baudrate": control_baudrate,
                "preconditions": _nested(env_payload, "serial", "control_preconditions") or [],
            },
            warnings=[] if control_port else ["control port is empty"],
        )
    )

    logger_cmd = [
        "python",
        "tools/device/polaris_serial_harness.py",
        "start",
        "--session-dir",
        "{session_dir}",
        "--no-sync-config",
        "--baudrate",
        baudrate,
    ]
    if _text(ports.get("ap")):
        logger_cmd.extend(["--ap-port", _text(ports.get("ap"))])
    if _text(ports.get("cp")):
        logger_cmd.extend(["--cp-port", _text(ports.get("cp"))])
    if _text(ports.get("asr") or ports.get("upper")):
        logger_cmd.extend(["--asr-port", _text(ports.get("asr") or ports.get("upper"))])
    logger_resources = [f"serial:{port}" for port in [_text(ports.get("cp")), _text(ports.get("asr") or ports.get("upper")), _text(ports.get("ap"))] if port]
    adapters.append(
        DeviceAdapter(
            adapter_id="serial.logger",
            kind="serial_logger",
            status="available" if logger_resources else "disabled",
            resources=logger_resources,
            capabilities=["serial_session_log"] if logger_resources else [],
            actions=[
                AdapterAction(
                    name="start",
                    kind="serial_logger_start",
                    command_template=logger_cmd,
                    side_effect=False,
                    resources=logger_resources,
                    notes=["Starts the managed serial harness for long-flow runners."],
                ),
                AdapterAction(
                    name="stop",
                    kind="serial_logger_stop",
                    command_template=[
                        "python",
                        "tools/device/polaris_serial_harness.py",
                        "stop",
                        "--session-dir",
                        "{session_dir}",
                    ],
                    side_effect=False,
                    resources=logger_resources,
                ),
            ]
            if logger_resources
            else [],
            config={"baudrate": baudrate, "ports": {key: ports.get(key, "") for key in ("cp", "asr", "upper", "ap")}},
            warnings=[] if logger_resources else ["no serial log ports are configured"],
        )
    )

    audio_key = _text(_nested(env_payload, "audio", "default_playback_device_key"))
    audio_script = r"C:\Users\Administrator\.codex\skills\listenai-play\scripts\listenai_play.py"
    play_cmd = [
        "python",
        audio_script,
        "play",
        "--audio-file",
        "{audio_file}",
    ] + (["--device-key", audio_key] if audio_key else [])
    play_skip_cmd = list(play_cmd) + ["--skip-probe"]
    low_latency_cmd = [
        "python",
        audio_script,
        "internal-play-once",
        "--platform",
        "windows",
    ] + (["--device-key", audio_key] if audio_key else []) + ["--audio-file", "{audio_file}"]
    adapters.append(
        DeviceAdapter(
            adapter_id="audio.playback",
            kind="audio",
            status="available",
            resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
            capabilities=["audio_playback", "wake_audio_injection"],
            actions=[
                AdapterAction(
                    name="ensure_laid",
                    kind="audio_probe",
                    command_template=["python", "tools/audio/polaris_laid.py", "ensure"],
                    side_effect=True,
                    resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
                ),
                AdapterAction(
                    name="laid_check",
                    kind="audio_probe",
                    command_template=["python", "tools/audio/polaris_laid.py", "check"],
                    side_effect=False,
                    resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
                ),
                AdapterAction(
                    name="laid_install",
                    kind="audio_probe",
                    command_template=["python", "tools/audio/polaris_laid.py", "install"],
                    side_effect=True,
                    resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
                ),
                AdapterAction(
                    name="laid_list",
                    kind="audio_probe",
                    command_template=["python", "tools/audio/polaris_laid.py", "list"],
                    side_effect=False,
                    resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
                ),
                AdapterAction(
                    name="probe",
                    kind="audio_probe",
                    command_template=["python", audio_script, "probe"] + (["--device-key", audio_key] if audio_key else []),
                    side_effect=False,
                    resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
                ),
                AdapterAction(
                    name="play",
                    kind="audio_playback",
                    command_template=play_cmd,
                    side_effect=True,
                    resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
                ),
                AdapterAction(
                    name="play_skip_probe",
                    kind="audio_playback",
                    command_template=play_skip_cmd,
                    side_effect=True,
                    resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
                ),
                AdapterAction(
                    name="play_repeat",
                    kind="audio_playback",
                    command_template=[
                        "python",
                        audio_script,
                        "play",
                        "--audio-file",
                        "{audio_file}",
                        "--repeat",
                        "{repeat}",
                    ]
                    + (["--device-key", audio_key] if audio_key else []),
                    side_effect=True,
                    resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
                ),
                AdapterAction(
                    name="play_low_latency",
                    kind="audio_playback",
                    command_template=low_latency_cmd,
                    side_effect=True,
                    resources=[f"audio:{audio_key or 'DEFAULT_RENDER_DEVICE'}"],
                ),
            ],
            config={"device_key": audio_key or "DEFAULT_RENDER_DEVICE", "volume": _nested(env_payload, "audio", "playback_volume")},
        )
    )

    wifi_ssid = _text(_nested(env_payload, "network", "wifi_ssid"))
    hotspot_enabled = bool(_nested(env_payload, "network", "enable_hotspot_control"))
    adapters.append(
        DeviceAdapter(
            adapter_id="network.local",
            kind="network",
            status="available" if wifi_ssid or hotspot_enabled else "config_required",
            resources=["network:wifi"],
            capabilities=(["wifi_config"] if wifi_ssid else []) + (["hotspot_control"] if hotspot_enabled else []),
            actions=[
                AdapterAction(
                    name="hotspot_status",
                    kind="network_query",
                    command_template=["python", "tools/device/polaris_network_orchestrator.py", "hotspot-status"],
                    side_effect=False,
                    resources=["network:wifi"],
                ),
                AdapterAction(
                    name="hotspot_on",
                    kind="network_control",
                    command_template=["python", "tools/device/polaris_network_orchestrator.py", "hotspot-set", "--enable", "1"],
                    side_effect=True,
                    resources=["network:wifi"],
                ),
                AdapterAction(
                    name="hotspot_off",
                    kind="network_control",
                    command_template=["python", "tools/device/polaris_network_orchestrator.py", "hotspot-set", "--enable", "0"],
                    side_effect=True,
                    resources=["network:wifi"],
                ),
                AdapterAction(
                    name="hotspot_cycle",
                    kind="network_control",
                    command_template=["python", "tools/device/polaris_network_orchestrator.py", "hotspot-cycle"],
                    side_effect=True,
                    resources=["network:wifi"],
                ),
                AdapterAction(
                    name="hotspot_cycle_window",
                    kind="network_control",
                    command_template=[
                        "python",
                        "tools/device/polaris_network_orchestrator.py",
                        "hotspot-cycle",
                        "--off-wait",
                        "{off_wait}",
                        "--on-wait",
                        "{on_wait}",
                        "--output-dir",
                        "{output_dir}",
                    ],
                    side_effect=True,
                    resources=["network:wifi"],
                ),
                AdapterAction(
                    name="ensure_online",
                    kind="network_control",
                    command_template=[
                        "python",
                        "tools/device/polaris_network_orchestrator.py",
                        "ensure-online",
                        "--ssid",
                        "{ssid}",
                        "--pwd",
                        "{pwd}",
                    ],
                    side_effect=True,
                    resources=["network:wifi"],
                ),
                AdapterAction(
                    name="ensure_online_window",
                    kind="network_control",
                    command_template=[
                        "python",
                        "tools/device/polaris_network_orchestrator.py",
                        "ensure-online",
                        "--ssid",
                        "{ssid}",
                        "--pwd",
                        "{pwd}",
                        "--verify-wait",
                        "{verify_wait}",
                        "--label",
                        "{label}",
                        "--output-dir",
                        "{output_dir}",
                    ],
                    side_effect=True,
                    resources=["network:wifi"],
                ),
            ],
            config={"wifi_ssid": wifi_ssid, "enable_hotspot_control": hotspot_enabled},
            warnings=[] if wifi_ssid or hotspot_enabled else ["wifi_ssid and hotspot control are not configured"],
        )
    )

    api_env = _text(_nested(env_payload, "cloud", "api_environment"))
    device_env = _text(_nested(env_payload, "cloud", "device_env"))
    adapters.append(
        DeviceAdapter(
            adapter_id="cloud.api",
            kind="cloud",
            status="available" if api_env else "config_required",
            resources=[f"cloud:{api_env or 'UNKNOWN'}"],
            capabilities=["cloud_api", "device_env_switch"] if api_env else [],
            actions=[
                AdapterAction(
                    name="probe_device",
                    kind="cloud_api",
                    command_template=["python", "tools/cloud/polaris_app_control.py", "--env-file", "{env_file}", "probe-device"],
                    side_effect=False,
                    resources=[f"cloud:{api_env}"],
                ),
                AdapterAction(
                    name="check_env_gate",
                    kind="cloud_api_preflight",
                    command_template=["python", "tools/cloud/polaris_app_control.py", "--env-file", "{env_file}", "check-env"],
                    side_effect=False,
                    resources=[f"serial:{_text(ports.get('ap'))}", f"cloud:{api_env}"],
                    notes=["云控 API 前必须确认设备端 env 与 cloud.api_environment 一致；失败时阻断 API。"],
                ),
                AdapterAction(
                    name="set_full_duplex",
                    kind="cloud_api",
                    command_template=["python", "tools/cloud/polaris_app_control.py", "--env-file", "{env_file}", "set-full-duplex", "--enable", "{enable}", "--timeout", "{timeout}"],
                    side_effect=True,
                    resources=[f"cloud:{api_env}"],
                ),
                AdapterAction(
                    name="set_volume",
                    kind="cloud_api",
                    command_template=["python", "tools/cloud/polaris_app_control.py", "--env-file", "{env_file}", "set-volume", "--value", "{value}"],
                    side_effect=True,
                    resources=[f"cloud:{api_env}"],
                ),
                AdapterAction(
                    name="set_multi_wakeup",
                    kind="cloud_api",
                    command_template=["python", "tools/cloud/polaris_app_control.py", "--env-file", "{env_file}", "set-multi-wakeup", "--enable", "{enable}"],
                    side_effect=True,
                    resources=[f"cloud:{api_env}"],
                ),
                AdapterAction(
                    name="set_night_mode",
                    kind="cloud_api",
                    command_template=[
                        "python",
                        "tools/cloud/polaris_app_control.py",
                        "--env-file",
                        "{env_file}",
                        "set-night-mode",
                        "--enable",
                        "{enable}",
                        "--time-from",
                        "{time_from}",
                        "--time-to",
                        "{time_to}",
                        "--volume",
                        "{volume}",
                        "--awake-threshold",
                        "{awake_threshold}",
                    ],
                    side_effect=True,
                    resources=[f"cloud:{api_env}"],
                ),
                AdapterAction(
                    name="set_wakeup_word",
                    kind="cloud_api",
                    command_template=["python", "tools/cloud/polaris_app_control.py", "--env-file", "{env_file}", "set-wakeup-word", "--word", "{word}"],
                    side_effect=True,
                    resources=[f"cloud:{api_env}"],
                ),
                AdapterAction(
                    name="set_wakeup_threshold",
                    kind="cloud_api",
                    command_template=["python", "tools/cloud/polaris_app_control.py", "--env-file", "{env_file}", "set-wakeup-threshold", "--threshold", "{threshold}"],
                    side_effect=True,
                    resources=[f"cloud:{api_env}"],
                ),
                AdapterAction(
                    name="proactive_interaction",
                    kind="cloud_api",
                    command_template=["python", "tools/cloud/polaris_app_control.py", "--env-file", "{env_file}", "proactive-interaction", "--interrupt", "--tts-long"],
                    side_effect=True,
                    resources=[f"cloud:{api_env}"],
                ),
            ]
            if api_env
            else [],
            config={
                "api_environment": api_env,
                "device_env": device_env,
                "device_env_command": _nested(env_payload, "cloud", "device_env_command"),
                "device_env_reboot_required": bool(_nested(env_payload, "cloud", "device_env_reboot_required")),
            },
            warnings=[] if api_env else ["cloud api_environment is empty"],
        )
    )

    adapters.append(
        DeviceAdapter(
            adapter_id="snapshot.runtime",
            kind="snapshot",
            status="available",
            resources=[],
            capabilities=["snapshot_capture", "snapshot_diff", "snapshot_restore_check"],
            actions=[
                AdapterAction(
                    name="capture_before",
                    kind="snapshot_capture",
                    command_template=[
                        "python",
                        "satellite/cucumber-agent-testing/scripts/run_snapshot.py",
                        "capture-before",
                        "--env-file",
                        "{env_file}",
                        "--run-dir",
                        "{run_dir}",
                        "--out",
                        "{out}",
                    ],
                    side_effect=False,
                    notes=["Capture evidence-only before snapshot with env/config and available artifacts."],
                ),
                AdapterAction(
                    name="capture_after",
                    kind="snapshot_capture",
                    command_template=[
                        "python",
                        "satellite/cucumber-agent-testing/scripts/run_snapshot.py",
                        "capture-after",
                        "--env-file",
                        "{env_file}",
                        "--run-dir",
                        "{run_dir}",
                        "--out",
                        "{out}",
                    ],
                    side_effect=False,
                    notes=["Capture evidence-only after snapshot from run artifacts."],
                ),
                AdapterAction(
                    name="diff",
                    kind="snapshot_diff",
                    command_template=[
                        "python",
                        "satellite/cucumber-agent-testing/scripts/run_snapshot.py",
                        "diff",
                        "--before",
                        "{before}",
                        "--after",
                        "{after}",
                        "--out",
                        "{out}",
                    ],
                    side_effect=False,
                    notes=["Produce changed/unchanged/unknown diff with attribution hints."],
                ),
                AdapterAction(
                    name="restore_check",
                    kind="snapshot_restore_check",
                    command_template=[
                        "python",
                        "satellite/cucumber-agent-testing/scripts/run_snapshot.py",
                        "restore-check",
                        "--before",
                        "{before}",
                        "--after",
                        "{after}",
                        "--out",
                        "{out}",
                    ],
                    side_effect=False,
                    notes=["Check whether recovery restored observed snapshot state without fabricating missing fields."],
                ),
            ],
            config={"schema": "polaris.snapshot.v1"},
        )
    )

    warnings = [warning for adapter in adapters for warning in adapter.warnings]
    return AdapterRegistry(project_id=project_id, project_type=project_type, adapters=adapters, warnings=warnings)
