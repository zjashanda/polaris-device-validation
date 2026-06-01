#!/usr/bin/env python3
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


PORT_LOGS = {
    "COM12": "COM12.log",
    "COM13": "COM13.log",
    "COM14": "COM14.log",
}
CANONICAL_PORT_ROLES = {
    "COM12": "cp",
    "COM13": "asr",
    "COM14": "ap",
}

SESSION_LIVE_DIR = ("logs", "live")
SESSION_ARTIFACTS_DIR = ("artifacts",)
ARTIFACT_GROUPS: Sequence[Tuple[str, Tuple[str, ...]]] = (
    ("app_control_", ("cloud", "app_control")),
    ("state_probe_", ("probe", "state")),
    ("phrase_probe_", ("probe", "phrase")),
    ("doc_case_run_", ("doc_cases", "runs")),
    ("doc_case_audit_", ("doc_cases", "audit")),
    ("case_result_table_", ("reporting", "case_tables")),
    ("command_validation_", ("validation", "commands")),
    ("harness_restart_verify_", ("device", "harness")),
    ("power_cycle_", ("device", "power")),
    ("hotspot_cycle", ("device", "network")),
    ("vir_reboot", ("device", "network")),
    ("recovery_batch_", ("execution", "batch")),
    ("followup_queue_", ("execution", "queue")),
    ("suite_run_", ("execution", "suites")),
    ("case_run_", ("execution", "cases")),
)
STAMP_RE = re.compile(r"^(?P<stamp>\d{14,17})_(?P<task>.+)$")
LEGACY_STAMP_RE = re.compile(r"^(?P<task>.+?)_(?P<stamp>\d{14,17})(?P<suffix>_.+)?$")


def now_iso_ms() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def current_session_dir(root: Optional[Path] = None) -> Path:
    root = root or workspace_root()
    marker = root / ".current_result_dir"
    if not marker.exists():
        raise FileNotFoundError(f"missing session marker: {marker}")
    session = marker.read_text(encoding="utf-8").strip().lstrip("\ufeff")
    path = Path(session)
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_live_root(session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    session_dir = session_dir or current_session_dir()
    live_root = session_dir.joinpath(*SESSION_LIVE_DIR)
    if create:
        return ensure_dir(live_root)
    return live_root if live_root.exists() else session_dir


def session_artifacts_root(session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    session_dir = session_dir or current_session_dir()
    artifacts_root = session_dir.joinpath(*SESSION_ARTIFACTS_DIR)
    if create:
        return ensure_dir(artifacts_root)
    return artifacts_root


def session_live_file(name: str, session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    session_dir = session_dir or current_session_dir()
    preferred = session_dir.joinpath(*SESSION_LIVE_DIR, name)
    legacy = session_dir / name
    if create:
        ensure_dir(preferred.parent)
        return preferred
    if preferred.exists():
        return preferred
    return legacy


def queue_path(session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    return session_live_file("control.jsonl", session_dir=session_dir, create=create)


def heartbeat_path(session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    return session_live_file("heartbeat.json", session_dir=session_dir, create=create)


def manifest_path(session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    return session_live_file("session_manifest.json", session_dir=session_dir, create=create)


def pid_path(session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    return session_live_file("logger.pid", session_dir=session_dir, create=create)


def merged_log_path(session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    return session_live_file("merged.log", session_dir=session_dir, create=create)


def events_path(session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    return session_live_file("events.jsonl", session_dir=session_dir, create=create)


def runtime_log_path(session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    return session_live_file("runtime.log", session_dir=session_dir, create=create)


def artifact_category_parts(prefix: str) -> Tuple[str, ...]:
    normalized = prefix.rstrip("_")
    for known_prefix, parts in ARTIFACT_GROUPS:
        known = known_prefix.rstrip("_")
        if prefix.startswith(known) or normalized.startswith(known):
            return parts
    token = normalized.split("_", 1)[0].replace("-", "_") or "misc"
    return ("misc", token)


def artifact_group_root(prefix: str, session_dir: Optional[Path] = None, *, create: bool = False) -> Path:
    session_dir = session_dir or current_session_dir()
    artifacts_root = session_artifacts_root(session_dir, create=create)
    group_root = artifacts_root.joinpath(*artifact_category_parts(prefix))
    if create:
        return ensure_dir(group_root)
    return group_root


def artifact_task_name(prefix: str) -> str:
    return prefix.rstrip("_")


def artifact_dir_name(prefix: str, stamp: Optional[str] = None) -> str:
    stamp = stamp or datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
    return f"{stamp}_{artifact_task_name(prefix)}"


def new_artifact_dir(prefix: str, session_dir: Optional[Path] = None) -> Path:
    session_dir = session_dir or current_session_dir()
    parent = artifact_group_root(prefix, session_dir=session_dir, create=True)
    base = artifact_dir_name(prefix)
    path = parent / base
    index = 1
    while path.exists():
        path = parent / f"{base}_{index}"
        index += 1
    return ensure_dir(path)


def _artifact_task_fragment(path: Path) -> str:
    name = path.name
    match = STAMP_RE.match(name)
    if match:
        return match.group("task")
    legacy_match = LEGACY_STAMP_RE.match(name)
    if legacy_match:
        return f"{legacy_match.group('task')}{legacy_match.group('suffix') or ''}"
    return name


def _artifact_stamp(path: Path) -> str:
    name = path.name
    match = STAMP_RE.match(name)
    if match:
        return match.group("stamp")
    legacy_match = LEGACY_STAMP_RE.match(name)
    if legacy_match:
        return legacy_match.group("stamp")
    return ""


def _artifact_name_matches(path: Path, prefix: str) -> bool:
    target = artifact_task_name(prefix)
    if any(mark in target for mark in "*?[]"):
        return path.match(target)
    fragment = _artifact_task_fragment(path)
    return fragment == target or fragment.startswith(f"{target}_")


def _artifact_sort_key(path: Path) -> Tuple[str, float, str]:
    stamp = _artifact_stamp(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (stamp, mtime, path.name)


def _unique_paths(paths: Sequence[Path]) -> List[Path]:
    unique: List[Path] = []
    seen = set()
    for item in paths:
        try:
            key = str(item.resolve())
        except OSError:
            key = str(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def find_artifact_dirs(prefix: str, session_dir: Optional[Path] = None) -> List[Path]:
    session_dir = session_dir or current_session_dir()
    legacy = sorted(
        [item for item in session_dir.iterdir() if item.is_dir() and _artifact_name_matches(item, prefix)],
        key=_artifact_sort_key,
    )
    nested_root = session_artifacts_root(session_dir)
    nested: List[Path] = []
    if nested_root.exists():
        nested = sorted(
            [item for item in nested_root.rglob("*") if item.is_dir() and _artifact_name_matches(item, prefix)],
            key=_artifact_sort_key,
        )
    return _unique_paths([*legacy, *nested])


def find_artifact_files(prefix: str, file_name: str, session_dir: Optional[Path] = None) -> List[Path]:
    results: List[Path] = []
    for directory in find_artifact_dirs(prefix, session_dir=session_dir):
        candidate = directory / file_name
        if candidate.exists():
            results.append(candidate)
    return results


def _artifact_dir_index(session_dir: Path) -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    nested_root = session_artifacts_root(session_dir)
    if not nested_root.exists():
        return index
    for item in nested_root.rglob("*"):
        if item.is_dir() and _artifact_stamp(item):
            index[item.name] = item
    return index


def _resolve_artifact_alias(name: str, index: Dict[str, Path]) -> Optional[Path]:
    direct = index.get(name)
    if direct is not None:
        return direct
    legacy_match = LEGACY_STAMP_RE.match(name)
    if legacy_match:
        candidate = f"{legacy_match.group('stamp')}_{legacy_match.group('task')}{legacy_match.group('suffix') or ''}"
        return index.get(candidate)
    return None


def _resolve_session_artifact_candidate(candidate: Path, session_dir: Path, index: Dict[str, Path]) -> Optional[Path]:
    try:
        relative = candidate.relative_to(session_dir)
    except ValueError:
        return None
    if not relative.parts:
        return None
    if relative.parts[0] in {SESSION_ARTIFACTS_DIR[0], SESSION_LIVE_DIR[0]}:
        return None
    target_dir = _resolve_artifact_alias(relative.parts[0], index)
    if target_dir is None:
        return None
    return target_dir.joinpath(*relative.parts[1:])


def resolve_artifact_reference(
    path_value: object,
    session_dir: Optional[Path] = None,
    *,
    must_exist: bool = True,
) -> Optional[Path]:
    session_dir = session_dir or current_session_dir()
    raw = str(path_value or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (workspace_root() / candidate).resolve(strict=False)
    if candidate.exists():
        return candidate
    index = _artifact_dir_index(session_dir)
    resolved = _resolve_session_artifact_candidate(candidate, session_dir, index)
    if resolved is None:
        return None
    if must_exist and not resolved.exists():
        return None
    return resolved


def queue_command(port: str, command: str, session_dir: Optional[Path] = None) -> dict:
    session_dir = session_dir or current_session_dir()
    resolved_port = resolve_configured_port(port)
    payload = {
        "ts": now_iso_ms(),
        "port": resolved_port,
        "command": command,
    }
    path = queue_path(session_dir, create=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def parse_prefixed_timestamp(line: str) -> Optional[datetime]:
    if len(line) < 23:
        return None
    try:
        return datetime.fromisoformat(line[:23])
    except ValueError:
        return None


def log_path_for_port(port: str, session_dir: Optional[Path] = None) -> Path:
    session_dir = session_dir or current_session_dir()
    normalized = resolve_configured_port(port)
    name = PORT_LOGS.get(normalized, f"{normalized}.log")
    return session_live_file(name, session_dir=session_dir)


def resolve_configured_port(port: str) -> str:
    """Map legacy COM12/13/14 role names to the current local config.

    Older Polaris tools still pass the original COM names as role shorthand.
    Keeping the translation here makes those tools inherit
    the current managed session first, then the local config, without having
    to patch every caller.
    """
    normalized = str(port).strip().upper()
    role = CANONICAL_PORT_ROLES.get(normalized)
    if not role:
        return normalized
    session_port = _session_port_for_role(role)
    if session_port:
        return session_port
    try:
        from tools.core.polaris_config import get_port

        return get_port(role)
    except Exception:
        return normalized


def _session_port_for_role(role: str) -> str:
    aliases = {
        "cp": {"cp", "cskcp"},
        "asr": {"asr", "upper", "wb01", "ws63"},
        "ap": {"ap", "cskap"},
    }.get(role, {role})
    try:
        session_dir = current_session_dir()
        path = manifest_path(session_dir=session_dir)
        if not path.exists():
            return ""
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        ports = payload.get("ports", {}) if isinstance(payload, dict) else {}
        if not isinstance(ports, dict):
            return ""
        for port, meta in ports.items():
            label = str((meta or {}).get("role", "") if isinstance(meta, dict) else "").strip().lower()
            if label in aliases:
                return str(port).strip().upper()
    except Exception:
        return ""
    return ""


def read_lines_between(
    port: str,
    start_dt: datetime,
    end_dt: Optional[datetime] = None,
    session_dir: Optional[Path] = None,
) -> List[str]:
    path = log_path_for_port(port, session_dir=session_dir)
    if not path.exists():
        return []
    lines: List[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            ts = parse_prefixed_timestamp(raw)
            if ts is None:
                continue
            if ts < start_dt:
                continue
            if end_dt is not None and ts > end_dt:
                continue
            lines.append(raw.rstrip("\n"))
    return lines


def wait_for_patterns(
    port: str,
    start_dt: datetime,
    patterns: Iterable[str],
    timeout_s: float = 5.0,
    session_dir: Optional[Path] = None,
) -> Dict[str, Optional[str]]:
    deadline = time.time() + timeout_s
    wanted = list(patterns)
    matched: Dict[str, Optional[str]] = {pattern: None for pattern in wanted}
    while time.time() < deadline:
        lines = read_lines_between(port, start_dt, session_dir=session_dir)
        for line in lines:
            lower = line.lower()
            for pattern in wanted:
                if matched[pattern] is None and pattern.lower() in lower:
                    matched[pattern] = line
        if all(value is not None for value in matched.values()):
            break
        time.sleep(0.2)
    return matched


def latest_heartbeat(session_dir: Optional[Path] = None) -> dict:
    session_dir = session_dir or current_session_dir()
    path = heartbeat_path(session_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
