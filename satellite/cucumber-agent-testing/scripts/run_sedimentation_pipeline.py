# -*- coding: utf-8 -*-
"""Run the first sedimentation pipeline.

The pipeline intentionally writes outputs under debug only. It is safe to run
multiple times; each run receives a timestamped directory and a run summary.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "satellite" / "cucumber-agent-testing"
SCRIPT_DIR = BASE / "scripts"
DEFAULT_OUTPUT_ROOT = BASE / "debug" / "sedimentation_pipeline"


def run_step(name: str, command: List[str], cwd: Path) -> Dict[str, Any]:
    started = _dt.datetime.now()
    result: Dict[str, Any] = {
        "name": name,
        "command": command,
        "started_at": started.isoformat(timespec="seconds"),
        "status": "RUNNING"
    }
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=None
        )
        result.update({
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "status": "PASS" if proc.returncode == 0 else "FAIL"
        })
    except Exception as exc:  # pragma: no cover - runtime protection
        result.update({
            "returncode": None,
            "stdout": "",
            "stderr": repr(exc),
            "status": "ERROR"
        })
    result["ended_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    return result


def write_report(output_dir: Path, steps: List[Dict[str, Any]]) -> None:
    lines = [
        "# Sedimentation Pipeline Run",
        "",
        f"- generated_at: `{_dt.datetime.now().isoformat(timespec='seconds')}`",
        f"- output_dir: `{output_dir}`",
        "",
        "## Steps",
        "",
        "| Step | Status | Return Code |",
        "| --- | --- | --- |"
    ]
    for step in steps:
        lines.append(f"| {step['name']} | {step['status']} | {step.get('returncode')} |")
    lines.extend([
        "",
        "## Next Review Files",
        "",
        "- `debug/requirements_corpus/<stamp>/corpus_candidates.csv`",
        "- `debug/requirements_corpus/<stamp>/synthetic_variants.csv`",
        "- `debug/interrupt_prerequisites/<stamp>/interrupt_prerequisite_report.md`",
        "- `debug/registry_drafts/<stamp>/README.md`",
        "- `debug/oracle_drafts/<stamp>/requirement_oracle_report.md`",
    ])
    (output_dir / "pipeline_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--skip-interrupt", action="store_true")
    parser.add_argument("--skip-registry-draft", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    steps: List[Dict[str, Any]] = []
    if not args.skip_ingest:
        steps.append(run_step(
            "ingest_requirements_corpus",
            [sys.executable, str(SCRIPT_DIR / "ingest_requirements_corpus.py")],
            ROOT
        ))
    if not args.skip_interrupt:
        steps.append(run_step(
            "discover_interrupt_prerequisites",
            [sys.executable, str(SCRIPT_DIR / "discover_interrupt_prerequisites.py")],
            ROOT
        ))
    if not args.skip_registry_draft:
        steps.append(run_step(
            "build_sedimentation_registry_draft",
            [sys.executable, str(SCRIPT_DIR / "build_sedimentation_registry_draft.py")],
            ROOT
        ))
        steps.append(run_step(
            "build_requirement_oracle_draft",
            [sys.executable, str(SCRIPT_DIR / "build_requirement_oracle_draft.py")],
            ROOT
        ))

    summary = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "steps": steps,
        "status": "PASS" if all(step["status"] == "PASS" for step in steps) else "PARTIAL_OR_FAIL"
    }
    (output_dir / "pipeline_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(output_dir, steps)
    print(output_dir)
    return 0 if summary["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
