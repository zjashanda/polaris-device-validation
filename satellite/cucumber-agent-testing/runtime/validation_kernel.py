#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validation Kernel lifecycle MVP.

The kernel is a thin deterministic coordinator.  It compiles Validation IR,
captures adapter/capability/resource/constraint snapshots and can delegate
execution to the existing run_optimized_task.py without replacing it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analytics_trend import build_trend, render_trend_markdown
from .event_graph import build_event_graph, render_event_graph_markdown
from .events import ValidationEvent
from .replay_vm import ReplayVM
from .state_assertion_dsl import evaluate_state_dsl
from .state_coverage_policy import evaluate_state_coverage_policy
from .timeline import Timeline
from .validation_ir import build_validation_ir


EVENT_FIELDS = set(ValidationEvent.__dataclass_fields__.keys())


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _quote_cmd(args: List[str]) -> str:
    rendered: List[str] = []
    for arg in args:
        text = str(arg)
        if not text:
            rendered.append('""')
        elif any(ch.isspace() for ch in text) or any(ch in text for ch in ['"', "'", "&"]):
            rendered.append('"' + text.replace('"', '\\"') + '"')
        else:
            rendered.append(text)
    return " ".join(rendered)


def _timeline_from_json(path: Path) -> Timeline:
    payload = _load_json(path)
    events: List[ValidationEvent] = []
    for item in payload.get("events", []):
        if isinstance(item, dict):
            events.append(ValidationEvent(**{key: value for key, value in item.items() if key in EVENT_FIELDS}))
    return Timeline.from_events(events)


def _timeline_from_payload(payload: Dict[str, Any]) -> Timeline:
    timeline_payload = payload.get("timeline", {}) if isinstance(payload.get("timeline"), dict) else {}
    events: List[ValidationEvent] = []
    for item in timeline_payload.get("events", []):
        if isinstance(item, dict):
            events.append(ValidationEvent(**{key: value for key, value in item.items() if key in EVENT_FIELDS}))
    return Timeline.from_events(events)


def _resolve_workspace_path(workspace_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (workspace_root / path).resolve()


@dataclass
class KernelStage:
    name: str
    result: str
    started_at: str
    finished_at: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class KernelRecord:
    kernel_id: str
    result: str
    task_id: str
    project_id: str
    mode: str
    created_at: str
    stages: List[KernelStage]
    artifacts: Dict[str, str] = field(default_factory=dict)
    runner: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "polaris.validation_kernel_record.v1",
            "kernel_id": self.kernel_id,
            "result": self.result,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "mode": self.mode,
            "created_at": self.created_at,
            "stages": [stage.to_dict() for stage in self.stages],
            "artifacts": self.artifacts,
            "runner": self.runner,
        }


class ValidationKernel:
    def __init__(self, *, workspace_root: Path, scripts_dir: Path, out_dir: Path) -> None:
        self.workspace_root = workspace_root
        self.scripts_dir = scripts_dir
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.lifecycle_path = self.out_dir / "lifecycle.jsonl"
        self.stages: List[KernelStage] = []

    def stage(self, name: str, result: str, started_at: str, detail: Optional[Dict[str, Any]] = None) -> None:
        stage = KernelStage(name=name, result=result, started_at=started_at, finished_at=now_iso(), detail=detail or {})
        self.stages.append(stage)
        append_jsonl(self.lifecycle_path, stage.to_dict())

    def run(
        self,
        *,
        task_path: Path,
        env_path: Path,
        task: Dict[str, Any],
        env_payload: Dict[str, Any],
        mode: str,
        tag: str = "",
        allow_side_effects: bool = False,
        execute_runner: bool = False,
        manage_session: bool = False,
        runtime_strict: bool = False,
        max_retries: int = 0,
        retry_blocked: bool = False,
        command_text: str = "",
        observe_ms: str = "",
    ) -> KernelRecord:
        started = now_iso()
        ir = build_validation_ir(
            task=task,
            env_payload=env_payload,
            task_path=str(task_path),
            env_file=str(env_path),
            mode=mode,
            allow_side_effects=allow_side_effects,
            tag=tag,
            command_text=command_text,
            observe_ms=observe_ms,
            source_context={"runtime_overrides": {"command_text": command_text, "observe_ms": observe_ms}},
        )
        write_json(self.out_dir / "validation_ir.json", ir.to_dict())
        write_json(self.out_dir / "adapter_registry.json", ir.adapters)
        write_json(self.out_dir / "capability_matrix.json", ir.capabilities)
        write_json(self.out_dir / "resource_snapshot.json", ir.resources)
        write_json(self.out_dir / "constraint_result.json", ir.constraints)
        self.stage("compile_ir", "PASS", started, {"ir_id": ir.ir_id})
        self.stage("preflight", str(ir.constraints.get("result", "UNKNOWN")), now_iso(), {"warning_count": len(ir.constraints.get("warnings", []))})

        runner_summary: Dict[str, Any] = {}
        result = "PLAN_OK" if ir.constraints.get("result") == "PASS" else str(ir.constraints.get("result", "BLOCKED"))
        if execute_runner and result in {"PLAN_OK", "PASS"}:
            runner_summary = self._run_optimized_task(
                task_path=task_path,
                env_path=env_path,
                mode=mode,
                tag=tag,
                allow_side_effects=allow_side_effects,
                manage_session=manage_session,
                runtime_strict=runtime_strict,
                max_retries=max_retries,
                retry_blocked=retry_blocked,
                command_text=command_text,
                observe_ms=observe_ms,
            )
            result = str(runner_summary.get("result", "UNKNOWN") or "UNKNOWN")
            sidecar_result = self._build_runner_sidecars(runner_summary, project_id=ir.project_id)
            if sidecar_result == "FAIL" and result == "PASS":
                result = "FAIL"
        elif execute_runner:
            self.stage("runner_skipped", "BLOCKED", now_iso(), {"reason": f"preflight result={result}"})

        records = list((self.out_dir / "optimized").rglob("execution_record.json"))
        if records:
            trend = build_trend(records)
            write_json(self.out_dir / "analytics_trend.json", trend)
            (self.out_dir / "analytics_trend.md").write_text(render_trend_markdown(trend), encoding="utf-8")

        artifacts = {
            "lifecycle": str(self.lifecycle_path),
            "validation_ir": str(self.out_dir / "validation_ir.json"),
            "adapter_registry": str(self.out_dir / "adapter_registry.json"),
            "capability_matrix": str(self.out_dir / "capability_matrix.json"),
            "resource_snapshot": str(self.out_dir / "resource_snapshot.json"),
            "constraint_result": str(self.out_dir / "constraint_result.json"),
        }
        optional_artifacts = {
            "event_graph": self.out_dir / "event_graph.json",
            "event_graph_report": self.out_dir / "event_graph_report.md",
            "runtime_analysis": self.out_dir / "runtime_analysis.json",
            "runtime_analysis_report": self.out_dir / "runtime_analysis.md",
            "analytics_trend": self.out_dir / "analytics_trend.json",
            "analytics_trend_report": self.out_dir / "analytics_trend.md",
        }
        for name, path in optional_artifacts.items():
            if path.exists():
                artifacts[name] = str(path)
        record = KernelRecord(
            kernel_id=ir.ir_id,
            result=result,
            task_id=ir.task_id,
            project_id=ir.project_id,
            mode=mode,
            created_at=now_iso(),
            stages=self.stages,
            artifacts=artifacts,
            runner=runner_summary,
        )
        write_json(self.out_dir / "kernel_record.json", record.to_dict())
        return record

    def _run_optimized_task(
        self,
        *,
        task_path: Path,
        env_path: Path,
        mode: str,
        tag: str,
        allow_side_effects: bool,
        manage_session: bool,
        runtime_strict: bool,
        max_retries: int,
        retry_blocked: bool = False,
        command_text: str = "",
        observe_ms: str = "",
    ) -> Dict[str, Any]:
        started = now_iso()
        cmd = [
            sys.executable,
            str(self.scripts_dir / "run_optimized_task.py"),
            "--task",
            str(task_path),
            "--env-file",
            str(env_path),
            "--mode",
            mode,
            "--out-root",
            str(self.out_dir / "optimized"),
            "--max-retries",
            str(max_retries),
        ]
        if tag:
            cmd.extend(["--tag", tag])
        if command_text:
            cmd.extend(["--command-text", command_text])
        if observe_ms:
            cmd.extend(["--observe-ms", observe_ms])
        if allow_side_effects:
            cmd.append("--allow-side-effects")
        if manage_session:
            cmd.append("--manage-session")
        if runtime_strict:
            cmd.append("--runtime-strict")
        if retry_blocked:
            cmd.append("--retry-blocked")
        write_json(self.out_dir / "runner_command.json", {"cmd": cmd, "cmdline": _quote_cmd(cmd)})
        completed = subprocess.run(
            cmd,
            cwd=str(self.workspace_root),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        stdout = completed.stdout or ""
        (self.out_dir / "runner_stdout.log").write_text(stdout, encoding="utf-8")
        summary = self._parse_runner_output(stdout)
        summary.update({"returncode": completed.returncode, "stdout": str(self.out_dir / "runner_stdout.log"), "cmd": cmd})
        self.stage("run_optimized_task", summary.get("result", "UNKNOWN"), started, {"returncode": completed.returncode, "run_root": summary.get("run_root", "")})
        return summary

    def _parse_runner_output(self, stdout: str) -> Dict[str, Any]:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        run_root = ""
        for line in lines:
            if "optimized" in line and ("Polaris" in line or "cucumber-agent-testing" in line):
                candidate = Path(line)
                if not candidate.is_absolute():
                    candidate = (self.workspace_root / candidate).resolve()
                if candidate.exists():
                    run_root = str(candidate)
        match = re.search(r"result=(?P<result>[A-Z_]+)\s+stability=(?P<stability>[A-Z_]+)(?:\s+attempts=(?P<attempts>\d+))?", stdout)
        return {
            "result": match.group("result") if match else ("FAIL" if "returncode=1" in stdout else "UNKNOWN"),
            "stability": match.group("stability") if match else "",
            "attempts": int(match.group("attempts") or 0) if match else 0,
            "run_root": run_root,
        }

    def _build_runner_sidecars(self, runner_summary: Dict[str, Any], project_id: str = "") -> str:
        run_root = Path(str(runner_summary.get("run_root", "") or ""))
        if not run_root.exists():
            return "SKIPPED"
        execution_record = next(run_root.rglob("execution_record.json"), None)
        if execution_record:
            write_json(self.out_dir / "runner_execution_record_ref.json", {"execution_record": str(execution_record)})
        replay_packages = self._discover_replay_packages(run_root, execution_record)
        if not replay_packages:
            self.stage("runtime_sidecars", "SKIPPED", now_iso(), {"reason": "no runtime replay package found", "run_root": str(run_root)})
            return "SKIPPED"

        analyses: List[Dict[str, Any]] = []
        for index, package_path in enumerate(replay_packages, start=1):
            package = _load_json(package_path)
            metadata = package.get("metadata", {}) if isinstance(package.get("metadata"), dict) else {}
            profile = str(metadata.get("profile", package_path.parent.name) or package_path.parent.name)
            scenario_id = package_path.parent.name
            analysis_dir = self.out_dir / "post_analysis" / f"{index:02d}_{scenario_id}"
            analysis_dir.mkdir(parents=True, exist_ok=True)

            timeline = _timeline_from_payload(package)
            package_project = str(metadata.get("project", "") or project_id or "")
            graph = build_event_graph(timeline, rule_overlay=self._default_event_graph_rules(), project_id=package_project)
            write_json(analysis_dir / "event_graph.json", graph.to_dict())
            (analysis_dir / "event_graph_report.md").write_text(render_event_graph_markdown(graph), encoding="utf-8")

            state = package.get("runtime_state", {}) if isinstance(package.get("runtime_state"), dict) else {}
            coverage = state.get("coverage", {}) if isinstance(state.get("coverage"), dict) else {}
            state_health = str(state.get("state_health", "UNKNOWN") or "UNKNOWN")
            policy_dsl = self._default_state_assertion_dsl(profile)
            (analysis_dir / "default_state_assertions.dsl").write_text(policy_dsl, encoding="utf-8")
            state_assertions = evaluate_state_dsl(state, policy_dsl) if state else {
                "schema": "polaris.state_assertion_dsl_result.v1",
                "result": "SKIPPED",
                "assertion_count": 0,
                "assertions": [],
                "reason": "runtime_state missing",
            }
            write_json(analysis_dir / "state_assertions.json", state_assertions)
            policy_payload = self._default_state_policy_payload()
            coverage_policy = evaluate_state_coverage_policy(state, profile, policy_payload, project_id=package_project) if state else {
                "schema": "polaris.state_coverage_policy_result.v1",
                "profile": profile,
                "project_id": package_project,
                "result": "SKIPPED",
                "checks": [],
                "reason": "runtime_state missing",
            }
            write_json(analysis_dir / "state_coverage_policy.json", coverage_policy)

            vm = ReplayVM(package)
            for _ in range(max(0, len(timeline.events))):
                if not vm.step():
                    break
            vm.snapshot()
            write_json(analysis_dir / "replay_vm_state.json", vm.to_dict())

            assertion_summary = package.get("assertion_summary", {}) if isinstance(package.get("assertion_summary"), dict) else {}
            item = {
                "scenario_id": scenario_id,
                "profile": profile,
                "project_id": package_project,
                "package": str(package_path),
                "event_count": package.get("timeline", {}).get("event_count", len(timeline.events)) if isinstance(package.get("timeline"), dict) else len(timeline.events),
                "assertion_result": assertion_summary.get("result", "UNKNOWN"),
                "state_assertion_result": state_assertions.get("result", "UNKNOWN"),
                "state_coverage_result": coverage_policy.get("result", "UNKNOWN"),
                "state_health": state_health,
                "state_violation_count": coverage.get("violation_count", len(state.get("state_violations", []) or [])),
                "transition_count": coverage.get("transition_count", len(state.get("transitions", []) or [])),
                "event_graph": str(analysis_dir / "event_graph.json"),
                "state_assertions": str(analysis_dir / "state_assertions.json"),
                "state_coverage_policy": str(analysis_dir / "state_coverage_policy.json"),
                "replay_vm_state": str(analysis_dir / "replay_vm_state.json"),
            }
            analyses.append(item)
            self.stage(
                "runtime_sidecar_analysis",
                str(state_assertions.get("result", "UNKNOWN")),
                now_iso(),
                {
                    "scenario_id": scenario_id,
                    "profile": profile,
                    "events": item["event_count"],
                    "graph_nodes": len(graph.nodes),
                    "graph_edges": len(graph.edges),
                    "graph_warnings": len(graph.warnings),
                    "state_health": state_health,
                    "state_coverage_result": coverage_policy.get("result", "UNKNOWN"),
                    "state_violation_count": item["state_violation_count"],
                    "transition_count": item["transition_count"],
                },
            )

        aggregate = self._aggregate_runtime_analysis(analyses)
        write_json(self.out_dir / "runtime_analysis.json", {"schema": "polaris.kernel_runtime_analysis.v1", "result": aggregate, "items": analyses})
        (self.out_dir / "runtime_analysis.md").write_text(self._render_runtime_analysis_markdown(aggregate, analyses), encoding="utf-8")
        if analyses:
            first_graph = Path(str(analyses[0].get("event_graph", "")))
            if first_graph.exists():
                write_json(self.out_dir / "event_graph.json", _load_json(first_graph))
                report = first_graph.with_name("event_graph_report.md")
                if report.exists():
                    (self.out_dir / "event_graph_report.md").write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
        return aggregate

    def _discover_replay_packages(self, run_root: Path, execution_record: Optional[Path]) -> List[Path]:
        packages: List[Path] = []
        packages.extend(run_root.rglob("runtime_replay/*/replay_package.json"))
        if execution_record and execution_record.exists():
            record = _load_json(execution_record)
            for attempt in record.get("attempts", []) if isinstance(record.get("attempts"), list) else []:
                if not isinstance(attempt, dict):
                    continue
                run_dir_text = str(attempt.get("run_dir", "") or "").strip()
                if not run_dir_text:
                    continue
                run_dir = _resolve_workspace_path(self.workspace_root, run_dir_text)
                if run_dir.exists():
                    packages.extend(run_dir.rglob("runtime_replay/*/replay_package.json"))
        unique: List[Path] = []
        seen: set[str] = set()
        for path in packages:
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _default_state_assertion_dsl(self, profile: str) -> str:
        policy = self._default_state_policy_payload()
        lines: List[str] = []
        common = policy.get("common", []) if isinstance(policy.get("common"), list) else []
        lines.extend(str(item) for item in common if str(item).strip())
        profiles = policy.get("profiles", {}) if isinstance(policy.get("profiles"), dict) else {}
        profile_lines = profiles.get(profile, []) if isinstance(profiles.get(profile), list) else []
        lines.extend(str(item) for item in profile_lines if str(item).strip())
        if not lines:
            lines = [
                "FORBID_STATE parallel_states.power = CRASHED",
                "FORBID_STATE final_state = CRASHED",
                "FORBID_HISTORY CrashDetected",
            ]
        return "\n".join(lines) + "\n"

    def _default_state_policy_payload(self) -> Dict[str, Any]:
        policy_path = self.scripts_dir.parent / "references" / "optimization" / "state_assertion_policy.json"
        return _load_json(policy_path)

    def _default_event_graph_rules(self) -> Dict[str, Any]:
        rules_path = self.scripts_dir.parent / "references" / "optimization" / "event_graph_rules.json"
        return _load_json(rules_path)

    @staticmethod
    def _aggregate_runtime_analysis(analyses: List[Dict[str, Any]]) -> str:
        results = [str(item.get("state_assertion_result", "") or "").upper() for item in analyses]
        coverage = [str(item.get("state_coverage_result", "") or "").upper() for item in analyses]
        health = [str(item.get("state_health", "") or "").upper() for item in analyses]
        if not results:
            return "SKIPPED"
        if any(item == "FAIL" for item in results) or any(item == "FAIL" for item in coverage) or any(item == "FAIL" for item in health):
            return "FAIL"
        if all(item == "SKIPPED" for item in results):
            return "SKIPPED"
        if (
            any(item in {"UNKNOWN", "ERROR"} for item in results)
            or any(item in {"WARN", "UNKNOWN", "ERROR"} for item in coverage)
            or any(item in {"WARN", "UNKNOWN", "ERROR"} for item in health)
        ):
            return "WARN"
        return "PASS"

    @staticmethod
    def _render_runtime_analysis_markdown(result: str, analyses: List[Dict[str, Any]]) -> str:
        lines = [
            "# Kernel Runtime Analysis",
            "",
            f"- result: `{result}`",
            f"- items: `{len(analyses)}`",
            "",
            "| Scenario | Profile | Events | Assertion | State/Coverage | State Health | Violations | Transitions |",
            "|---|---|---:|---|---|---|---:|---:|",
        ]
        for item in analyses:
            lines.append(
                "| {scenario} | `{profile}` | {events} | `{assertion}` | `{state}`/`{coverage}` | `{health}` | {violations} | {transitions} |".format(
                    scenario=item.get("scenario_id", ""),
                    profile=item.get("profile", ""),
                    events=item.get("event_count", 0),
                    assertion=item.get("assertion_result", "UNKNOWN"),
                    state=item.get("state_assertion_result", "UNKNOWN"),
                    coverage=item.get("state_coverage_result", "UNKNOWN"),
                    health=item.get("state_health", "UNKNOWN"),
                    violations=item.get("state_violation_count", 0),
                    transitions=item.get("transition_count", 0),
                )
            )
        lines.append("")
        return "\n".join(lines)
