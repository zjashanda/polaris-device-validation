#!/usr/bin/env python3
"""Diagnose cloud-control readiness for Polaris projects."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import re
import sys
import time
from datetime import datetime
from typing import Any

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
else:
    ROOT = Path(__file__).resolve().parents[2]

import serial

from tools.cloud.polaris_app_control import (
    build_request,
    cloud_response_ok,
    direct_capture_deviceinfo,
    env_baudrate,
    env_ports,
    load_env_label,
    load_env_payload,
    resolve_env_file,
    response_to_dict,
    smart_decode,
)


ENV_CODE_TO_LABEL = {"0": "pro", "1": "uat", "2": "sit"}
PROJECT_VERSION_RE = re.compile(r"(?:Project|AP) Version:\s*(?P<version>\S+)")
FLASH_ENV_RE = re.compile(r"(?:^|\b)env=(?P<env>[0-2])\b")
FLASH_GET_ENV_RE = re.compile(r"flash get env:(?P<env>[0-2])\s+success")

PROJECT_CLOUD_GATES: dict[str, dict[str, list[str]]] = {
    "venusws63": {
        "authorized_versions": ["35.03.01.01.18.26.05.04.00.01"],
        "unauthorized_versions": ["35.03.01.01.18.26.05.04.00.02"],
    }
}


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def project_id_from_env_file(env_file: str) -> str:
    payload = read_json(resolve_env_file(env_file))
    active = str(payload.get("active_project") or payload.get("project_id") or "").strip()
    return active


def serial_query(port: str, baudrate: int, command: str, timeout_s: float) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="milliseconds")
    lines: list[str] = []
    partial = ""
    try:
        with serial.Serial(port, baudrate, timeout=0.2, write_timeout=1.0) as ser:
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            ser.write((command + "\r\n").encode("utf-8"))
            try:
                ser.flush()
            except Exception:
                pass
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                waiting = ser.in_waiting or 0
                if not waiting:
                    time.sleep(0.05)
                    continue
                partial += smart_decode(ser.read(waiting)).replace("\r", "")
                while "\n" in partial:
                    line, partial = partial.split("\n", 1)
                    clean = line.strip()
                    if clean:
                        lines.append(clean)
    except Exception as exc:  # pragma: no cover - depends on local serial ports
        return {
            "port": port,
            "baudrate": baudrate,
            "command": command,
            "started_at": started_at,
            "ended_at": datetime.now().isoformat(timespec="milliseconds"),
            "ok": False,
            "error": repr(exc),
            "lines": [],
        }
    if partial.strip():
        lines.append(partial.strip())
    return {
        "port": port,
        "baudrate": baudrate,
        "command": command,
        "started_at": started_at,
        "ended_at": datetime.now().isoformat(timespec="milliseconds"),
        "ok": True,
        "lines": lines,
    }


def extract_project_version(query_results: list[dict[str, Any]]) -> str:
    for result in query_results:
        for line in result.get("lines") or []:
            match = PROJECT_VERSION_RE.search(str(line))
            if match:
                return match.group("version").strip()
    return ""


def extract_flash_env(query_results: list[dict[str, Any]]) -> tuple[str, str]:
    for result in query_results:
        for line in result.get("lines") or []:
            text = str(line).strip()
            match = FLASH_ENV_RE.search(text) or FLASH_GET_ENV_RE.search(text)
            if match:
                code = match.group("env").strip()
                return code, ENV_CODE_TO_LABEL.get(code, "")
    return "", ""


def extract_business(response: dict[str, Any]) -> dict[str, Any]:
    text = str(response.get("text") or "").strip()
    if not text:
        return {"code": None, "msg": "", "payload": None}
    try:
        payload = json.loads(text)
    except Exception:
        return {"code": None, "msg": text[:300], "payload": None}
    nested = payload.get("result", {}).get("returnData", {}) if isinstance(payload.get("result"), dict) else {}
    if isinstance(nested, dict):
        return {"code": nested.get("code", payload.get("code")), "msg": nested.get("msg", ""), "payload": payload}
    return {"code": payload.get("code"), "msg": payload.get("msg", ""), "payload": payload}


def classify_version(project_id: str, version: str, env_payload: dict[str, Any]) -> dict[str, Any]:
    gates = dict(PROJECT_CLOUD_GATES.get(project_id, {}))
    cloud = env_payload.get("cloud", {}) if isinstance(env_payload.get("cloud"), dict) else {}
    configured_gate = cloud.get("version_gate", {}) if isinstance(cloud.get("version_gate"), dict) else {}
    for key in ("authorized_versions", "unauthorized_versions"):
        if isinstance(configured_gate.get(key), list):
            gates[key] = [str(item).strip() for item in configured_gate.get(key) if str(item).strip()]
    authorized = gates.get("authorized_versions", [])
    unauthorized = gates.get("unauthorized_versions", [])
    if not version:
        return {
            "name": "version_gate",
            "status": "WARN",
            "reason": "未从 AP version 命令解析到 Project/AP Version，不能确认云控授权版本。",
            "observed": version,
            "expected": authorized,
        }
    if version in unauthorized:
        return {
            "name": "version_gate",
            "status": "BLOCKED",
            "reason": f"当前版本 {version} 已知未获得后台 API 控制授权；需切换到已授权版本后再测云控。",
            "observed": version,
            "expected": authorized,
        }
    if authorized and version not in authorized:
        return {
            "name": "version_gate",
            "status": "WARN",
            "reason": f"当前版本 {version} 不在本地已知云控授权版本清单中，需确认后台是否放权。",
            "observed": version,
            "expected": authorized,
        }
    return {
        "name": "version_gate",
        "status": "PASS",
        "reason": "当前 Project/AP Version 命中本地已知云控授权版本清单。",
        "observed": version,
        "expected": authorized,
    }


def classify_env(expected: str, observed: str, observed_code: str, *, require_match: bool = False) -> dict[str, Any]:
    if not observed:
        status = "BLOCKED" if require_match else "WARN"
        return {
            "name": "device_env_match",
            "status": status,
            "reason": "未从 flash.show / flash.get.int env 解析到设备端环境；云端配置 API 前必须先确认设备环境，不能继续调用 API。" if require_match else "未从 flash.show / flash.get.int env 解析到设备端环境，需人工确认。",
            "api_environment": expected,
            "device_env": observed,
            "device_env_code": observed_code,
        }
    if expected and observed and expected != observed:
        return {
            "name": "device_env_match",
            "status": "BLOCKED",
            "reason": f"API 环境为 {expected}，设备端 env={observed_code}/{observed}，环境不一致会导致云控不生效。",
            "api_environment": expected,
            "device_env": observed,
            "device_env_code": observed_code,
        }
    return {
        "name": "device_env_match",
        "status": "PASS",
        "reason": "API 环境与设备端环境一致。",
        "api_environment": expected,
        "device_env": observed,
        "device_env_code": observed_code,
    }


def classify_cloud_response(response: dict[str, Any] | None, api_env: str = "", skipped_reason: str = "") -> dict[str, Any]:
    if response is None:
        return {
            "name": "cloud_control_probe",
            "status": "SKIPPED",
            "reason": skipped_reason or "未开启 --probe-cloud，本次只做串口侧环境/版本诊断。",
        }
    if cloud_response_ok(response):
        return {
            "name": "cloud_control_probe",
            "status": "PASS",
            "reason": "云端接口 HTTP 与业务码均成功。",
            "response": response,
        }
    business = extract_business(response)
    msg = str(business.get("msg") or "")
    code = str(business.get("code") or "")
    if code == "501" and "未登录过的设备" in msg:
        env_label = str(api_env or "目标环境").upper()
        reason = f"{env_label}/目标环境认为该 IoT ID 未登录过，优先检查设备是否切到该环境并完成联网注册。"
    elif code == "501" and "设备未上线" in msg:
        reason = "目标云端认为设备未上线；环境、IoT ID、设备在线态或后台版本授权仍不满足云控条件。"
    elif code == "501":
        reason = "云端返回 501 业务失败；按环境、IoT ID、版本授权、功能授权顺序排查。"
    else:
        reason = "云端返回非成功业务结果；不能归因固件功能失败，需先排查云控前置。"
    return {
        "name": "cloud_control_probe",
        "status": "BLOCKED",
        "reason": reason,
        "business_code": code,
        "business_msg": msg,
        "response": response,
    }


def overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = [str(item.get("status", "")) for item in checks]
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "FAIL" in statuses:
        return "FAIL"
    if "WARN" in statuses:
        return "PASS_WITH_WARNINGS"
    return "PASS"


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Polaris 云控诊断报告",
        "",
        f"- 项目：`{payload.get('project_id', '')}`",
        f"- 结果：`{payload.get('result', '')}`",
        f"- API 环境：`{payload.get('api_environment', '')}`",
        f"- 设备端环境：`{payload.get('device_env', '')}`（code=`{payload.get('device_env_code', '')}`）",
        f"- Project Version：`{payload.get('project_version', '')}`",
        f"- IoT ID：`{payload.get('deviceinfo', {}).get('iot_id', '')}`",
        "",
        "## 检查项",
        "",
    ]
    for check in payload.get("checks", []):
        lines.extend(
            [
                f"### {check.get('name', '')}",
                f"- 状态：`{check.get('status', '')}`",
                f"- 说明：{check.get('reason', '')}",
                "",
            ]
        )
    lines.extend(
        [
            "## 排查顺序",
            "",
            "1. 先看 `Project Version`：WS63 当前本地规则中 `.00.02` 为未授权版本，需切到 `.00.01`。",
            "2. 再看设备端 `env`：`env=1` 为 UAT，`env=2` 为 SIT，必须和 `cloud.api_environment` 一致。",
            "3. 再看 `deviceinfo` 的 IoT ID/IP：IoT ID 缺失或目标环境提示未登录，说明设备未在该环境注册/上线。",
            "4. 最后看云端业务码：HTTP 200 但业务 `code=501` 仍是前置阻塞，不能判固件功能 FAIL。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_cloud_probe(env_payload: dict[str, Any], deviceinfo: dict[str, str], enable: int, timeout: int) -> dict[str, Any]:
    request = build_request(deviceinfo, env_payload)
    response = request.fullDuplex_switch_new(onoroff=int(enable), timeOut=int(timeout))
    return response_to_dict(response)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose Polaris cloud-control prerequisites")
    parser.add_argument("--env-file", default="", help="Polaris local config; defaults to root polaris.local.json")
    parser.add_argument("--project-id", default="", help="Override project id for local version gate rules")
    parser.add_argument("--output-dir", default="", help="Directory for diagnostic artifacts")
    parser.add_argument("--probe-cloud", action="store_true", help="Also call full-duplex cloud API as a readiness probe")
    parser.add_argument("--enable", type=int, choices=[0, 1], default=1, help="Full-duplex probe switch value")
    parser.add_argument("--timeout", type=int, default=60, help="Full-duplex probe timeout")
    parser.add_argument("--serial-timeout", type=float, default=3.5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    env_payload = load_env_payload(args.env_file)
    ports = env_ports(env_payload)
    ap_port = ports.get("ap") or "COM14"
    baudrate = env_baudrate(env_payload)
    project_id = args.project_id or project_id_from_env_file(args.env_file) or str(env_payload.get("project_id") or "")
    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "satellite" / "cucumber-agent-testing" / "debug" / "cloud_diagnostics" / f"{now_stamp()}_{project_id or 'project'}"
    output_dir.mkdir(parents=True, exist_ok=True)

    serial_results = [
        serial_query(ap_port, baudrate, "version", args.serial_timeout),
        serial_query(ap_port, baudrate, "flash.show", args.serial_timeout),
        serial_query(ap_port, baudrate, "flash.get.int env", args.serial_timeout),
    ]
    for index, result in enumerate(serial_results):
        safe_cmd = str(result.get("command", "")).replace(" ", "_")
        (output_dir / f"{index:02d}_{ap_port}_{safe_cmd}.log").write_text(
            "\n".join(result.get("lines") or []) + "\n",
            encoding="utf-8",
        )

    try:
        deviceinfo_capture = direct_capture_deviceinfo(env_payload)
        deviceinfo = deviceinfo_capture.get("parsed", {})
    except Exception as exc:  # pragma: no cover - depends on local serial ports
        deviceinfo_capture = {"error": repr(exc), "lines": [], "parsed": {}}
        deviceinfo = {}

    project_version = extract_project_version(serial_results)
    device_env_code, device_env = extract_flash_env(serial_results)
    api_environment = load_env_label(env_payload)
    env_check = classify_env(api_environment, device_env, device_env_code, require_match=bool(args.probe_cloud))
    response = None
    cloud_error = ""
    cloud_skipped_reason = ""
    if args.probe_cloud and env_check.get("status") != "PASS":
        cloud_skipped_reason = "设备端 env 未通过门禁，已跳过云端配置 API 调用。"
    elif args.probe_cloud and not deviceinfo.get("iot_id"):
        cloud_skipped_reason = "deviceinfo 未解析到 IoT ID，已跳过云端配置 API 调用。"
    if args.probe_cloud and env_check.get("status") == "PASS" and deviceinfo.get("iot_id"):
        try:
            response = run_cloud_probe(env_payload, deviceinfo, args.enable, args.timeout)
        except Exception as exc:  # pragma: no cover - depends on network/cloud
            cloud_error = repr(exc)
            response = {
                "ok": False,
                "status_code": None,
                "elapsed_s": None,
                "text": "",
                "url": None,
                "error": cloud_error,
                "business_ok": False,
            }

    checks = [
        classify_version(project_id, project_version, env_payload),
        env_check,
        classify_cloud_response(response, api_environment, skipped_reason=cloud_skipped_reason),
    ]
    payload = {
        "generated_at": datetime.now().isoformat(timespec="milliseconds"),
        "project_id": project_id,
        "env_file": str(resolve_env_file(args.env_file)),
        "ap_port": ap_port,
        "baudrate": baudrate,
        "api_environment": api_environment,
        "device_env_code": device_env_code,
        "device_env": device_env,
        "project_version": project_version,
        "deviceinfo": deviceinfo,
        "deviceinfo_capture": deviceinfo_capture,
        "serial_results": serial_results,
        "cloud_probe_enabled": bool(args.probe_cloud),
        "cloud_probe_error": cloud_error,
        "cloud_response": response,
        "checks": checks,
        "result": overall_status(checks),
    }
    (output_dir / "cloud_diagnostics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(output_dir / "cloud_diagnostics.md", payload)
    print(json.dumps({"result": payload["result"], "artifact_dir": str(output_dir), "checks": checks}, ensure_ascii=False, indent=2))
    return 3 if payload["result"] == "BLOCKED" else 0


if __name__ == "__main__":
    sys.exit(main())
