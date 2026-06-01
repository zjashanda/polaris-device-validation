# -*- coding: utf-8 -*-
"""Build reviewable registry extension drafts for sedimentation batch 1.

The script does not modify the active registry files. It writes extension drafts
under debug/registry_drafts so the changes can be reviewed and smoke-tested
before being merged into feature_contracts/step/action/assertion registries.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "satellite" / "cucumber-agent-testing"
DEFAULT_DRAFT = BASE / "references" / "sedimentation_batch1_registry_draft.json"
DEFAULT_CORPUS_ROOT = BASE / "debug" / "requirements_corpus"
DEFAULT_OUTPUT_ROOT = BASE / "debug" / "registry_drafts"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def latest_child_dir(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    dirs = [p for p in path.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def load_corpus(corpus_dir: Optional[Path]) -> Dict[str, Any]:
    if not corpus_dir:
        return {"records": [], "variants": [], "source_dir": ""}
    return {
        "records": read_json(corpus_dir / "corpus_candidates.json", []),
        "variants": read_json(corpus_dir / "synthetic_variants.json", []),
        "source_dir": str(corpus_dir),
    }


def build_feature_contracts(draft: Dict[str, Any], corpus: Dict[str, Any]) -> Dict[str, Any]:
    contracts: Dict[str, Any] = {
        "generated_from": "sedimentation_batch1_registry_draft.json",
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "corpus_source": corpus.get("source_dir", ""),
        "contracts": {}
    }
    for cap in draft.get("capabilities", []):
        contracts["contracts"][cap["id"]] = {
            "category": cap.get("category"),
            "test_items": cap.get("test_items", []),
            "level": cap.get("level"),
            "intent": cap.get("intent"),
            "data_sources": cap.get("data_sources", []),
            "preconditions": infer_preconditions(cap),
            "actions": cap.get("actions", []),
            "assertions": cap.get("assertions", []),
            "attribution": cap.get("attribution", {}),
            "needs_user_confirmation": cap.get("needs_user_confirmation", []),
            "merge_status": "draft_do_not_merge_without_smoke"
        }
    return contracts


def infer_preconditions(cap: Dict[str, Any]) -> List[str]:
    cap_id = cap.get("id", "")
    preconditions = ["local_serial_config_available", "audio_playback_device_available"]
    if cap_id.startswith("online.") or cap_id.startswith("network."):
        preconditions.append("device_online_or_recoverable")
    if "interrupt" in cap_id:
        preconditions.append("self_play_prerequisite_available")
    if "requirements" in cap_id or "free." in cap_id or "coverage" in cap_id:
        preconditions.append("requirements_corpus_available_or_doc_requirements_present")
    return preconditions


def build_step_registry(draft: Dict[str, Any]) -> Dict[str, Any]:
    steps = {
        "generated_from": "sedimentation_batch1_registry_draft.json",
        "steps": []
    }
    common_steps = [
        {
            "pattern": "使用本地 Polaris 串口配置",
            "binds_to": "action.load_local_polaris_config",
            "parameters": {}
        },
        {
            "pattern": "使用指定声卡播放测试音频",
            "binds_to": "action.verify_audio_device",
            "parameters": {}
        },
        {
            "pattern": "开启串口日志采集",
            "binds_to": "action.start_managed_serial_capture",
            "parameters": {}
        },
        {
            "pattern": "设备处于可唤醒状态",
            "binds_to": "action.ensure_wake_ready_or_wait_timeout",
            "parameters": {}
        },
        {
            "pattern": "设备已联网",
            "binds_to": "action.ensure_online",
            "parameters": {}
        },
        {
            "pattern": "已从需求文档抽取命令词候选",
            "binds_to": "action.load_or_build_requirements_corpus",
            "parameters": {"kind": "command"}
        },
        {
            "pattern": "已从需求文档抽取自由说候选",
            "binds_to": "action.load_or_build_requirements_corpus",
            "parameters": {"kind": "free_speech"}
        },
        {
            "pattern": "已选择稳定自播前置",
            "binds_to": "action.discover_or_load_interrupt_prerequisite",
            "parameters": {}
        }
    ]
    steps["steps"].extend(common_steps)
    for cap in draft.get("capabilities", []):
        steps["steps"].append({
            "pattern": f"执行能力 {cap['id']}",
            "binds_to": f"action.execute_capability.{cap['id']}",
            "parameters": {
                "capability_id": cap["id"],
                "level": cap.get("level")
            }
        })
        steps["steps"].append({
            "pattern": f"验证能力 {cap['id']} 的断言",
            "binds_to": f"assertion.evaluate_capability.{cap['id']}",
            "parameters": {
                "capability_id": cap["id"]
            }
        })
    return steps


def build_action_registry(draft: Dict[str, Any]) -> Dict[str, Any]:
    actions: Dict[str, Any] = {
        "generated_from": "sedimentation_batch1_registry_draft.json",
        "actions": {}
    }
    actions["actions"].update({
        "action.load_local_polaris_config": {
            "type": "python",
            "entrypoint": "load polaris.local.json, fallback to config/polaris_env.json/config/polaris_local_ports.json",
            "side_effects": false_like(False)
        },
        "action.verify_audio_device": {
            "type": "skill",
            "entrypoint": "listenai-play device probe",
            "side_effects": false_like(False)
        },
        "action.start_managed_serial_capture": {
            "type": "python",
            "entrypoint": "managed logger under debug/session",
            "side_effects": false_like(False)
        },
        "action.ensure_online": {
            "type": "python",
            "entrypoint": "tools/device/polaris_network_orchestrator.py ensure-online",
            "side_effects": true_like(True)
        },
        "action.load_or_build_requirements_corpus": {
            "type": "python",
            "entrypoint": "satellite/cucumber-agent-testing/scripts/ingest_requirements_corpus.py",
            "side_effects": false_like(False)
        },
        "action.discover_or_load_interrupt_prerequisite": {
            "type": "python",
            "entrypoint": "planned interrupt prerequisite discovery: weather/music/offline longest TTS",
            "side_effects": true_like(True)
        }
    })
    for cap in draft.get("capabilities", []):
        actions["actions"][f"action.execute_capability.{cap['id']}"] = {
            "type": "composite",
            "capability_id": cap["id"],
            "actions": cap.get("actions", []),
            "data_sources": cap.get("data_sources", []),
            "side_effects": side_effect_for_capability(cap["id"]),
            "merge_status": "draft"
        }
    return actions


def true_like(value: bool) -> bool:
    return value


def false_like(value: bool) -> bool:
    return value


def side_effect_for_capability(capability_id: str) -> bool:
    side_effect_prefixes = ("wake.", "command.", "online.", "interrupt.", "network.", "false_wake.")
    return capability_id.startswith(side_effect_prefixes)


def build_assertion_registry(draft: Dict[str, Any]) -> Dict[str, Any]:
    assertions = {
        "generated_from": "sedimentation_batch1_registry_draft.json",
        "assertions": {}
    }
    for cap in draft.get("capabilities", []):
        assertions["assertions"][f"assertion.evaluate_capability.{cap['id']}"] = {
            "capability_id": cap["id"],
            "rules": cap.get("assertions", []),
            "attribution": cap.get("attribution", {}),
            "needs_user_confirmation": cap.get("needs_user_confirmation", []),
            "unknown_or_unconfirmed_policy": "NEEDS_REVIEW_OR_BLOCKED_NOT_FAIL",
            "merge_status": "draft"
        }
    return assertions


def build_oracle_summary(corpus: Dict[str, Any]) -> Dict[str, Any]:
    records = [r for r in corpus.get("records", []) if r.get("kind") != "error"]
    variants = corpus.get("variants", [])
    counts: Dict[str, int] = {}
    for record in records:
        counts[record.get("kind", "unknown")] = counts.get(record.get("kind", "unknown"), 0) + 1
    return {
        "corpus_source": corpus.get("source_dir", ""),
        "counts": counts,
        "records_total": len(records),
        "synthetic_variants_total": len(variants),
        "formal_oracle_policy": "Only reviewed records with expected_text/intent/action can be used for formal PASS/FAIL.",
        "negative_sample_policy": "Generated negative samples are exploratory until confirmed by user or requirement document."
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_feature_stub(contracts: Dict[str, Any]) -> str:
    lines = [
        "# Auto-generated draft feature stubs",
        "# Move selected scenarios into features/ only after step/action/assertion registry is merged.",
        "",
        "@sedimentation @draft",
        "Feature: Polaris 第一批沉淀能力",
        "",
        "  Background:",
        "    Given 使用本地 Polaris 串口配置",
        "    And 使用指定声卡播放测试音频",
        "    And 开启串口日志采集",
        ""
    ]
    for cap_id, contract in contracts.get("contracts", {}).items():
        safe_name = contract.get("intent", cap_id).replace("\n", " ")
        lines.extend([
            f"  @{cap_id.replace('.', '_')}",
            f"  Scenario: {safe_name}",
            f"    When 执行能力 {cap_id}",
            f"    Then 验证能力 {cap_id} 的断言",
            ""
        ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", default=str(DEFAULT_DRAFT))
    parser.add_argument("--corpus-dir", default="")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    draft_path = Path(args.draft)
    draft = read_json(draft_path, {})
    corpus_dir = Path(args.corpus_dir) if args.corpus_dir else latest_child_dir(DEFAULT_CORPUS_ROOT)
    corpus = load_corpus(corpus_dir)

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    feature_contracts = build_feature_contracts(draft, corpus)
    step_registry = build_step_registry(draft)
    action_registry = build_action_registry(draft)
    assertion_registry = build_assertion_registry(draft)
    oracle_summary = build_oracle_summary(corpus)

    write_json(output_dir / "feature_contracts.extension.json", feature_contracts)
    write_json(output_dir / "step_registry.extension.json", step_registry)
    write_json(output_dir / "action_registry.extension.json", action_registry)
    write_json(output_dir / "assertion_registry.extension.json", assertion_registry)
    write_json(output_dir / "oracle_summary.json", oracle_summary)
    (output_dir / "polaris_sedimentation_batch1.feature.draft").write_text(
        build_feature_stub(feature_contracts),
        encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "\n".join([
            "# Registry Draft Output",
            "",
            f"- generated_at: `{_dt.datetime.now().isoformat(timespec='seconds')}`",
            f"- draft_source: `{draft_path}`",
            f"- corpus_source: `{corpus.get('source_dir', '')}`",
            "",
            "These files are extension drafts. Review and smoke-test before merging into active registry files.",
            "",
            "## Files",
            "",
            "- `feature_contracts.extension.json`",
            "- `step_registry.extension.json`",
            "- `action_registry.extension.json`",
            "- `assertion_registry.extension.json`",
            "- `oracle_summary.json`",
            "- `polaris_sedimentation_batch1.feature.draft`"
        ]),
        encoding="utf-8"
    )
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
