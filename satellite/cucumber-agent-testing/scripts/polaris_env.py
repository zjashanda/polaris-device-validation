#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for loading the user-facing Polaris local config.

The preferred local config is `polaris.local.json` in the skill root.  It may
contain multiple project profiles and an `active_project` selector.  Legacy flat
configs such as `config/polaris_env.json` are still accepted as a fallback.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple


LOCAL_CONFIG_NAME = "polaris.local.json"
LEGACY_CONFIG = Path("config") / "polaris_env.json"


def resolve_env_path(value: str | os.PathLike[str] | None, workspace_root: Path) -> Path:
    """Resolve a config path against the workspace root."""
    if value:
        path = Path(value)
        return path if path.is_absolute() else (workspace_root / path).resolve()
    return default_env_path(workspace_root)


def default_env_path(workspace_root: Path) -> Path:
    """Return the default config path without forcing the file to exist."""
    env_override = os.environ.get("POLARIS_ENV_FILE", "").strip()
    if env_override:
        return resolve_env_path(env_override, workspace_root)
    root_local = workspace_root / LOCAL_CONFIG_NAME
    if root_local.exists():
        return root_local
    legacy = workspace_root / LEGACY_CONFIG
    if legacy.exists():
        return legacy
    return root_local


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def normalize_env_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize either a flat env config or a multi-project local config."""
    projects = payload.get("projects")
    if not isinstance(projects, dict):
        return payload

    active_project = str(payload.get("active_project") or payload.get("project_id") or "").strip()
    if not active_project and len(projects) == 1:
        active_project = next(iter(projects))
    profile = projects.get(active_project)
    if not isinstance(profile, dict):
        return {}

    common = payload.get("common") if isinstance(payload.get("common"), dict) else {}
    merged = deep_merge(common, profile)
    merged.setdefault("project_id", active_project)
    merged["_config_source"] = {
        "schema": payload.get("schema", ""),
        "active_project": active_project,
    }
    return merged


def load_env_payload(path: Path) -> Dict[str, Any]:
    return normalize_env_payload(read_json(path))


def load_default_env(workspace_root: Path) -> Tuple[Path, Dict[str, Any]]:
    path = default_env_path(workspace_root)
    return path, load_env_payload(path)
