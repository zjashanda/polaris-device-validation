#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validation IR MVP.

The IR is the deterministic object that a future Kernel/Scheduler can consume.
It is intentionally a wrapper around existing task/env/registry inputs rather
than a new execution engine.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .capability_runtime import build_capability_matrix
from .constraint_engine import evaluate_constraints
from .device_adapter import build_adapter_registry
from .resource_runtime import build_resource_snapshot


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
class ValidationIR:
    ir_id: str
    task_id: str
    project_id: str
    mode: str
    scenario_tag: str
    feature: str
    mapping: str
    constraints: Dict[str, Any]
    resources: Dict[str, Any]
    capabilities: Dict[str, Any]
    adapters: Dict[str, Any]
    execution: Dict[str, Any] = field(default_factory=dict)
    source: Dict[str, Any] = field(default_factory=dict)
    source_kind: str = "task"
    intent: str = ""
    preconditions: List[str] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    expect: List[Dict[str, Any]] = field(default_factory=list)
    timeout: Dict[str, Any] = field(default_factory=dict)
    retry: Dict[str, Any] = field(default_factory=dict)
    cleanup: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = "polaris.validation_ir.v1"
        return payload


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _list_of_text(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _expect_from_task(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    expected = task.get("expected", {}) if isinstance(task.get("expected"), dict) else {}
    assertions = task.get("assertions", []) if isinstance(task.get("assertions"), list) else []
    result: List[Dict[str, Any]] = []
    summary = _text(expected.get("summary"))
    if summary:
        result.append({"type": "summary", "text": summary, "owner": "task.expected"})
    for item in expected.get("assertions", []) if isinstance(expected.get("assertions"), list) else []:
        if isinstance(item, dict):
            result.append({"type": "assertion", **item, "owner": item.get("owner", "task.expected")})
        elif str(item).strip():
            result.append({"type": "assertion", "name": str(item).strip(), "owner": "task.expected"})
    for item in assertions:
        if isinstance(item, dict):
            result.append({"type": "assertion", **item, "owner": item.get("owner", "task.assertions")})
        elif str(item).strip():
            result.append({"type": "assertion", "name": str(item).strip(), "owner": "task.assertions"})
    return result


def _actions_from_task(task: Dict[str, Any], *, command_text: str = "") -> List[Dict[str, Any]]:
    runner = task.get("runner", {}) if isinstance(task.get("runner"), dict) else {}
    scenario = task.get("scenario", {}) if isinstance(task.get("scenario"), dict) else {}
    inputs = task.get("inputs", {}) if isinstance(task.get("inputs"), dict) else {}
    task_id = _text(task.get("task_id") or task.get("id") or "task")
    actions: List[Dict[str, Any]] = []
    if command_text:
        actions.append(
            {
                "id": "voice_interaction",
                "type": "voice_command",
                "text": command_text,
                "source": "scene_node.command_text",
            }
        )
    elif _text(inputs.get("command_text")):
        actions.append(
            {
                "id": "voice_interaction",
                "type": "voice_command",
                "text": _text(inputs.get("command_text")),
                "source": "task.inputs.command_text",
            }
        )
    elif _text(inputs.get("command_file")):
        actions.append(
            {
                "id": "command_batch",
                "type": "voice_command_file",
                "file": _text(inputs.get("command_file")),
                "limit": inputs.get("command_limit", ""),
                "source": "task.inputs.command_file",
            }
        )
    else:
        actions.append(
            {
                "id": "scenario_runner",
                "type": "cucumber_mapping",
                "scenario_tag": _text(scenario.get("tag")),
                "feature": _text(runner.get("feature")),
                "mapping": _text(runner.get("mapping")),
                "source": "task.runner",
            }
        )
    for index, item in enumerate(task.get("actions", []) if isinstance(task.get("actions"), list) else [], start=1):
        if isinstance(item, dict):
            actions.append({"id": item.get("id", f"task_action_{index:02d}"), **item, "source": item.get("source", "task.actions")})
    return actions


def _timeout_from_task(task: Dict[str, Any], *, observe_ms: str = "") -> Dict[str, Any]:
    execution = task.get("execution", {}) if isinstance(task.get("execution"), dict) else {}
    timeouts = task.get("timeouts", {}) if isinstance(task.get("timeouts"), dict) else {}
    result = dict(timeouts)
    if observe_ms:
        result["observe_ms"] = int(observe_ms) if str(observe_ms).isdigit() else observe_ms
    elif "observe_ms" in execution:
        result["observe_ms"] = execution.get("observe_ms")
    return result


def _retry_from_task(task: Dict[str, Any]) -> Dict[str, Any]:
    policy = task.get("policy", {}) if isinstance(task.get("policy"), dict) else {}
    retry = task.get("retry", {}) if isinstance(task.get("retry"), dict) else {}
    result = dict(retry)
    for key in ("max_retries", "retry_blocked", "retry_on"):
        if key in policy and key not in result:
            result[key] = policy.get(key)
    return result


def _task_ir_fields(task: Dict[str, Any], *, command_text: str = "", observe_ms: str = "") -> Dict[str, Any]:
    scenario = task.get("scenario", {}) if isinstance(task.get("scenario"), dict) else {}
    expected = task.get("expected", {}) if isinstance(task.get("expected"), dict) else {}
    execution = task.get("execution", {}) if isinstance(task.get("execution"), dict) else {}
    task_id = _text(task.get("task_id") or task.get("id") or "task")
    return {
        "intent": _text(task.get("intent") or scenario.get("tag") or task_id),
        "preconditions": _list_of_text(task.get("preconditions")) + _list_of_text(task.get("precondition")),
        "actions": _actions_from_task(task, command_text=command_text),
        "expect": _expect_from_task(task),
        "timeout": _timeout_from_task(task, observe_ms=observe_ms),
        "retry": _retry_from_task(task),
        "cleanup": [dict(item) for item in task.get("cleanup", []) if isinstance(item, dict)] if isinstance(task.get("cleanup"), list) else [],
        "metadata": {
            "tags": _list_of_text(task.get("tags")),
            "description": _text(task.get("description")),
            "failure_attribution": _list_of_text(expected.get("failure_attribution")),
            "manage_session": execution.get("manage_session", ""),
            "allow_side_effects": execution.get("allow_side_effects", ""),
        },
    }


def _merge_execution_overrides(task: Dict[str, Any], *, command_text: str = "", observe_ms: str = "") -> Dict[str, Any]:
    patched = deepcopy(task)
    if command_text:
        patched.setdefault("inputs", {})
        if isinstance(patched["inputs"], dict):
            patched["inputs"]["command_text"] = command_text
    if observe_ms:
        patched.setdefault("execution", {})
        if isinstance(patched["execution"], dict):
            patched["execution"]["observe_ms"] = int(observe_ms) if str(observe_ms).isdigit() else observe_ms
    return patched


def build_validation_ir(
    *,
    task: Dict[str, Any],
    env_payload: Dict[str, Any],
    task_path: str = "",
    env_file: str = "",
    mode: str = "",
    allow_side_effects: bool = False,
    tag: str = "",
    source_kind: str = "task",
    source_context: Optional[Dict[str, Any]] = None,
    command_text: str = "",
    observe_ms: str = "",
) -> ValidationIR:
    task_for_ir = _merge_execution_overrides(task, command_text=command_text, observe_ms=observe_ms)
    runner = task.get("runner", {}) if isinstance(task.get("runner"), dict) else {}
    scenario = task.get("scenario", {}) if isinstance(task.get("scenario"), dict) else {}
    execution = task_for_ir.get("execution", {}) if isinstance(task_for_ir.get("execution"), dict) else {}
    resolved_mode = _text(mode or runner.get("mode") or task.get("mode") or "dry-run")
    scenario_tag = _text(tag or scenario.get("tag"))
    task_id = _text(task.get("task_id") or task.get("id") or "task")
    project_id = _text(env_payload.get("project_id") or _nested(env_payload, "_config_source", "active_project"))
    constraints = evaluate_constraints(task=task_for_ir, env_payload=env_payload, mode=resolved_mode, allow_side_effects=allow_side_effects, tag=scenario_tag)
    resources = build_resource_snapshot(env_payload, task_for_ir).to_dict()
    capabilities = build_capability_matrix(env_payload).to_dict()
    adapters = build_adapter_registry(env_payload).to_dict()
    normalized = _task_ir_fields(task_for_ir, command_text=command_text, observe_ms=observe_ms)
    source = {"task_path": task_path, "env_file": env_file}
    if source_context:
        source.update(source_context)
    return ValidationIR(
        ir_id=f"{project_id}:{task_id}:{scenario_tag or 'all'}:{resolved_mode}",
        task_id=task_id,
        project_id=project_id,
        mode=resolved_mode,
        scenario_tag=scenario_tag,
        feature=_text(runner.get("feature")),
        mapping=_text(runner.get("mapping")),
        constraints=constraints,
        resources=resources,
        capabilities=capabilities,
        adapters=adapters,
        execution=dict(execution),
        source=source,
        source_kind=source_kind,
        **normalized,
    )


def _scene_node_observe_ms(node: Dict[str, Any]) -> str:
    metadata = node.get("metadata", {}) if isinstance(node.get("metadata"), dict) else {}
    observe_s = metadata.get("observe_s", "")
    if str(observe_s).strip() and str(observe_s).lstrip("-").isdigit():
        return str(max(0, int(observe_s)) * 1000)
    observe_ms = metadata.get("observe_ms", "")
    return str(observe_ms).strip() if str(observe_ms).strip() else ""


def build_scene_node_validation_ir(
    *,
    scene: Dict[str, Any],
    node: Dict[str, Any],
    task: Dict[str, Any],
    env_payload: Dict[str, Any],
    task_path: str,
    scene_path: str = "",
    env_file: str = "",
    mode: str = "",
    allow_side_effects: bool = False,
) -> ValidationIR:
    scene_id = _text(scene.get("scene_id") or scene.get("id") or Path(scene_path).stem)
    node_id = _text(node.get("node_id") or node.get("id") or "node")
    node_mode = _text(mode or node.get("mode") or "")
    command_text = _text(node.get("command_text"))
    observe_ms = _scene_node_observe_ms(node)
    source_context = {
        "scene_path": scene_path,
        "scene_id": scene_id,
        "scene_node": {
            "node_id": node_id,
            "action": _text(node.get("action")),
            "category": _text(node.get("category")),
            "depends_on": list(node.get("depends_on", []) or []),
            "metadata": node.get("metadata", {}) if isinstance(node.get("metadata"), dict) else {},
        },
    }
    ir = build_validation_ir(
        task=task,
        env_payload=env_payload,
        task_path=task_path,
        env_file=env_file,
        mode=node_mode,
        allow_side_effects=allow_side_effects,
        source_kind="scene_node",
        source_context=source_context,
        command_text=command_text,
        observe_ms=observe_ms,
    )
    ir.ir_id = f"{ir.project_id}:{scene_id}:{node_id}:{ir.task_id}:{ir.mode}"
    ir.metadata.update({"scene_id": scene_id, "node_id": node_id, "category": _text(node.get("category"))})
    return ir


def build_scene_validation_ir_bundle(
    *,
    scene: Dict[str, Any],
    env_payload: Dict[str, Any],
    load_task: Callable[[str], Dict[str, Any]],
    scene_path: str = "",
    env_file: str = "",
    mode: str = "",
    allow_side_effects: bool = False,
) -> Dict[str, Any]:
    scene_id = _text(scene.get("scene_id") or scene.get("id") or Path(scene_path).stem or "scene")
    project_id = _text(env_payload.get("project_id") or _nested(env_payload, "_config_source", "active_project"))
    items: List[Dict[str, Any]] = []
    for node in scene.get("nodes", []) if isinstance(scene.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        task_path = _text(node.get("task"))
        task = load_task(task_path)
        items.append(
            build_scene_node_validation_ir(
                scene=scene,
                node=node,
                task=task,
                env_payload=env_payload,
                task_path=task_path,
                scene_path=scene_path,
                env_file=env_file,
                mode=mode,
                allow_side_effects=allow_side_effects,
            ).to_dict()
        )
    return {
        "schema": "polaris.validation_ir_bundle.v1",
        "bundle_id": f"{project_id}:{scene_id}",
        "source_kind": "scene",
        "project_id": project_id,
        "created_at": now_iso(),
        "source": {"scene_path": scene_path, "env_file": env_file},
        "scene": {
            "scene_id": scene_id,
            "strategy_name": _text(scene.get("strategy_name")),
            "seed": scene.get("seed", ""),
            "node_count": len(items),
        },
        "items": items,
    }


def _task_from_feature_plan(plan: Dict[str, Any], feature_plan: Dict[str, Any]) -> Dict[str, Any]:
    context = feature_plan.get("context", {}) if isinstance(feature_plan.get("context"), dict) else {}
    scenario_id = _text(plan.get("scenario_id") or plan.get("scenario_name") or "feature_scenario")
    return {
        "schema": "polaris.cucumber.task.v1",
        "task_id": scenario_id,
        "description": _text(plan.get("scenario_name")),
        "scenario": {"tag": scenario_id},
        "runner": {
            "mode": "dry-run",
            "compile_first": True,
            "feature": _text(feature_plan.get("feature")),
            "mapping": "",
        },
        "inputs": {
            "command_text": _text(context.get("command_text")),
            "command_file": _text(context.get("command_file")),
            "command_limit": context.get("command_limit", ""),
        },
        "execution": {
            "observe_ms": context.get("observe_ms", ""),
            "manage_session": True,
            "allow_side_effects": False,
        },
        "preconditions": _list_of_text(plan.get("preconditions")),
        "expected": {
            "summary": _text(plan.get("agent_goal") or plan.get("mapping_title")),
            "assertions": plan.get("assertions", []) if isinstance(plan.get("assertions"), list) else [],
            "failure_attribution": _list_of_text(plan.get("failure_split")),
        },
        "actions": [
            {
                "id": str(command.get("name", f"command_{index:02d}")),
                "type": "compiled_command",
                "cmd": command.get("cmd", []),
                "cmdline": command.get("cmdline", ""),
                "source": "compiled_feature_plan",
            }
            for index, command in enumerate(plan.get("commands", []) if isinstance(plan.get("commands"), list) else [], start=1)
            if isinstance(command, dict)
        ],
    }


def build_feature_plan_ir_bundle(
    *,
    feature_plan: Dict[str, Any],
    env_payload: Dict[str, Any],
    plan_path: str = "",
    env_file: str = "",
    mode: str = "",
    allow_side_effects: bool = False,
) -> Dict[str, Any]:
    project_id = _text(env_payload.get("project_id") or _nested(env_payload, "_config_source", "active_project"))
    feature_name = _text(feature_plan.get("feature") or Path(plan_path).stem or "feature")
    items: List[Dict[str, Any]] = []
    for plan in feature_plan.get("plans", []) if isinstance(feature_plan.get("plans"), list) else []:
        if not isinstance(plan, dict):
            continue
        task = _task_from_feature_plan(plan, feature_plan)
        scenario_id = _text(plan.get("scenario_id") or task.get("task_id"))
        ir = build_validation_ir(
            task=task,
            env_payload=env_payload,
            task_path=plan_path,
            env_file=env_file,
            mode=mode or "dry-run",
            allow_side_effects=allow_side_effects,
            tag=scenario_id,
            source_kind="feature_scenario",
            source_context={
                "feature_plan": plan_path,
                "feature": feature_name,
                "scenario": {
                    "scenario_id": scenario_id,
                    "scenario_name": _text(plan.get("scenario_name")),
                    "tags": plan.get("tags", []) if isinstance(plan.get("tags"), list) else [],
                },
            },
        )
        ir.ir_id = f"{project_id}:{feature_name}:{scenario_id}:{ir.mode}"
        ir.metadata.update({"scenario_id": scenario_id, "scenario_name": _text(plan.get("scenario_name"))})
        items.append(ir.to_dict())
    return {
        "schema": "polaris.validation_ir_bundle.v1",
        "bundle_id": f"{project_id}:{feature_name}",
        "source_kind": "feature",
        "project_id": project_id,
        "created_at": now_iso(),
        "source": {"feature_plan": plan_path, "env_file": env_file},
        "feature": {
            "feature": feature_name,
            "scenario_count": len(items),
            "compile_error_count": len(feature_plan.get("compile_errors", []) if isinstance(feature_plan.get("compile_errors"), list) else []),
        },
        "items": items,
    }
