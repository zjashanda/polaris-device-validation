#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal resource model for local Polaris validation runs.

This module does not lock OS resources yet. It creates a deterministic snapshot
of the resources a task is going to touch, so preflight and execution records
can catch obvious conflicts before a long true-device run starts.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Tuple


@dataclass
class ResourceClaim:
    resource_type: str
    resource_id: str
    owner: str
    access: str = "exclusive"
    shared: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResourceConflict:
    resource_type: str
    resource_id: str
    owners: List[str]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResourceSnapshot:
    claims: List[ResourceClaim]
    conflicts: List[ResourceConflict]
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "claims": [item.to_dict() for item in self.claims],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "warnings": self.warnings,
        }


@dataclass
class ResourceLock:
    claim: ResourceClaim
    lock_path: str
    run_id: str
    acquired_at: float

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["claim"] = self.claim.to_dict()
        return payload


def _nested(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def _text(value: Any) -> str:
    return str(value or "").strip()


def _add_claim(claims: List[ResourceClaim], resource_type: str, resource_id: str, owner: str, **metadata: Any) -> None:
    text = _text(resource_id)
    if not text:
        return
    claims.append(ResourceClaim(resource_type=resource_type, resource_id=text.upper() if resource_type == "serial" else text, owner=owner, metadata=metadata))


def build_resource_claims(env_payload: Dict[str, Any], task: Dict[str, Any] | None = None) -> List[ResourceClaim]:
    task = task or {}
    claims: List[ResourceClaim] = []

    ports = _nested(env_payload, "serial", "ports")
    if isinstance(ports, dict):
        for role, port in ports.items():
            _add_claim(claims, "serial", port, f"serial.{role}", role=role, topology=_nested(env_payload, "serial", "topology"))

    audio_key = _text(_nested(env_payload, "audio", "default_playback_device_key"))
    if audio_key:
        _add_claim(claims, "audio", audio_key, "audio.playback", policy="explicit_device_key")
    else:
        claims.append(ResourceClaim(resource_type="audio", resource_id="DEFAULT_RENDER_DEVICE", owner="audio.playback", access="shared", shared=True, metadata={"policy": "default_device"}))

    wifi_ssid = _text(_nested(env_payload, "network", "wifi_ssid"))
    if wifi_ssid:
        _add_claim(claims, "network", wifi_ssid, "network.wifi", access="shared", shared=True)

    cloud_env = _text(_nested(env_payload, "cloud", "api_environment"))
    if cloud_env:
        _add_claim(claims, "cloud", cloud_env, "cloud.api", access="shared", shared=True, device_env=_nested(env_payload, "cloud", "device_env"))

    control = _text(_nested(env_payload, "serial", "ports", "control"))
    if control and _nested(env_payload, "serial", "control_preconditions"):
        _add_claim(claims, "power", control.upper(), "power.control", port=control.upper(), commands=_nested(env_payload, "serial", "control_preconditions"))

    schema = _text(task.get("schema"))
    if "online-stress" in schema:
        _add_claim(claims, "scenario", "online_mixed_stress", "task.online_stress", access="shared", shared=True)

    return claims


def _serial_share_allowed(owners: Iterable[str]) -> bool:
    owner_set = {owner.lower() for owner in owners}
    # WS63 often maps upper/asr to the same physical port. That is an alias,
    # not a runtime conflict.
    return owner_set <= {"serial.upper", "serial.asr"}


def detect_conflicts(claims: List[ResourceClaim]) -> List[ResourceConflict]:
    by_key: Dict[Tuple[str, str], List[ResourceClaim]] = {}
    for claim in claims:
        if claim.shared:
            continue
        by_key.setdefault((claim.resource_type, claim.resource_id), []).append(claim)

    conflicts: List[ResourceConflict] = []
    for (resource_type, resource_id), items in sorted(by_key.items()):
        owners = sorted({item.owner for item in items})
        if len(owners) <= 1:
            continue
        if resource_type == "serial" and _serial_share_allowed(owners):
            continue
        conflicts.append(
            ResourceConflict(
                resource_type=resource_type,
                resource_id=resource_id,
                owners=owners,
                reason=f"{resource_type}:{resource_id} is claimed by multiple exclusive owners",
            )
        )
    return conflicts


def build_resource_snapshot(env_payload: Dict[str, Any], task: Dict[str, Any] | None = None) -> ResourceSnapshot:
    claims = build_resource_claims(env_payload, task)
    warnings: List[str] = []
    if not _text(_nested(env_payload, "audio", "default_playback_device_key")):
        warnings.append("audio.default_playback_device_key is empty; default render device will be used")
    return ResourceSnapshot(claims=claims, conflicts=detect_conflicts(claims), warnings=warnings)


def _safe_lock_name(claim: ResourceClaim) -> str:
    material = f"{claim.resource_type}_{claim.resource_id}_{claim.owner}".lower()
    return re.sub(r"[^a-z0-9_.-]+", "_", material).strip("_") + ".lock"


def _read_lock(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class ResourceLockManager:
    """File-based local resource lock manager.

    The lock scope is intentionally local-machine only. It prevents two Polaris
    tasks in this workspace from using the same serial/audio/power resource at
    the same time without adding an external service.
    """

    def __init__(self, lock_root: Path, *, stale_after_s: int = 6 * 60 * 60) -> None:
        self.lock_root = lock_root
        self.stale_after_s = stale_after_s
        self.lock_root.mkdir(parents=True, exist_ok=True)

    def cleanup_stale(self) -> List[Dict[str, Any]]:
        removed: List[Dict[str, Any]] = []
        now = time.time()
        for path in self.lock_root.glob("*.lock"):
            payload = _read_lock(path)
            acquired_at = float(payload.get("acquired_at", 0) or 0)
            pid = int(payload.get("pid", 0) or 0)
            stale_by_age = acquired_at and now - acquired_at > self.stale_after_s
            stale_by_pid = pid and not _pid_exists(pid)
            if stale_by_age or stale_by_pid:
                removed.append({"lock_path": str(path), "payload": payload, "stale_by_age": stale_by_age, "stale_by_pid": stale_by_pid})
                path.unlink(missing_ok=True)
        return removed

    def acquire(self, claims: List[ResourceClaim], *, run_id: str = "", owner: str = "polaris") -> List[ResourceLock]:
        self.cleanup_stale()
        acquired: List[ResourceLock] = []
        run_id = run_id or uuid.uuid4().hex
        try:
            for claim in claims:
                if claim.shared:
                    continue
                path = self.lock_root / _safe_lock_name(claim)
                payload = {
                    "run_id": run_id,
                    "owner": owner,
                    "pid": os.getpid(),
                    "acquired_at": time.time(),
                    "claim": claim.to_dict(),
                }
                try:
                    with path.open("x", encoding="utf-8") as handle:
                        json.dump(payload, handle, ensure_ascii=False, indent=2)
                except FileExistsError as exc:
                    existing = _read_lock(path)
                    raise RuntimeError(f"resource locked: {claim.resource_type}:{claim.resource_id} by {existing}") from exc
                acquired.append(ResourceLock(claim=claim, lock_path=str(path), run_id=run_id, acquired_at=payload["acquired_at"]))
            return acquired
        except Exception:
            self.release(acquired)
            raise

    def release(self, locks: List[ResourceLock]) -> None:
        for lock in reversed(locks):
            path = Path(lock.lock_path)
            payload = _read_lock(path)
            if not payload or payload.get("run_id") == lock.run_id:
                path.unlink(missing_ok=True)


@contextmanager
def locked_resources(lock_root: Path, claims: List[ResourceClaim], *, run_id: str = "", owner: str = "polaris") -> Iterator[List[ResourceLock]]:
    manager = ResourceLockManager(lock_root)
    locks = manager.acquire(claims, run_id=run_id, owner=owner)
    try:
        yield locks
    finally:
        manager.release(locks)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True
