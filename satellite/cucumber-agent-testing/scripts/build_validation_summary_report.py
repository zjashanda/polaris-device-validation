#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a user-facing validation summary from run/optimized/kernel directories."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_OUT_ROOT = BDD_ROOT / "debug" / "reports"
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

try:
    from analyze_media_response_oracle import analyze_run as analyze_media_run  # type: ignore
except Exception:
    analyze_media_run = None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def summary_result(summary: Dict[str, Any]) -> str:
    counts = summary.get("overall_counts", {}) if isinstance(summary.get("overall_counts"), dict) else {}
    if not counts:
        counts = summary.get("counts", {}) if isinstance(summary.get("counts"), dict) else {}
    for result in ["FAIL", "BLOCKED", "TIMING_AMBIGUOUS", "REQUIREMENT_REVIEW", "PASS"]:
        if counts.get(result):
            return result
    return "UNKNOWN"


def project_from_record(record: Dict[str, Any], bdd_summary: Dict[str, Any], kernel_record: Dict[str, Any]) -> str:
    if kernel_record.get("project_id"):
        return str(kernel_record.get("project_id"))
    preflight = record.get("preflight", {}) if isinstance(record.get("preflight"), dict) else {}
    for item in preflight.get("constraints", []) if isinstance(preflight.get("constraints"), list) else []:
        if isinstance(item, dict) and item.get("name") == "project_selected":
            actual = item.get("actual", {}) if isinstance(item.get("actual"), dict) else {}
            if actual.get("project_id"):
                return str(actual.get("project_id"))
    for item in bdd_summary.get("scenario_results", []) if isinstance(bdd_summary.get("scenario_results"), list) else []:
        runtime = item.get("runtime_replay", {}) if isinstance(item.get("runtime_replay"), dict) else {}
        if runtime.get("project"):
            return str(runtime.get("project"))
    return ""


def collect_evidence(run_dir: Path, scenario_results: List[Dict[str, Any]]) -> List[str]:
    evidence: List[str] = []
    for item in scenario_results:
        value = str(item.get("evidence_path", "") or "")
        if value and value not in evidence:
            evidence.append(value)
        runtime = item.get("runtime_replay", {}) if isinstance(item.get("runtime_replay"), dict) else {}
        for key in ["report_path", "replay_dir"]:
            value = str(runtime.get(key, "") or "")
            if value and value not in evidence:
                evidence.append(value)
    for candidate in [run_dir / "bdd_run_report.md", run_dir / "kernel_record.json", run_dir / "kernel_scene_report.md", run_dir / "media_oracle" / "media_response_oracle.md"]:
        if candidate.exists():
            text = rel(candidate)
            if text not in evidence:
                evidence.insert(0, text)
    for candidate in [run_dir / "snapshot_before.json", run_dir / "snapshot_after.json", run_dir / "snapshot_diff.json", run_dir / "snapshot_coverage.json"]:
        if candidate.exists():
            text = rel(candidate)
            if text not in evidence:
                evidence.append(text)
    return evidence


def runtime_event_counts(runtime_summary: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for sidecar in runtime_summary.values() if isinstance(runtime_summary, dict) else []:
        if not isinstance(sidecar, dict):
            continue
        for key, value in (sidecar.get("event_counts", {}) or {}).items():
            try:
                counts[key] = counts.get(key, 0) + int(value or 0)
            except Exception:
                pass
    return counts


def load_media_oracle(run_dir: Path) -> Dict[str, Any]:
    existing = load_json(run_dir / "media_oracle" / "media_response_oracle.json")
    if existing:
        return existing
    if analyze_media_run is None:
        return {"result": "SKIPPED", "reason": "media oracle module unavailable"}
    try:
        return analyze_media_run(run_dir)
    except Exception as exc:
        return {"result": "ERROR", "reason": str(exc), "attribution": "media_oracle_error"}


def skipped_media_oracle(reason: str) -> Dict[str, Any]:
    return {"result": "SKIPPED", "attribution": "not_applicable", "reason": reason}


def load_acoustic_oracle(run_dir: Path) -> Dict[str, Any]:
    candidates = [
        run_dir / "acoustic_oracle" / "acoustic_oracle.json",
        run_dir / "media_oracle" / "acoustic_oracle.json",
    ]
    for candidate in candidates:
        payload = load_json(candidate)
        if payload:
            return payload
    nested = sorted(run_dir.rglob("acoustic_oracle.json"), key=lambda item: item.stat().st_mtime)
    if nested:
        return load_json(nested[-1]) or {"result": "ERROR", "reason": f"cannot parse {nested[-1]}"}
    return {"result": "SKIPPED", "attribution": "not_configured", "reason": "未发现声学回采 oracle 产物；未配置 capture/loopback 时不能证明真实出声。"}


def load_snapshot_refs(path: Path, record: Dict[str, Any], bdd_summary: Dict[str, Any]) -> Dict[str, Any]:
    snapshots = record.get("snapshot") or record.get("snapshots") or bdd_summary.get("snapshots") or {}
    snapshots = dict(snapshots) if isinstance(snapshots, dict) else {}
    coverage = load_json(path / "snapshot_coverage.json")
    diff = load_json(path / "snapshot_diff.json")
    if coverage:
        snapshots.setdefault("coverage", rel(path / "snapshot_coverage.json"))
        snapshots.setdefault("coverage_summary", coverage)
    if diff:
        snapshots.setdefault("diff", rel(path / "snapshot_diff.json"))
        snapshots.setdefault("diff_summary", diff.get("summary", {}))
    for name in ["before", "after", "final"]:
        candidate = path / f"snapshot_{name}.json"
        if candidate.exists():
            snapshots.setdefault(name, rel(candidate))
    return snapshots


def normalize_run(value: str) -> Dict[str, Any]:
    path = resolve_path(value)
    record_path = first_existing(path / "execution_record.json")
    kernel_scene = load_json(path / "kernel_scene_record.json")
    if record_path is None and not kernel_scene:
        nested_records = sorted(path.rglob("execution_record.json"), key=lambda item: item.stat().st_mtime)
        record_path = nested_records[-1] if nested_records else None
    record = load_json(record_path) if record_path else {}
    attempts = record.get("attempts", []) if isinstance(record.get("attempts"), list) else []
    last_attempt = attempts[-1] if attempts else {}
    run_dir = resolve_path(last_attempt.get("run_dir", "")) if last_attempt.get("run_dir") else path
    bdd_summary = load_json(run_dir / "bdd_run_summary.json")
    runtime_summary = load_json(run_dir / "runtime_replay_summary.json")
    kernel_record = load_json(path / "kernel_record.json")
    scenario_results = bdd_summary.get("scenario_results", []) if isinstance(bdd_summary.get("scenario_results"), list) else []
    if kernel_scene and not scenario_results:
        scenario_results = [{"scenario_id": node.get("node_id", ""), "mapping_title": node.get("category", ""), "result": node.get("result", ""), "attribution": "kernel_scene", "reason": node.get("reason", ""), "metrics": {"command_text": node.get("command_text", "")}} for node in kernel_scene.get("nodes", []) if isinstance(node, dict)]
    mode = record.get("mode") or bdd_summary.get("mode", kernel_record.get("mode", ""))
    if kernel_scene and not record:
        media_oracle = skipped_media_oracle("scene 汇总目录不直接做媒体 oracle；媒体/TTS 需查看节点下真实 BDD run 或单独 media oracle 报告。")
        acoustic_oracle = {"result": "SKIPPED", "attribution": "not_applicable", "reason": "scene 汇总目录不直接做声学回采 oracle。"}
    elif mode and mode != "execute":
        media_oracle = skipped_media_oracle(f"{mode} 模式未执行真实媒体链路。")
        acoustic_oracle = {"result": "SKIPPED", "attribution": "not_applicable", "reason": f"{mode} 模式未执行真实声学链路。"}
    else:
        media_oracle = load_media_oracle(run_dir)
        acoustic_oracle = load_acoustic_oracle(run_dir)
    result = record.get("result") or kernel_record.get("result") or kernel_scene.get("result") or summary_result(bdd_summary)
    project_id = project_from_record(record, bdd_summary, kernel_record)
    if not project_id and kernel_scene:
        for node in kernel_scene.get("nodes", []) if isinstance(kernel_scene.get("nodes"), list) else []:
            node_record = load_json(Path(str(node.get("kernel_record", ""))))
            if node_record.get("project_id"):
                project_id = str(node_record.get("project_id"))
                break
    snapshots = load_snapshot_refs(path, record, bdd_summary)
    if not snapshots and run_dir != path:
        snapshots = load_snapshot_refs(run_dir, record, bdd_summary)
    return {"input": rel(path), "optimized": bool(record), "task_id": record.get("task_id", kernel_record.get("task_id", kernel_scene.get("scene_id", ""))), "project_id": project_id, "mode": mode, "result": result or "UNKNOWN", "stability": record.get("stability", ""), "run_dir": rel(run_dir), "record_path": rel(record_path) if record_path else "", "scenario_results": scenario_results, "runtime_summary": runtime_summary, "runtime_event_counts": runtime_event_counts(runtime_summary), "media_oracle": media_oracle, "acoustic_oracle": acoustic_oracle, "snapshots": snapshots, "evidence": collect_evidence(run_dir, scenario_results)}


def build_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    by_project: Dict[str, Dict[str, int]] = {}
    scenario_counts: Dict[str, Dict[str, int]] = {}
    media_counts: Dict[str, int] = {}
    acoustic_counts: Dict[str, int] = {}
    snapshot_coverage_values: List[float] = []
    stability = {"RebootDetected": 0, "CrashDetected": 0}
    non_pass: List[Dict[str, Any]] = []
    for item in items:
        result = str(item.get("result") or "UNKNOWN")
        project = str(item.get("project_id") or "unknown")
        counts[result] = counts.get(result, 0) + 1
        by_project.setdefault(project, {})[result] = by_project.setdefault(project, {}).get(result, 0) + 1
        media_result = str((item.get("media_oracle", {}) or {}).get("result", "UNKNOWN"))
        media_counts[media_result] = media_counts.get(media_result, 0) + 1
        acoustic_result = str((item.get("acoustic_oracle", {}) or {}).get("result", "UNKNOWN"))
        acoustic_counts[acoustic_result] = acoustic_counts.get(acoustic_result, 0) + 1
        snapshots = item.get("snapshots", {}) if isinstance(item.get("snapshots"), dict) else {}
        coverage_summary = snapshots.get("coverage_summary", {}) if isinstance(snapshots.get("coverage_summary"), dict) else {}
        try:
            snapshot_coverage_values.append(float(coverage_summary.get("coverage_ratio")))
        except Exception:
            pass
        events = item.get("runtime_event_counts", {}) if isinstance(item.get("runtime_event_counts"), dict) else {}
        for key in stability:
            stability[key] += int(events.get(key, 0) or 0)
        for scenario in item.get("scenario_results", []):
            sid = str(scenario.get("scenario_id") or "unknown")
            sres = str(scenario.get("result") or "UNKNOWN")
            scenario_counts.setdefault(sid, {})[sres] = scenario_counts.setdefault(sid, {}).get(sres, 0) + 1
            if sres != "PASS":
                non_pass.append({"run_dir": item.get("run_dir", ""), "scenario_id": sid, "result": sres, "attribution": scenario.get("attribution", ""), "reason": scenario.get("reason", "")})
    snapshot_coverage = {
        "run_count_with_snapshot": len(snapshot_coverage_values),
        "avg_coverage_ratio": round(sum(snapshot_coverage_values) / len(snapshot_coverage_values), 4) if snapshot_coverage_values else None,
        "min_coverage_ratio": min(snapshot_coverage_values) if snapshot_coverage_values else None,
        "max_coverage_ratio": max(snapshot_coverage_values) if snapshot_coverage_values else None,
    }
    return {"schema": "polaris.validation_summary_report.v2", "generated_at": now_iso(), "total": len(items), "counts": counts, "by_project": by_project, "scenario_counts": scenario_counts, "media_oracle_counts": media_counts, "acoustic_oracle_counts": acoustic_counts, "snapshot_coverage": snapshot_coverage, "stability_event_counts": stability, "non_pass_items": non_pass, "runs": items}


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def short(text: Any, limit: int = 180) -> str:
    value = str(text or "").replace("\n", " ").replace("|", "\\|")
    return value if len(value) <= limit else value[:limit - 3] + "..."


def recommended_actions(summary: Dict[str, Any]) -> List[str]:
    actions: List[str] = []
    if summary.get("non_pass_items"):
        actions.append("对 FAIL/BLOCKED/TIMING_AMBIGUOUS run 执行 generate_failure_case.py，生成回归候选用例和复测清单。")
    stability = summary.get("stability_event_counts", {}) if isinstance(summary.get("stability_event_counts"), dict) else {}
    if stability.get("RebootDetected") or stability.get("CrashDetected"):
        actions.append("存在重启/崩溃事件，优先分析 boot reason、watchdog、panic/assert marker。")
    media_counts = summary.get("media_oracle_counts", {}) if isinstance(summary.get("media_oracle_counts"), dict) else {}
    if media_counts.get("FAIL") or media_counts.get("PASS_WITH_WARNINGS"):
        actions.append("媒体响应存在失败或警告，复核 TTS/MediaStarted/MediaCompleted 和 HTTP/player 错误。")
    if not actions:
        actions.append("当前汇总未发现明确失败；可继续扩大轮次或切换项目复验。")
    return actions


def render_markdown(summary: Dict[str, Any]) -> str:
    lines = ["# Polaris 验证总报告", "", f"- 生成时间：`{summary.get('generated_at')}`", f"- 总 run 数：`{summary.get('total')}`", f"- 结果分布：`{json_text(summary.get('counts', {}))}`", f"- 媒体 Oracle 分布：`{json_text(summary.get('media_oracle_counts', {}))}`", f"- 声学 Oracle 分布：`{json_text(summary.get('acoustic_oracle_counts', {}))}`", f"- 快照覆盖：`{json_text(summary.get('snapshot_coverage', {}))}`", f"- 稳定性事件：`{json_text(summary.get('stability_event_counts', {}))}`", "", "## 按项目汇总", "", "| 项目 | 结果分布 |", "| --- | --- |"]
    for project, project_counts in sorted((summary.get("by_project", {}) or {}).items()):
        lines.append(f"| {project} | `{json_text(project_counts)}` |")
    lines += ["", "## 按场景汇总", "", "| 场景 | 结果分布 |", "| --- | --- |"]
    for scenario, counts in sorted((summary.get("scenario_counts", {}) or {}).items()):
        lines.append(f"| `{scenario}` | `{json_text(counts)}` |")
    lines += ["", "## 运行明细", "", "| 项目 | 任务/场景 | 模式 | 结果 | 稳定性 | 媒体 Oracle | 声学 Oracle | run_dir |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for item in summary.get("runs", []):
        media = item.get("media_oracle", {}) if isinstance(item.get("media_oracle"), dict) else {}
        acoustic = item.get("acoustic_oracle", {}) if isinstance(item.get("acoustic_oracle"), dict) else {}
        lines.append(f"| {item.get('project_id','')} | {item.get('task_id','')} | {item.get('mode','')} | `{item.get('result','')}` | `{item.get('stability','')}` | `{media.get('result','')}` | `{acoustic.get('result','')}` | `{item.get('run_dir','')}` |")
    lines += ["", "## 场景与证据", ""]
    for item in summary.get("runs", []):
        lines.append(f"### {item.get('project_id') or 'unknown'} / {item.get('task_id') or 'run'}")
        media = item.get("media_oracle", {}) if isinstance(item.get("media_oracle"), dict) else {}
        acoustic = item.get("acoustic_oracle", {}) if isinstance(item.get("acoustic_oracle"), dict) else {}
        snapshots = item.get("snapshots", {}) if isinstance(item.get("snapshots"), dict) else {}
        lines.append(f"- media_oracle：`{media.get('result','')}` / `{media.get('attribution','')}`，{short(media.get('reason',''))}")
        lines.append(f"- acoustic_oracle：`{acoustic.get('result','')}` / `{acoustic.get('attribution','')}`，{short(acoustic.get('reason',''))}")
        if snapshots:
            lines.append(f"- snapshot_diff：`{snapshots.get('diff','')}`")
            lines.append(f"- snapshot_coverage：`{json_text(snapshots.get('coverage_summary', {}))}`")
        for scenario in item.get("scenario_results", []):
            runtime = scenario.get("runtime_replay", {}) if isinstance(scenario.get("runtime_replay"), dict) else {}
            metrics = scenario.get("metrics", {}) if isinstance(scenario.get("metrics"), dict) else {}
            lines += ["", f"- scenario：`{scenario.get('scenario_id','')}`", f"- result：`{scenario.get('result','')}`", f"- attribution：`{scenario.get('attribution','')}`", f"- reason：{short(scenario.get('reason',''))}", f"- runtime：`{runtime.get('result','')}` events=`{runtime.get('event_count','')}`", f"- metrics：`{json_text(metrics)}`"]
        if item.get("evidence"):
            lines.append("- evidence:")
            for evidence in item.get("evidence", []):
                lines.append(f"  - `{evidence}`")
        lines.append("")
    if summary.get("non_pass_items"):
        lines += ["## 未通过/需复核项", "", "| run_dir | 场景 | 结果 | 归因 | 原因 |", "| --- | --- | --- | --- | --- |"]
        for item in summary.get("non_pass_items", []):
            lines.append(f"| `{item.get('run_dir')}` | `{item.get('scenario_id')}` | `{item.get('result')}` | `{item.get('attribution')}` | {short(item.get('reason'))} |")
        lines.append("")
    lines += ["## 下一步建议", ""]
    for action in recommended_actions(summary):
        lines.append(f"- {action}")
    lines += ["", "## 判定口径", "", "- `PASS`：真机动作、业务证据、Runtime 断言和稳定性均通过。", "- `FAIL`：前置有效但功能证据明确不满足，进入固件/设备/ASR/云端问题分析。", "- `BLOCKED`：串口、声卡、PA、联网、云环境或资料导致本轮不能有效判定。", "- `TIMING_AMBIGUOUS`：注入点或超时窗口不可证明，不直接判固件失败。", "- 媒体 Oracle v1 为日志/事件级判断；未配置 loopback/capture 时不声称真实声学出声。", "- 声学 Oracle 只证明回采声学信号达到阈值，不替代 ASR/语义/设备业务断言。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Polaris validation summary report.")
    parser.add_argument("--run", action="append", default=[])
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()
    if not args.run:
        raise SystemExit("请至少提供一个 --run")
    items = [normalize_run(value) for value in args.run]
    summary = build_summary(items)
    out_dir = resolve_path(args.out_dir) if args.out_dir else DEFAULT_OUT_ROOT / stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "validation_summary_report.json", summary)
    (out_dir / "validation_summary_report.md").write_text(render_markdown(summary), encoding="utf-8")
    print(out_dir)
    print(f"total={summary['total']} counts={summary['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
