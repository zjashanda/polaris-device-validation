#!/usr/bin/env python3
"""Run a basic offline -> online recovery validation.

The script intentionally keeps all artifacts in the Cucumber debug run and
uses existing Polaris helpers:
1. cycle Windows mobile hotspot off/on and collect serial evidence;
2. ensure the DUT is online again;
3. run one wake+online-query smoke to prove post-recovery voice path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.core.polaris_adapter_bridge import action_result_to_step, run_adapter_action_capture  # noqa: E402
from tools.validation.polaris_fa2_command_batch import run_command_batch  # noqa: E402


DEFAULT_DEVICE_KEY = ""
DEFAULT_WAKE_WORD = "小美小美"
DEFAULT_QUERY = "今天天气怎么样"


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_output_dir() -> Path:
    bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
    if bdd_run_dir:
        return Path(bdd_run_dir).resolve() / "network_recovery"
    return BASE / "debug" / "network_recovery" / datetime.now().strftime("%Y%m%d_%H%M%S")


def quote_cmd(cmd: List[str]) -> str:
    quoted: List[str] = []
    for arg in cmd:
        if not arg:
            quoted.append('""')
        elif any(ch.isspace() for ch in arg) or any(ch in arg for ch in ['"', "'", "&"]):
            quoted.append('"' + arg.replace('"', '\\"') + '"')
        else:
            quoted.append(arg)
    return " ".join(quoted)


def run_step(name: str, adapter_id: str, action: str, params: Dict[str, Any], output_dir: Path, timeout_s: int) -> Dict[str, Any]:
    log_path = output_dir / f"{name}.log"
    started_at = datetime.now()
    result = run_adapter_action_capture(
        adapter_id=adapter_id,
        action=action,
        params=params,
        timeout_s=timeout_s,
        execute=True,
        allow_side_effects=True,
        log_path=log_path,
    )
    step = action_result_to_step(name, result, started_at)
    step["log_path"] = rel(log_path)
    return step


def find_latest_fa2(output_dir: Path) -> Optional[Path]:
    roots = list(output_dir.glob("**/fa2_command_batch_summary.json"))
    if not roots:
        bdd_run_dir = os.environ.get("POLARIS_BDD_RUN_DIR", "").strip()
        if bdd_run_dir:
            roots = list((Path(bdd_run_dir).resolve() / "session" / "artifacts" / "misc" / "fa2").glob("*bdd_network_recovery_query*/fa2_command_batch_summary.json"))
    if not roots:
        return None
    return max(roots, key=lambda path: path.stat().st_mtime)


def network_window_online(window: Dict[str, Any]) -> bool:
    analysis = window.get("analysis", {})
    return any(
        int(analysis.get(key, 0) or 0) > 0
        for key in [
            "rssi_ok_count",
            "cloud_login_count",
            "keepalive_count",
            "route_info_upload_count",
            "heartbeat_count",
            "cloud_status_online_count",
        ]
    )


def summarize(
    output_dir: Path,
    *,
    cycle_dir: Path,
    ensure_dir: Path,
    fa2_summary_path: Optional[Path],
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    cycle_summary_path = cycle_dir / "summary.json"
    ensure_summary_path = ensure_dir / "summary.json"
    cycle = load_json(cycle_summary_path) if cycle_summary_path.exists() else {}
    ensure = load_json(ensure_summary_path) if ensure_summary_path.exists() else {}
    fa2 = load_json(fa2_summary_path) if fa2_summary_path and fa2_summary_path.exists() else {}

    off_state = str(cycle.get("after_stop_status", {}).get("operational_state", "")).lower()
    on_state = str(cycle.get("after_start_status", {}).get("operational_state", "")).lower()
    off_analysis = cycle.get("off_window", {}).get("analysis", {})
    on_window = cycle.get("on_window", {})
    offline_signal = off_state in {"off", "disabled"} or int(off_analysis.get("wifi_offline_count", 0) or 0) > 0
    hotspot_restarted = on_state == "on"
    ensure_success = bool(ensure.get("success"))
    recovery_online_evidence = bool(ensure.get("online_evidence")) or network_window_online(on_window)
    fa2_counts = dict(fa2.get("counts", {}))
    fa2_total = int(fa2.get("total", 0) or 0)
    online_query_pass = fa2_total > 0 and int(fa2_counts.get("PASS", 0) or 0) == fa2_total
    online_query_has_cloud = any(
        row.get("ap_online_asr_texts") or int(row.get("asr_total", 0) or 0) > 0
        for row in fa2.get("rows", [])
    ) or int(fa2_counts.get("PASS", 0) or 0) > 0

    if any(step["returncode"] != 0 for step in steps):
        result = "BLOCKED"
        attribution = "network_or_script_step_failed"
        reason = "断网/恢复/在线查询步骤存在非 0 返回码。"
    elif not hotspot_restarted:
        result = "BLOCKED"
        attribution = "windows_hotspot_recovery_failed"
        reason = "热点关闭后未能恢复到 On 状态。"
    elif not ensure_success:
        result = "BLOCKED"
        attribution = "device_not_online_after_recovery"
        reason = "ensure-online 未确认设备恢复在线。"
    elif not online_query_pass:
        result = "FAIL"
        attribution = "voice_online_path_after_recovery"
        reason = f"联网恢复后在线语音 smoke 未通过：{fa2_counts}。"
    else:
        result = "PASS"
        attribution = "pass"
        reason = "热点断开/恢复后，设备重新在线并完成在线语音 smoke。"

    if result == "PASS" and not offline_signal:
        attribution = "pass_with_limited_offline_serial_evidence"
        reason += " 断网串口证据较弱，但热点状态已发生 off/on。"

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result": result,
        "attribution": attribution,
        "reason": reason,
        "output_dir": rel(output_dir),
        "steps": steps,
        "checks": {
            "offline_signal": offline_signal,
            "hotspot_restarted": hotspot_restarted,
            "ensure_success": ensure_success,
            "recovery_online_evidence": recovery_online_evidence,
            "online_query_pass": online_query_pass,
            "online_query_has_cloud_or_asr": online_query_has_cloud,
        },
        "metrics": {
            "off_state": off_state,
            "on_state": on_state,
            "fa2_total": fa2_total,
            "fa2_counts": fa2_counts,
        },
        "evidence": {
            "cycle_summary": rel(cycle_summary_path) if cycle_summary_path.exists() else "",
            "ensure_summary": rel(ensure_summary_path) if ensure_summary_path.exists() else "",
            "fa2_summary": rel(fa2_summary_path) if fa2_summary_path else "",
        },
    }
    write_json(output_dir / "network_recovery_summary.json", payload)
    (output_dir / "network_recovery_report.md").write_text(render_report(payload), encoding="utf-8")
    return payload


def render_report(payload: Dict[str, Any]) -> str:
    lines = [
        "# 联网恢复基础验证报告",
        "",
        f"- 生成时间：`{payload.get('generated_at')}`",
        f"- 结论：`{payload.get('result')}`",
        f"- 归因：`{payload.get('attribution')}`",
        f"- 原因：{payload.get('reason')}",
        "",
        "## 检查项",
        "",
    ]
    for key, value in payload.get("checks", {}).items():
        lines.append(f"- `{key}`：`{value}`")
    lines.extend(["", "## 指标", ""])
    for key, value in payload.get("metrics", {}).items():
        lines.append(f"- `{key}`：`{json.dumps(value, ensure_ascii=False)}`")
    lines.extend(["", "## 证据", ""])
    for key, value in payload.get("evidence", {}).items():
        lines.append(f"- `{key}`：`{value}`")
    lines.extend(
        [
            "",
            "## 归因口径",
            "",
            "- 热点或设备未恢复在线：环境/前置 BLOCKED。",
            "- 恢复在线后在线语音 smoke 不通过：才归入恢复后在线语音链路问题。",
            "- 断网串口证据缺失但热点状态已 off/on 时，标记为弱证据，不直接判失败。",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run basic network recovery validation.")
    parser.add_argument("--ssid", default="")
    parser.add_argument("--pwd", default="")
    parser.add_argument("--device-key", default=DEFAULT_DEVICE_KEY)
    parser.add_argument("--wake-word", default=DEFAULT_WAKE_WORD)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--off-wait", type=float, default=15.0)
    parser.add_argument("--on-wait", type=float, default=45.0)
    parser.add_argument("--verify-wait", type=float, default=8.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    steps: List[Dict[str, Any]] = []
    cycle_dir = output_dir / "hotspot_cycle"
    ensure_dir = output_dir / "ensure_online"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    ensure_dir.mkdir(parents=True, exist_ok=True)
    steps.append(
        run_step(
            "01_hotspot_cycle",
            "network.local",
            "hotspot_cycle_window",
            {"off_wait": str(args.off_wait), "on_wait": str(args.on_wait), "output_dir": str(cycle_dir)},
            output_dir,
            timeout_s=int(args.off_wait + args.on_wait + 60),
        )
    )
    steps.append(
        run_step(
            "02_ensure_online",
            "network.local",
            "ensure_online_window",
            {
                "ssid": args.ssid,
                "pwd": args.pwd,
                "verify_wait": str(args.verify_wait),
                "label": "bdd_network_recovery_ensure_online",
                "output_dir": str(ensure_dir),
            },
            output_dir,
            timeout_s=160,
        )
    )
    query_file = output_dir / "online_query.txt"
    query_file.write_text(args.query.strip() + "\n", encoding="utf-8")
    query_started = datetime.now()
    fa2_batch = run_command_batch(
        command_file=query_file,
        wake_word=args.wake_word,
        device_key=args.device_key,
        limit=1,
        post_command_gap_ms=9000,
        label="bdd_network_recovery_query",
    )
    steps.append(
        {
            "name": "03_online_query_smoke",
            "cmd": ["adapter-only", "fa2_command_batch"],
            "returncode": int(fa2_batch.get("returncode", 0) or 0),
            "started_at": query_started.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "log_path": "",
            "stdout_tail": [str(fa2_batch.get("output_dir", ""))],
        }
    )
    payload = summarize(
        output_dir,
        cycle_dir=cycle_dir,
        ensure_dir=ensure_dir,
        fa2_summary_path=fa2_batch.get("summary_path") if isinstance(fa2_batch.get("summary_path"), Path) else find_latest_fa2(output_dir),
        steps=steps,
    )
    print(output_dir)
    print(json.dumps({"result": payload["result"], "attribution": payload["attribution"]}, ensure_ascii=False))
    return 0 if payload["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

