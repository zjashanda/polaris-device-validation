#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Register reviewed failure-to-test-case candidates into deterministic assets."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
WORKSPACE_ROOT = SCRIPT_DIR.parents[2]

TYPE_TO_BASE_TASK = {
    "environment_precondition_guard": "satellite/cucumber-agent-testing/tasks/examples/attribution_validator.example.json",
    "timing_boundary_regression": "satellite/cucumber-agent-testing/tasks/examples/wake_latency.example.json",
    "media_response_regression": "satellite/cucumber-agent-testing/tasks/examples/online_full_duplex.media_interrupt.example.json",
    "stability_regression": "satellite/cucumber-agent-testing/tasks/examples/online_mixed_stress.example.json",
    "functional_regression": "satellite/cucumber-agent-testing/tasks/examples/basic_command.example.json",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def resolve_path(value: str, root: Path = WORKSPACE_ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


def rel(path: Path, root: Path = WORKSPACE_ROOT) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_id(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_\-]+", "_", str(value or "case")).strip("_").lower()
    return text or "case"


def select_candidates(package: dict[str, Any], only_id: str = "") -> list[dict[str, Any]]:
    candidates = package.get("candidates", []) if isinstance(package.get("candidates"), list) else []
    result = [item for item in candidates if isinstance(item, dict)]
    if only_id:
        result = [item for item in result if str(item.get("id", "")) == only_id]
    return result


def base_task_for(candidate: dict[str, Any], workspace_root: Path) -> tuple[Path, dict[str, Any]]:
    ctype = str(candidate.get("candidate_type") or "functional_regression")
    rel_path = TYPE_TO_BASE_TASK.get(ctype, TYPE_TO_BASE_TASK["functional_regression"])
    path = resolve_path(rel_path, workspace_root)
    payload = load_json(path)
    if not payload:
        raise RuntimeError(f"base task not found or invalid: {rel_path}")
    return path, payload


def build_generated_task(candidate: dict[str, Any], base_task: dict[str, Any], base_task_rel: str, approved_by: str) -> dict[str, Any]:
    case_id = str(candidate.get("id") or "generated")
    task = json.loads(json.dumps(base_task, ensure_ascii=False))
    task["task_id"] = "regression_" + safe_id(case_id)
    task["status"] = "approved_generated_regression"
    task["description"] = f"失败回归复测：{candidate.get('title', case_id)}"
    runner = task.setdefault("runner", {})
    runner["mode"] = "dry-run"
    runner.setdefault("feature", "satellite/cucumber-agent-testing/features/polaris_voice_core.feature")
    runner.setdefault("mapping", "satellite/cucumber-agent-testing/references/voice_core_mapping.json")
    task.setdefault("environment", {})["env_file"] = "polaris.local.json"
    task.setdefault("execution", {})["allow_side_effects"] = False
    task.setdefault("execution", {})["manage_session"] = True
    inputs = task.setdefault("inputs", {})
    inputs["source_failure_case_id"] = case_id
    inputs["source_failure_scenario_id"] = candidate.get("scenario_id", "")
    inputs["source_failure_result"] = candidate.get("result", "")
    inputs["source_failure_attribution"] = candidate.get("attribution", "")
    task["registry_binding"] = {
        "registered_at": now_iso(),
        "approved_by": approved_by,
        "candidate_type": candidate.get("candidate_type", ""),
        "base_task": base_task_rel,
        "source_run": candidate.get("source_run", ""),
        "run_dir": candidate.get("run_dir", ""),
        "review_policy": "candidate must be approved before writing workspace assets",
    }
    task["expected"] = {
        "summary": "复测时不应再次出现同一失败结果/归因；若再次出现，必须保留完整证据并进入问题闭环。",
        "source_reason": candidate.get("reason", ""),
        "failure_attribution": [
            "前置缺失或云控/声卡/串口问题保持 BLOCKED，不判固件 FAIL。",
            "前置满足且业务证据仍不满足时，才进入固件/设备/ASR/云端功能问题。",
            "时序不安全或临界窗口不可证明时，输出 TIMING_AMBIGUOUS。",
        ],
    }
    return task


def render_failure_doc(candidate: dict[str, Any], task_rel: str, approved_by: str) -> str:
    suggestions = candidate.get("suggested_assertion_updates", []) if isinstance(candidate.get("suggested_assertion_updates"), list) else []
    rules = candidate.get("suggested_rule_updates", []) if isinstance(candidate.get("suggested_rule_updates"), list) else []
    lines = [
        f"# {candidate.get('id', '')} 失败回归模式",
        "",
        f"- 注册时间：`{now_iso()}`",
        f"- 审核人：`{approved_by}`",
        f"- 类型：`{candidate.get('candidate_type', '')}`",
        f"- 原场景：`{candidate.get('scenario_id', '')}`",
        f"- 原结果：`{candidate.get('result', '')}` / `{candidate.get('attribution', '')}`",
        f"- source_run：`{candidate.get('source_run', '')}`",
        f"- run_dir：`{candidate.get('run_dir', '')}`",
        f"- 生成 task：`{task_rel}`",
        "",
        "## 失败原因摘录",
        "",
        str(candidate.get("reason", "")),
        "",
        "## 候选 Gherkin",
        "",
        "```gherkin",
        str(candidate.get("gherkin", "")).rstrip(),
        "```",
        "",
        "## 断言/规则建议",
        "",
    ]
    for item in suggestions:
        lines.append(f"- `{item.get('target', '')}`：{item.get('suggestion', '')}")
    for item in rules:
        lines.append(f"- `{item.get('target', '')}`：{item.get('suggestion', '')}")
    lines.extend(
        [
            "",
            "## 执行方式",
            "",
            "```powershell",
            f"python satellite\\cucumber-agent-testing\\scripts\\run_optimized_task.py --task {task_rel} --precheck-only",
            f"python satellite\\cucumber-agent-testing\\scripts\\run_optimized_task.py --task {task_rel} --print-command",
            "```",
            "",
            "真机 execute 前需要人工确认副作用和设备配置，再追加 `--allow-side-effects` 等参数。",
            "",
        ]
    )
    return "\n".join(lines)


def load_registry(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload:
        payload.setdefault("cases", [])
        return payload
    return {"schema": "polaris.failure_regression_registry.v1", "updated_at": now_iso(), "cases": []}


def upsert_case(cases: list[dict[str, Any]], record: dict[str, Any]) -> None:
    for index, item in enumerate(cases):
        if item.get("id") == record.get("id"):
            cases[index] = record
            return
    cases.append(record)


def load_scene(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload:
        payload.setdefault("nodes", [])
        return payload
    return {
        "schema": "polaris.scene_graph.v1",
        "scene_id": "generated_failure_regression_approved",
        "strategy_name": "failure_regression_reviewed",
        "seed": 0,
        "constraints": ["task_examples_exist", "reviewed_failure_candidates_only"],
        "nodes": [],
    }


def upsert_node(nodes: list[dict[str, Any]], node: dict[str, Any]) -> None:
    for index, item in enumerate(nodes):
        if item.get("node_id") == node.get("node_id"):
            nodes[index] = node
            return
    nodes.append(node)


def build_registration_plan(candidates: list[dict[str, Any]], workspace_root: Path, approved_by: str) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for candidate in candidates:
        base_path, base_payload = base_task_for(candidate, workspace_root)
        case_safe = safe_id(str(candidate.get("id", "")))
        task_rel = f"satellite/cucumber-agent-testing/tasks/generated/regression/{case_safe}.example.json"
        doc_rel = f"docs/wiki/voice-validation/failure-patterns/{case_safe}.md"
        generated_task = build_generated_task(candidate, base_payload, rel(base_path, workspace_root), approved_by)
        plan.append(
            {
                "candidate": candidate,
                "case_safe": case_safe,
                "task_rel": task_rel,
                "doc_rel": doc_rel,
                "registry_rel": "satellite/cucumber-agent-testing/references/failure_regression_registry.json",
                "scene_rel": "satellite/cucumber-agent-testing/references/scenes/generated_failure_regression.scene.example.json",
                "task": generated_task,
            }
        )
    return plan


def write_registration(plan: list[dict[str, Any]], workspace_root: Path, approved_by: str) -> dict[str, Any]:
    registry_path = resolve_path("satellite/cucumber-agent-testing/references/failure_regression_registry.json", workspace_root)
    scene_path = resolve_path("satellite/cucumber-agent-testing/references/scenes/generated_failure_regression.scene.example.json", workspace_root)
    registry = load_registry(registry_path)
    scene = load_scene(scene_path)
    written: list[str] = []
    for item in plan:
        candidate = item["candidate"]
        task_path = resolve_path(item["task_rel"], workspace_root)
        doc_path = resolve_path(item["doc_rel"], workspace_root)
        write_json(task_path, item["task"])
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(render_failure_doc(candidate, item["task_rel"], approved_by), encoding="utf-8")
        node_id = "REG-" + item["case_safe"][:48]
        node = {
            "node_id": node_id,
            "action": "voice_interaction",
            "task": item["task_rel"],
            "category": "generated_failure_regression",
            "command_text": str(candidate.get("metrics", {}).get("command_text", "") if isinstance(candidate.get("metrics"), dict) else "") or str(candidate.get("title", "")),
            "mode": "dry-run",
            "depends_on": [],
            "metadata": {
                "case_id": candidate.get("id", ""),
                "candidate_type": candidate.get("candidate_type", ""),
                "source_scenario_id": candidate.get("scenario_id", ""),
                "approved_by": approved_by,
            },
        }
        upsert_node(scene.setdefault("nodes", []), node)
        record = {
            "id": candidate.get("id", ""),
            "title": candidate.get("title", ""),
            "candidate_type": candidate.get("candidate_type", ""),
            "source_run": candidate.get("source_run", ""),
            "run_dir": candidate.get("run_dir", ""),
            "result": candidate.get("result", ""),
            "attribution": candidate.get("attribution", ""),
            "registered_at": now_iso(),
            "approved_by": approved_by,
            "task": item["task_rel"],
            "doc": item["doc_rel"],
            "scene": item["scene_rel"],
            "scene_node_id": node_id,
        }
        upsert_case(registry.setdefault("cases", []), record)
        written.extend([item["task_rel"], item["doc_rel"]])
    registry["updated_at"] = now_iso()
    scene["updated_at"] = now_iso()
    write_json(registry_path, registry)
    write_json(scene_path, scene)
    written.extend([rel(registry_path, workspace_root), rel(scene_path, workspace_root)])
    return {"written": written, "registry": rel(registry_path, workspace_root), "scene": rel(scene_path, workspace_root)}


def render_preview(plan: list[dict[str, Any]], approve: bool) -> str:
    lines = ["# Failure-to-Test-Case 注册预览", "", f"- approve：`{approve}`", f"- candidate_count：`{len(plan)}`", ""]
    for item in plan:
        cand = item["candidate"]
        lines.extend(
            [
                f"## {cand.get('id', '')} {cand.get('title', '')}",
                f"- task：`{item['task_rel']}`",
                f"- doc：`{item['doc_rel']}`",
                f"- registry：`{item['registry_rel']}`",
                f"- scene：`{item['scene_rel']}`",
                f"- base_task：`{item['task'].get('registry_binding', {}).get('base_task', '')}`",
                "",
            ]
        )
    if not approve:
        lines.append("未加 `--approve`，本次只生成预览，不写入 workspace registry/task/scene。")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Register reviewed failure case candidates.")
    parser.add_argument("--package", required=True, help="failure_case_package.json generated by generate_failure_case.py")
    parser.add_argument("--candidate-id", default="")
    parser.add_argument("--approve", action="store_true", help="Write registry/task/scene/wiki assets after review")
    parser.add_argument("--approved-by", default="")
    parser.add_argument("--workspace-root", default=str(WORKSPACE_ROOT), help="Target workspace root; use debug temp root for smoke tests")
    parser.add_argument("--out-dir", default="", help="Preview output directory")
    args = parser.parse_args()

    workspace_root = resolve_path(args.workspace_root)
    package_path = resolve_path(args.package)
    package = load_json(package_path)
    candidates = select_candidates(package, args.candidate_id)
    if not candidates:
        raise SystemExit("no candidates selected")
    if args.approve and not args.approved_by.strip():
        raise SystemExit("--approved-by is required when --approve is used")
    approved_by = args.approved_by.strip() or "review_pending"
    plan = build_registration_plan(candidates, workspace_root, approved_by)
    preview = {
        "schema": "polaris.failure_registration_preview.v1",
        "generated_at": now_iso(),
        "package": rel(package_path),
        "approve": bool(args.approve),
        "approved_by": approved_by,
        "workspace_root": str(workspace_root),
        "candidate_count": len(plan),
        "items": [{key: item[key] for key in ("case_safe", "task_rel", "doc_rel", "registry_rel", "scene_rel")} for item in plan],
    }
    if args.approve:
        preview["write_result"] = write_registration(plan, workspace_root, approved_by)
        preview["result"] = "REGISTERED"
    else:
        preview["result"] = "REVIEW_REQUIRED"
    out_dir = resolve_path(args.out_dir) if args.out_dir else (BDD_ROOT / "debug" / "failure_case_registration" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    write_json(out_dir / "registration_preview.json", preview)
    (out_dir / "registration_preview.md").write_text(render_preview(plan, args.approve), encoding="utf-8")
    print(out_dir)
    print(f"result={preview['result']} candidate_count={len(plan)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
