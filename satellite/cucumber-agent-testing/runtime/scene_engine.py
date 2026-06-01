#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Constraint-aware scene graph utilities.

The scene engine uses simple DAG records first. It is intentionally compatible
with task JSON and run_optimized_task.py so existing Cucumber logic remains the
execution backend.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .constraint_engine import ConstraintResult


@dataclass
class SceneNode:
    node_id: str
    action: str
    task: str
    category: str = ""
    command_text: str = ""
    mode: str = "dry-run"
    depends_on: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SceneGraph:
    scene_id: str
    strategy_name: str
    seed: int
    nodes: List[SceneNode]
    constraints: List[str] = field(default_factory=list)
    mutations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "polaris.scene_graph.v1",
            "scene_id": self.scene_id,
            "strategy_name": self.strategy_name,
            "seed": self.seed,
            "constraints": self.constraints,
            "mutations": self.mutations,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [
                {"from": dep, "to": item.node_id}
                for item in self.nodes
                for dep in item.depends_on
            ],
        }


def _weighted_categories(strategy: Dict[str, Any]) -> List[str]:
    bag: List[str] = []
    for item in strategy.get("bag", []) if isinstance(strategy.get("bag"), list) else []:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "") or "").strip()
        weight = int(item.get("weight", 1) or 1)
        if category:
            bag.extend([category] * max(1, weight))
    return bag


def generate_scene_graph(
    *,
    strategy_pool: Dict[str, Any],
    strategy_name: str,
    scene_id: str,
    seed: int,
    count: int,
    task: str,
    mode: str,
) -> SceneGraph:
    strategies = strategy_pool.get("strategies", {}) if isinstance(strategy_pool.get("strategies"), dict) else {}
    strategy = strategies.get(strategy_name)
    if not isinstance(strategy, dict):
        raise ValueError(f"strategy not found: {strategy_name}")
    rng = random.Random(seed)
    categories = _weighted_categories(strategy)
    if not categories:
        categories = sorted((strategy.get("phrases", {}) or {}).keys()) or ["basic_command"]
    phrases = strategy.get("phrases", {}) if isinstance(strategy.get("phrases"), dict) else {}
    nodes: List[SceneNode] = []
    previous = ""
    for index in range(1, count + 1):
        category = rng.choice(categories)
        candidates = phrases.get(category, []) if isinstance(phrases.get(category), list) else []
        command_text = str(rng.choice(candidates)) if candidates else category
        node_id = f"node_{index:03d}"
        random_gap_s = strategy.get("random_gap_s", [0, 0])
        observe_s = strategy.get("observe_s", [0, 0])
        metadata = {
            "random_gap_s": _range_pick(rng, random_gap_s),
            "observe_s": _range_pick(rng, observe_s),
        }
        nodes.append(
            SceneNode(
                node_id=node_id,
                action="voice_interaction",
                task=task,
                category=category,
                command_text=command_text,
                mode=mode,
                depends_on=[previous] if previous else [],
                metadata=metadata,
            )
        )
        previous = node_id
    return SceneGraph(
        scene_id=scene_id,
        strategy_name=strategy_name,
        seed=seed,
        nodes=nodes,
        constraints=["linear_dependencies", "network_required_for_online_categories"],
    )


def mutate_scene_graph(scene: SceneGraph, mutation: str, *, seed: int = 0) -> SceneGraph:
    rng = random.Random(seed or scene.seed)
    if mutation == "none":
        return scene
    if mutation == "shuffle":
        shuffled = list(scene.nodes)
        rng.shuffle(shuffled)
        previous = ""
        nodes: List[SceneNode] = []
        for node in shuffled:
            nodes.append(SceneNode(**{**node.to_dict(), "depends_on": [previous] if previous else []}))
            previous = node.node_id
        scene.nodes = nodes
        scene.mutations.append({"type": "shuffle", "seed": seed})
        return scene
    if mutation == "timing_jitter":
        for node in scene.nodes:
            node.metadata["random_gap_s"] = max(0, int(node.metadata.get("random_gap_s", 0) or 0) + rng.randint(-2, 5))
        scene.mutations.append({"type": "timing_jitter", "seed": seed})
        return scene
    if mutation == "insert_network_recovery" and scene.nodes:
        insert_at = rng.randint(0, len(scene.nodes) - 1)
        base = scene.nodes[insert_at]
        node_id = f"mutation_network_{insert_at + 1:03d}"
        recovery = SceneNode(
            node_id=node_id,
            action="network_recovery",
            task="satellite/cucumber-agent-testing/tasks/examples/first_wake.example.json",
            category="network",
            command_text="",
            mode=base.mode,
            depends_on=list(base.depends_on),
            metadata={"mutation": "insert_network_recovery"},
        )
        base.depends_on = [node_id]
        scene.nodes.insert(insert_at, recovery)
        scene.mutations.append({"type": "insert_network_recovery", "seed": seed, "node_id": node_id})
        return scene
    raise ValueError(f"unsupported mutation: {mutation}")


def validate_scene_graph(scene: Dict[str, Any], *, online_requires_network: bool = True, network_configured: bool = False) -> Dict[str, Any]:
    results: List[ConstraintResult] = []
    nodes = scene.get("nodes", []) if isinstance(scene.get("nodes"), list) else []
    node_ids = [str(item.get("node_id", "")) for item in nodes if isinstance(item, dict)]
    if len(node_ids) != len(set(node_ids)):
        results.append(ConstraintResult("scene_unique_node_ids", "FAIL", "scene node_id 存在重复。", severity="error"))
    else:
        results.append(ConstraintResult("scene_unique_node_ids", "PASS", "scene node_id 唯一。"))
    missing_deps: List[Dict[str, str]] = []
    known = set(node_ids)
    for item in nodes:
        if not isinstance(item, dict):
            continue
        for dep in item.get("depends_on", []) or []:
            if dep not in known:
                missing_deps.append({"node_id": str(item.get("node_id", "")), "missing_dep": str(dep)})
    if missing_deps:
        results.append(ConstraintResult("scene_dependencies", "FAIL", "scene 依赖不存在。", severity="error", actual={"missing": missing_deps}))
    else:
        results.append(ConstraintResult("scene_dependencies", "PASS", "scene 依赖均存在。"))
    if _has_cycle(nodes):
        results.append(ConstraintResult("scene_acyclic", "FAIL", "scene graph 存在环。", severity="error"))
    else:
        results.append(ConstraintResult("scene_acyclic", "PASS", "scene graph 为 DAG。"))
    online_categories = {"music", "crosstalk", "news", "qa_cooking", "qa_encyclopedia", "combo"}
    has_online = any(str(item.get("category", "")) in online_categories for item in nodes if isinstance(item, dict))
    if online_requires_network and has_online and not network_configured:
        results.append(ConstraintResult("network_required_for_online_categories", "WARN", "在线类别场景未配置网络，执行归因置信度降低。", severity="warn"))
    else:
        results.append(ConstraintResult("network_required_for_online_categories", "PASS", "在线类别网络约束满足或不适用。"))
    aggregate = "FAIL" if any(item.result == "FAIL" for item in results) else "PASS_WITH_WARNINGS" if any(item.result == "WARN" for item in results) else "PASS"
    return {"result": aggregate, "constraints": [item.to_dict() for item in results]}


def _range_pick(rng: random.Random, value: Any) -> int:
    if isinstance(value, list) and len(value) >= 2:
        return rng.randint(int(value[0] or 0), int(value[1] or 0))
    return int(value or 0) if str(value or "").isdigit() else 0


def _has_cycle(nodes: List[Any]) -> bool:
    deps = {str(item.get("node_id", "")): [str(dep) for dep in item.get("depends_on", []) or []] for item in nodes if isinstance(item, dict)}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dep in deps.get(node, []):
            if visit(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in deps)
