#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Randomized online-interaction stress for Polaris projects.

The runner keeps AP/CP/ASR serial readers open from the beginning and stores full
logs. It mixes wake + online command/music/crosstalk/news/cooking/encyclopedia
phrases with random gaps to look for reboot, crash, serial silence, playback
failures, and missing wake/ASR/TTS evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
BDD_ROOT = SCRIPT_DIR.parents[0]
ROOT = SCRIPT_DIR.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from polaris_env import load_env_payload, resolve_env_path  # noqa: E402
from runtime.snapshots import (  # noqa: E402
    build_snapshot,
    diff_snapshots,
    write_coverage as write_snapshot_coverage,
    write_diff as write_snapshot_diff,
    write_snapshot,
)
from tools.audio.polaris_audio_builder import build_sequence  # noqa: E402
from tools.core.polaris_adapter_bridge import (  # noqa: E402
    ManagedAdapterSession,
    run_adapter_action_capture,
    run_audio_playback_adapter,
    start_serial_logger_adapter,
    stop_serial_logger_adapter,
)
from tools.core.polaris_runtime import session_live_file  # noqa: E402
from tools.logs.polaris_interaction_trace import best_chinese_text, extract_interaction_trace  # noqa: E402


DEFAULT_STRATEGY = BDD_ROOT / "references" / "scene_strategy_pool.json"
AP_WAKE_RE = re.compile(r"(Pre Wakeup|wakeup_callback|multi_allow_wakeup_callback|mark has wakeup)", re.I)
CP_WAKE_RE = re.compile(r"\bWAKE\(1\)", re.I)
ASR_WAKE_RE = re.compile(r"\bonline_wakeup\b|offline_wakeup|wakeup", re.I)
ANY_WAKE_RE = re.compile(r"(Pre Wakeup|wakeup_callback|online_wakeup|multi_allow_wakeup_callback|mark has wakeup)", re.I)
ASR_RE = re.compile(r"((?:online|offline)_asr_callbak|MSpeech Cloud 3 evt|cloud asr with|Recv .* ASR|recognizer start)", re.I)
ASR_TEXT_RE = re.compile(r"(?:online|offline)_asr_callbak,\s*(?:text|keyword):\s*(.+)$", re.I)
CLOUD_ASR_TEXT_RE = re.compile(r'"asr"\s*:\s*"([^"]*)"', re.I)
CP_COMMAND_KEYWORD_RE = re.compile(r"WAKE\(0\).*?KEY=\d+\(([^)]*)\)", re.I)
ALGO_COMMAND_KEYWORD_RE = re.compile(r'"keyword"\s*:\s*"([^"]*)"', re.I)
LOCAL_ASR_KEYWORD_RE = re.compile(r"ignore local asr\s+(.+?)\s+when cloud connected", re.I)
PUNCT_OR_SPACE_RE = re.compile(r"[\s，。！？、,.!?:：;；\"'“”‘’（）()\[\]{}<>《》]+")
MOJIBAKE_HINT_RE = re.compile(
    r"[\u0080-\u009f\u00a0-\u00ff\ufffd"
    r"\u6c13\u5fd9\u83bd\u76f2\u8305\u732b\u832b\u8302\u951f\u7d94"
    r"\u7481\u93b5\u9477\u93b4\u621e\u935a\u9429\u934b\u93be\u9410\u947f"
    r"\ue11b\ue15e\ue15f\u3126\u6f83\ufe3e\u6b91\u5a42\u3049\u93c8"
    r"\u95ca\u95ae\u93c2\u748b\u7d86]"
    r"|(\u677c\u626e\u77c6|\u5a11\u65bf\u724a|\u59b2\u6401\u5d37|\u9361\u6b91|\u93cd\u3127\u6443|\u8f70\u7c88|\u6d94\u581f|\u69f8\u9366|\u55d9\u6b91|\u6828\u74d5)"
)
TTS_RE = re.compile(r"(TTS playing|TTS recv|ttsplayer play|wakeup_tts_callback|shortplayer status|cloud\.instructions\.audioBroadcast|tone player)", re.I)
CLOUD_REPLY_RE = re.compile(r"(cloud\.speech\.reply|cloud\.instructions|MSpeech Cloud 4 evt|MSpeech Cloud 32 evt)", re.I)
MEDIA_PLAY_RE = re.compile(r"(ttsplayer play|play audio https?://|player\".*\"status\":\"play\"|ttsplayer report state: play|TTS playing)", re.I)
MEDIA_STOP_RE = re.compile(r"(player\".*\"status\":\"stop\"|ttsplayer report state: stop|PLAYBACK_COMPLETE|ttsplayer status:\s*6)", re.I)
MEDIA_ERROR_RE = re.compile(
    r"(\[E\]\s*\[http\].*(recv timeout|retry|fail|error)|"
    r"\[HTTPC\]\[ERR\]|"
    r"\[W\]\s*\[http_retry\].*(read_failed|retry)|"
    r"\b(http|https).*(download|demux|play).*(fail|error|timeout)|"
    r"\b(demux|download|decoder|player).*(fail|error|timeout))",
    re.I,
)
BOOT_RE = re.compile(
    r"(Boot Reason|boot reason|ListenAI .*BOOT|RESET=|ASSERT|panic|fatal|watchdog|hard fault|exception|will reboot device|reboot_reason)",
    re.I,
)
BOOT_IGNORE_RE = re.compile(r"ignore exception", re.I)
SERIAL_ERR_RE = re.compile(r"LOGGER_ERROR", re.I)
LATENCY_FIELDS = [
    "wake_to_recognition_ms",
    "wake_to_cloud_request_ms",
    "wake_to_first_cloud_response_ms",
    "wake_to_tts_start_ms",
    "wake_to_media_start_ms",
    "cloud_request_to_recognition_ms",
    "recognition_to_cloud_request_ms",
    "cloud_request_to_first_cloud_response_ms",
    "cloud_request_to_audio_broadcast_ms",
    "cloud_request_to_speech_reply_ms",
    "cloud_request_to_tts_start_ms",
    "cloud_request_to_media_start_ms",
    "recognition_to_first_cloud_response_ms",
    "recognition_to_audio_broadcast_ms",
    "recognition_to_speech_reply_ms",
    "recognition_to_tts_start_ms",
    "recognition_to_media_start_ms",
    "first_cloud_response_to_tts_start_ms",
    "first_cloud_response_to_media_start_ms",
    "audio_broadcast_to_tts_start_ms",
    "audio_broadcast_to_media_start_ms",
    "tts_start_to_media_start_ms",
    "tts_or_media_play_duration_ms",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except Exception:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def first_latency_value(metrics: Dict[str, Any], key: str) -> Any:
    for sample in metrics.get("latency_samples") or []:
        latency = sample.get("latency", {}) if isinstance(sample, dict) else {}
        if isinstance(latency, dict) and latency.get(key) not in (None, ""):
            return latency.get(key)
    return ""


def smart_decode(data: bytes) -> str:
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_datetime(value: str) -> datetime:
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(raw)


def tomorrow_0830() -> datetime:
    now = datetime.now()
    target = now.replace(hour=8, minute=30, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


@dataclass
class CapturedLine:
    wall: str
    mono: float
    port: str
    name: str
    text: str
    line_no: int = 0


@dataclass
class SerialState:
    name: str
    port: str
    baudrate: int
    is_open: bool = False
    line_count: int = 0
    bytes_read: int = 0
    last_error: Optional[str] = None


class HarnessLogReader:
    def __init__(self, name: str, port: str, baudrate: int, session_dir: Path) -> None:
        self.name = name
        self.port = port
        self.baudrate = baudrate
        # The serial logger writes under session/logs/live.  Resolve the live
        # path up front, otherwise a freshly created session falls back to the
        # legacy session/COMxx.log path and every round is misread as silent.
        self.log_path = session_live_file(f"{port}.log", session_dir=session_dir, create=True)
        self.state = SerialState(name=name, port=port, baudrate=baudrate)

    def start(self) -> None:
        self.state.is_open = self.log_path.parent.exists()

    def stop(self) -> None:
        self._refresh_state()

    def snapshot_len(self) -> int:
        return len(self._load_entries())

    def since(self, index: int) -> List[CapturedLine]:
        return self._load_entries()[index:]

    def _refresh_state(self) -> None:
        try:
            self.state.bytes_read = self.log_path.stat().st_size if self.log_path.exists() else 0
        except OSError:
            self.state.bytes_read = 0
        self.state.is_open = self.log_path.parent.exists()

    def _load_entries(self) -> List[CapturedLine]:
        self._refresh_state()
        if not self.log_path.exists():
            return []
        entries: List[CapturedLine] = []
        with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, raw in enumerate(handle):
                line = raw.rstrip("\n")
                if len(line) < 24:
                    continue
                wall = line[:23]
                rest = line[24:] if len(line) > 24 else ""
                text = rest
                match = re.match(r"\[(?P<port>[^/]+)/(?P<role>[^\]]+)\]\s*(?P<text>.*)$", rest)
                if match:
                    text = match.group("text")
                try:
                    mono = datetime.fromisoformat(wall).timestamp() + (index / 1000000.0)
                except ValueError:
                    mono = float(index)
                entries.append(CapturedLine(wall=wall, mono=mono, port=self.port, name=self.name, text=text, line_no=index + 1))
                if "LOGGER_ERROR" in text:
                    self.state.last_error = text
        self.state.line_count = len(entries)
        return entries


def adapter_result_dict(name: str, result: Any, started: datetime, duration_start: float) -> Dict[str, Any]:
    return {
        "cmd": result.cmd,
        "started_at": started.isoformat(timespec="milliseconds"),
        "duration_s": round(time.perf_counter() - duration_start, 3),
        "returncode": result.returncode if result.returncode is not None else (-1 if result.result in {"FAIL", "BLOCKED"} else 0),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "result": result.result,
        "adapter": result.to_dict(),
        "name": name,
    }


def play_audio(audio_file: Path, device_key: str, skip_probe: bool = True) -> Dict[str, Any]:
    started = datetime.now()
    start_mono = time.perf_counter()
    capture = run_audio_playback_adapter(audio_file, device_key, skip_probe=skip_probe, timeout_s=120)
    return {
        "cmd": list(capture.completed.args),
        "started_at": started.isoformat(timespec="milliseconds"),
        "duration_s": round(time.perf_counter() - start_mono, 3),
        "returncode": capture.completed.returncode,
        "stdout": capture.completed.stdout,
        "stderr": capture.completed.stderr,
        "adapter": capture.action_result.to_dict(),
    }


def count_re(entries: Iterable[CapturedLine], regex: re.Pattern[str]) -> int:
    return sum(1 for item in entries if regex.search(item.text))


def sample_re(entries: Iterable[CapturedLine], regex: re.Pattern[str], limit: int = 10) -> List[str]:
    samples: List[str] = []
    for item in entries:
        if regex.search(item.text):
            samples.append(f"{item.wall} [{item.port}/{item.name}] {item.text}")
            if len(samples) >= limit:
                break
    return samples


def is_boot_or_crash_line(text: str) -> bool:
    return bool(BOOT_RE.search(text)) and not BOOT_IGNORE_RE.search(text)


def count_boot_or_crash(entries: Iterable[CapturedLine]) -> int:
    return sum(1 for item in entries if is_boot_or_crash_line(item.text))


def sample_boot_or_crash(entries: Iterable[CapturedLine], limit: int = 10) -> List[str]:
    samples: List[str] = []
    for item in entries:
        if is_boot_or_crash_line(item.text):
            samples.append(f"{item.wall} [{item.port}/{item.name}] {item.text}")
            if len(samples) >= limit:
                break
    return samples


def extract_texts(entries: Iterable[CapturedLine]) -> List[str]:
    values: List[str] = []
    seen_norms: set[str] = set()
    for item in entries:
        match = ASR_TEXT_RE.search(item.text) or CLOUD_ASR_TEXT_RE.search(item.text)
        if match:
            text = best_chinese_text(match.group(1).strip())
            norm = normalize_recognition_text(text)
            if text and norm not in seen_norms:
                values.append(text)
                seen_norms.add(norm)
    clean_values = [text for text in values if text and not looks_like_mojibake(text)]
    if clean_values:
        return [text for text in values if not looks_like_mojibake(text)]
    return values


def extract_command_keywords(entries: Iterable[CapturedLine]) -> List[str]:
    values: List[str] = []
    for item in entries:
        for regex in (CP_COMMAND_KEYWORD_RE, LOCAL_ASR_KEYWORD_RE, ALGO_COMMAND_KEYWORD_RE):
            match = regex.search(item.text)
            if not match:
                continue
            text = best_chinese_text(match.group(1).strip())
            if text and text not in values and "xiao mei xiao mei" not in text.lower():
                values.append(text)
    return values


def normalize_recognition_text(text: str) -> str:
    return PUNCT_OR_SPACE_RE.sub("", str(text or "").lower())


def recognition_matches_expected(observed: str, expected_values: Iterable[str]) -> bool:
    observed_norms = [normalize_recognition_text(value) for value in mojibake_variants(observed)]
    observed_norms = [value for value in observed_norms if value]
    if not observed_norms:
        return True
    for expected in expected_values:
        expected_norms = [normalize_recognition_text(value) for value in mojibake_variants(expected)]
        expected_norms = [value for value in expected_norms if value]
        for observed_norm in observed_norms:
            for expected_norm in expected_norms:
                if observed_norm == expected_norm or observed_norm in expected_norm or expected_norm in observed_norm:
                    return True
    return False


def mojibake_variants(text: str) -> List[str]:
    raw = str(text or "")
    variants = [raw]
    for encoding in ("latin1", "cp1252"):
        try:
            recovered = raw.encode(encoding).decode("utf-8")
        except Exception:
            continue
        if recovered and recovered not in variants:
            variants.append(recovered)
    return variants


def looks_like_mojibake(text: str) -> bool:
    return bool(MOJIBAKE_HINT_RE.search(str(text or "")))


def find_unexpected_texts(observed_values: Iterable[str], expected_values: Iterable[str]) -> List[str]:
    expected_list = list(expected_values)
    has_expected_match = any(recognition_matches_expected(observed, expected_list) for observed in observed_values)
    unexpected: List[str] = []
    for observed in observed_values:
        text = str(observed or "").strip()
        if not text:
            continue
        if not recognition_matches_expected(text, expected_list) and text not in unexpected:
            # Some firmware logs contain the same cloud ASR twice: one line is
            # valid UTF-8 and another is mojibake.  If this round already has a
            # clean expected match, do not count the mojibake duplicate as a
            # false recognition.
            if has_expected_match and looks_like_mojibake(text):
                continue
            unexpected.append(text)
    return unexpected


def entries_to_lines(entries: Iterable[CapturedLine]) -> List[str]:
    return [f"{item.wall} [{item.port}/{item.name}] {item.text}" for item in entries]


def is_media_error_line(text: str) -> bool:
    if "PA_MGR" in text and "Refresh PA to OFF" in text:
        return False
    return bool(MEDIA_ERROR_RE.search(text))


def count_media_errors(entries: Iterable[CapturedLine]) -> int:
    return sum(1 for item in entries if is_media_error_line(item.text))


def sample_media_errors(entries: Iterable[CapturedLine], limit: int = 10) -> List[str]:
    samples: List[str] = []
    for item in entries:
        if is_media_error_line(item.text):
            samples.append(f"{item.wall} [{item.port}/{item.name}] {item.text}")
            if len(samples) >= limit:
                break
    return samples


class StressRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.run_dir = Path(args.run_dir).resolve() if args.run_dir else (
            BDD_ROOT / "debug" / "online_mixed_stress" / stamp()
        )
        self.logs_dir = self.run_dir / "logs"
        self.audio_dir = self.run_dir / "audio"
        self.rounds_dir = self.run_dir / "rounds"
        self.session_dir = self.run_dir / "session"
        self.serial_session: Optional[ManagedAdapterSession] = None
        self.readers = [
            HarnessLogReader("ap", args.ap_port, args.data_baud, self.session_dir),
            HarnessLogReader("asr", args.asr_port, args.data_baud, self.session_dir),
        ]
        if args.cp_port:
            self.readers.insert(1, HarnessLogReader("cp", args.cp_port, args.data_baud, self.session_dir))
        self.rng = random.Random(args.seed)
        self.results: List[Dict[str, Any]] = []
        self.counts: Dict[str, int] = {}
        self.category_counts: Dict[str, int] = {}
        self.anomaly_counts: Dict[str, int] = {}
        self.control_actions: List[Dict[str, Any]] = []
        self.round_csv_path = self.run_dir / "rounds.csv"
        self.stop_requested = False
        self.dump_indices: Dict[str, int] = {}
        self.strategy = getattr(args, "strategy_payload", {}) or {}
        self.phrases = self._build_phrase_bank()
        self.category_bag_template = self._build_category_bag()
        self.category_bag_cache: Dict[int, List[str]] = {}
        self.before_snapshot: Optional[Dict[str, Any]] = None
        self.snapshot_refs: Dict[str, Any] = {}
        self.snapshot_events: List[Dict[str, Any]] = []

    def _build_phrase_bank(self) -> Dict[str, List[str]]:
        strategy_phrases = self.strategy.get("phrases") if isinstance(self.strategy.get("phrases"), dict) else {}
        if strategy_phrases:
            return {str(key): [str(item) for item in value] for key, value in strategy_phrases.items() if isinstance(value, list)}
        return {
            "basic_command": [
                "打开空调",
                "关闭空调",
                "调高温度",
                "调低温度",
                "设置温度到二十六度",
                "打开制冷",
                "打开除湿",
                "打开自动模式",
            ],
            "music": [
                "播放音乐",
                "播放一首歌",
                "播放周杰伦的歌",
                "下一首",
                "暂停播放",
                "继续播放",
                "停止播放",
            ],
            "crosstalk": [
                "播放相声",
                "我想听相声",
                "播放郭德纲的相声",
                "来一段相声",
            ],
            "news": [
                "播放新闻",
                "今天有什么新闻",
                "播报今日新闻",
                "来一段新闻",
                "播放财经新闻",
                "播放体育新闻",
            ],
            "qa_cooking": [
                "红烧肉怎么做",
                "番茄炒蛋怎么做",
                "宫保鸡丁怎么做",
                "土豆丝怎么炒",
                "炒青菜怎么做好吃",
                "水煮鱼怎么做",
            ],
            "qa_encyclopedia": [
                "太阳为什么会发光",
                "长城有多长",
                "地球为什么是圆的",
                "恐龙为什么灭绝了",
                "人工智能是什么",
                "珠穆朗玛峰有多高",
            ],
        }

    def _build_category_bag(self) -> List[str]:
        bag_items = self.strategy.get("bag") if isinstance(self.strategy.get("bag"), list) else []
        result: List[str] = []
        for item in bag_items:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category", "")).strip()
            weight = int(item.get("weight", 0) or 0)
            if category and weight > 0:
                result.extend([category] * weight)
        if result:
            return result
        return (
            ["basic_command"] * 4
            + ["music"] * 3
            + ["crosstalk"] * 3
            + ["news"] * 3
            + ["qa_cooking", "qa_encyclopedia", "combo"]
        )

    def run(self) -> int:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        end_at = parse_datetime(self.args.end_at) if self.args.end_at else tomorrow_0830()
        started_at = datetime.now()
        self._write_metadata(started_at, end_at)
        self.before_snapshot = self._capture_snapshot("before", file_name="snapshot_before.json", extra_state={"planned_end_at": end_at.isoformat(timespec="seconds")})
        self._init_csv()
        try:
            self._start_readers()
            preflight = self._preflight()
            if not preflight.get("audio_ready") or not preflight.get("serial_ready"):
                self._write_heartbeat("BLOCKED", end_at, preflight)
                return 2
            self._stress_loop(end_at, preflight)
        finally:
            for reader in self.readers:
                reader.stop()
            if self.serial_session is not None:
                try:
                    stop_serial_logger_adapter(self.serial_session)
                finally:
                    self.serial_session = None
            self._write_final_summary(started_at, end_at)
        return 0

    def _snapshot_task(self) -> Dict[str, Any]:
        return {
            "schema": "polaris.online-stress.task.v1",
            "task_id": "online_mixed_stress",
            "expected_state": {
                "cloud_link": "expected",
                "wake_evidence": "expected",
                "media_response": "expected_or_warn",
            },
            "execution": {
                "max_rounds": self.args.max_rounds,
                "summary_every": self.args.summary_every,
                "sample_window_every": self.args.sample_window_every,
            },
        }

    def _snapshot_env(self) -> Tuple[Path, Dict[str, Any]]:
        env_path = resolve_env_path(getattr(self.args, "env_file_resolved", "") or self.args.env_file, ROOT)
        return env_path, load_env_payload(env_path)

    def _capture_snapshot(self, phase: str, *, file_name: str = "", extra_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        env_path, env_payload = self._snapshot_env()
        snapshot = build_snapshot(
            env_path=env_path,
            env_payload=env_payload,
            task=self._snapshot_task(),
            phase=phase,
            run_dir=self.run_dir,
            evidence_roots=[self.run_dir],
            scope={"runner": "run_online_mixed_stress", "project_id": self.args.project_id},
            extra_state=extra_state,
        )
        path = write_snapshot(self.run_dir, snapshot, file_name=file_name or None)
        self.snapshot_events.append({"phase": phase, "path": rel(path), "created_at": now_iso()})
        return snapshot

    def _capture_round_snapshot(self, round_index: int, result: str, *, incident: bool = False) -> str:
        phase_prefix = "incident" if incident else "checkpoint"
        phase = f"{phase_prefix}_{round_index:05d}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', result.lower()).strip('_') or 'round'}"
        snapshot = self._capture_snapshot(
            phase,
            file_name=f"snapshot_{phase}.json",
            extra_state={
                "round_index": round_index,
                "round_result": result,
                "result_counts": self.counts,
                "anomaly_counts": self.anomaly_counts,
            },
        )
        return rel(self.run_dir / f"snapshot_{phase}.json")

    def _write_metadata(self, started_at: datetime, end_at: datetime) -> None:
        payload = {
            "project_id": self.args.project_id,
            "pid": os.getpid(),
            "started_at": started_at.isoformat(timespec="seconds"),
            "planned_end_at": end_at.isoformat(timespec="seconds"),
            "run_dir": str(self.run_dir),
            "serial": {
                "ap": f"{self.args.ap_port}@{self.args.data_baud}",
                "cp": f"{self.args.cp_port}@{self.args.data_baud}" if self.args.cp_port else "",
                "asr": f"{self.args.asr_port}@{self.args.data_baud}",
                "control": f"{self.args.control_port}@{self.args.control_baud}",
            },
            "env_file": getattr(self.args, "env_file_resolved", ""),
            "device_key": self.args.device_key,
            "wake_text": self.args.wake_text,
            "random_gap_s": [self.args.min_gap_s, self.args.max_gap_s],
            "observe_s": [self.args.min_observe_s, self.args.max_observe_s],
            "categories": self.phrases,
            "category_strategy": {
                "type": self.strategy.get("mode", "random_balanced_bag"),
                "strategy_name": getattr(self.args, "strategy_name", "online_mixed_stress"),
                "strategy_file": getattr(self.args, "strategy_file", ""),
                "bag_size": len(self.category_bag_template),
                "bag_template": self.category_bag_template,
                "note": "每 16 轮先按配比装袋再随机打散，既随机又保底覆盖命令词、音乐、相声、新闻、问答和组合交互。",
            },
        }
        write_json(self.run_dir / "metadata.json", payload)
        (self.run_dir / "runner.pid").write_text(str(os.getpid()), encoding="utf-8")

    def _init_csv(self) -> None:
        self.round_csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self.round_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "round",
                "started_at",
                "finished_at",
                "category",
                "phrase",
                "result",
                "playback_returncode",
                "line_count",
                "ap_wake_count",
                "upper_wake_count",
                "cp_wake_count",
                "asr_wake_count",
                "asr_count",
                "tts_count",
                "cloud_reply_count",
                "media_play_count",
                "media_stop_count",
                "media_error_count",
                "boot_count",
                "serial_error_count",
                "asr_texts",
                "command_keywords",
                "expected_utterances",
                "unexpected_asr_texts",
                "unexpected_recognition_count",
                "asr_pinyin_texts",
                "online_mids",
                "online_session_ids",
                "online_record_ids",
                *LATENCY_FIELDS,
                "latency_samples",
                "wake_recognition_pairs",
                "observe_s",
                "next_gap_s",
                "reason",
            ])
            writer.writeheader()

    def _start_readers(self) -> None:
        self.serial_session = start_serial_logger_adapter(
            self.session_dir,
            env_file=getattr(self.args, "env_file_resolved", "") or self.args.env_file,
            log_path=self.logs_dir / "adapter_serial_logger.log",
        )
        for reader in self.readers:
            reader.start()
        time.sleep(1.2)

    def _preflight(self) -> Dict[str, Any]:
        ensure_start = datetime.now()
        ensure_mono = time.perf_counter()
        ensure_result = run_adapter_action_capture(
            adapter_id="audio.playback",
            action="ensure_laid",
            device_key=self.args.device_key,
            timeout_s=60,
            execute=True,
            allow_side_effects=True,
            log_path=self.logs_dir / "audio_ensure_laid.log",
        )
        ensure = adapter_result_dict("audio.ensure_laid", ensure_result, ensure_start, ensure_mono)
        probe_start = datetime.now()
        probe_mono = time.perf_counter()
        probe_result = run_adapter_action_capture(
            adapter_id="audio.playback",
            action="probe",
            device_key=self.args.device_key,
            timeout_s=60,
            execute=True,
            allow_side_effects=True,
            log_path=self.logs_dir / "audio_probe.log",
        )
        probe = adapter_result_dict("audio.probe", probe_result, probe_start, probe_mono)
        control_log = self.logs_dir / f"{self.args.control_port}_control.log"
        pa_on_start = datetime.now()
        pa_on_mono = time.perf_counter()
        pa_on_result = run_adapter_action_capture(
            adapter_id="control.serial",
            action="pa_on",
            timeout_s=30,
            execute=True,
            allow_side_effects=True,
            log_path=control_log,
        )
        self.control_actions.append(adapter_result_dict("control.pa_on", pa_on_result, pa_on_start, pa_on_mono))
        time.sleep(0.4)
        pa_persist_start = datetime.now()
        pa_persist_mono = time.perf_counter()
        pa_persist_result = run_adapter_action_capture(
            adapter_id="control.serial",
            action="pa_persist",
            timeout_s=30,
            execute=True,
            allow_side_effects=True,
            log_path=control_log.with_name(control_log.stem + "_persist.log"),
        )
        self.control_actions.append(adapter_result_dict("control.pa_persist", pa_persist_result, pa_persist_start, pa_persist_mono))
        time.sleep(0.4)
        payload = {
            "ensure_laid": ensure,
            "probe": probe,
            "audio_ready": ensure_result.result == "PASS" and probe_result.result == "PASS",
            "serial_ready": all(reader.state.is_open for reader in self.readers),
            "control_ready": all(item.get("result") == "PASS" for item in self.control_actions),
            "control_actions": self.control_actions,
        }
        write_json(self.run_dir / "preflight.json", payload)
        return payload

    def _choose_interaction(self, round_index: int) -> Tuple[str, str, List[Dict[str, Any]]]:
        # Randomize each block, while the weighted bag prevents long runs from drifting into mostly Q&A.
        categories = ["basic_command", "music", "crosstalk", "news", "qa_cooking", "qa_encyclopedia"]
        block_index = (round_index - 1) // len(self.category_bag_template)
        offset = (round_index - 1) % len(self.category_bag_template)
        bag = self.category_bag_cache.get(block_index)
        if bag is None:
            bag = list(self.category_bag_template)
            self.rng.shuffle(bag)
            self.category_bag_cache[block_index] = bag
        category = bag[offset]
        if category == "combo":
            first_cat = self.rng.choice(categories)
            second_cat = self.rng.choice([item for item in categories if item != first_cat])
            first = self.rng.choice(self.phrases[first_cat])
            second = self.rng.choice(self.phrases[second_cat])
            mid_gap = self.rng.randint(4500, 11000)
            phrase = f"{first} -> {second}"
            sequence = [
                {"type": "tts", "text": self.args.wake_text},
                {"type": "silence", "duration_ms": self.rng.randint(900, 2200)},
                {"type": "tts", "text": first},
                {"type": "silence", "duration_ms": mid_gap},
                {"type": "tts", "text": self.args.wake_text},
                {"type": "silence", "duration_ms": self.rng.randint(900, 2200)},
                {"type": "tts", "text": second},
            ]
            return category, phrase, sequence
        phrase = self.rng.choice(self.phrases[category])
        sequence = [
            {"type": "tts", "text": self.args.wake_text},
            {"type": "silence", "duration_ms": self.rng.randint(900, 2400)},
            {"type": "tts", "text": phrase},
        ]
        return category, phrase, sequence

    def _snapshot(self) -> Dict[str, int]:
        return {reader.name: reader.snapshot_len() for reader in self.readers}

    def _entries_since(self, snap: Dict[str, int]) -> List[CapturedLine]:
        entries: List[CapturedLine] = []
        for reader in self.readers:
            entries.extend(reader.since(snap.get(reader.name, 0)))
        entries.sort(key=lambda item: item.mono)
        return entries

    def _metrics(self, entries: List[CapturedLine]) -> Dict[str, Any]:
        ap_entries = [item for item in entries if item.name == "ap"]
        cp_entries = [item for item in entries if item.name == "cp"]
        asr_entries = [item for item in entries if item.name == "asr"]
        interaction_trace = extract_interaction_trace(entries_to_lines(entries))
        return {
            "line_count": len(entries),
            "ap_line_count": len(ap_entries),
            "cp_line_count": len(cp_entries),
            "asr_line_count": len(asr_entries),
            "ap_wake_count": count_re(ap_entries, AP_WAKE_RE),
            "cp_wake_count": count_re(cp_entries, CP_WAKE_RE),
            "asr_wake_count": count_re(asr_entries, ASR_WAKE_RE),
            "any_wake_count": count_re(entries, ANY_WAKE_RE),
            "asr_count": count_re(entries, ASR_RE),
            "tts_count": count_re(entries, TTS_RE),
            "cloud_reply_count": count_re(entries, CLOUD_REPLY_RE),
            "media_play_count": count_re(entries, MEDIA_PLAY_RE),
            "media_stop_count": count_re(entries, MEDIA_STOP_RE),
            "media_error_count": count_media_errors(entries),
            "boot_count": count_boot_or_crash(entries),
            "serial_error_count": count_re(entries, SERIAL_ERR_RE),
            "asr_texts": extract_texts(entries),
            "asr_pinyin_texts": [
                str(item.get("asr_pinyin", ""))
                for item in interaction_trace.get("recognition_events", [])
                if str(item.get("asr_pinyin", "")).strip()
            ],
            "command_keywords": extract_command_keywords(entries),
            "interaction_trace": interaction_trace,
            "online_request_ids": interaction_trace.get("online_request_ids", []),
            "latency_samples": interaction_trace.get("latency_samples", []),
            "samples": {
                "wake": sample_re(entries, ANY_WAKE_RE),
                "asr": sample_re(entries, ASR_RE),
                "tts": sample_re(entries, TTS_RE),
                "cloud_reply": sample_re(entries, CLOUD_REPLY_RE),
                "media_play": sample_re(entries, MEDIA_PLAY_RE),
                "media_stop": sample_re(entries, MEDIA_STOP_RE),
                "media_error": sample_media_errors(entries),
                "boot": sample_boot_or_crash(entries),
                "serial_error": sample_re(entries, SERIAL_ERR_RE),
            },
        }

    def _classify_round(self, playback: Dict[str, Any], metrics: Dict[str, Any]) -> Tuple[str, str]:
        if playback.get("returncode") != 0:
            return "BLOCKED_PLAYBACK", f"播放失败 returncode={playback.get('returncode')}"
        if metrics["serial_error_count"] > 0:
            return "FAIL_SERIAL_ERROR", "串口 reader 出现错误。"
        if metrics["boot_count"] > 0:
            return "FAIL_REBOOT_OR_CRASH", "窗口内观察到 reboot/crash/reset 类标记。"
        if metrics["line_count"] <= 0:
            return "BLOCKED_SERIAL_SILENT", "窗口内没有串口日志。"
        if metrics["ap_wake_count"] <= 0 and metrics["cp_wake_count"] <= 0 and metrics["asr_wake_count"] <= 0:
            return "FAIL_NO_WAKE", "播放成功但没有唤醒 marker。"
        if metrics["asr_count"] <= 0 and not metrics["asr_texts"]:
            return "WARN_NO_ASR", "有唤醒但没有 ASR 文本/云端识别证据。"
        if metrics.get("unexpected_asr_texts"):
            return "WARN_UNEXPECTED_RECOGNITION", "观察到与本轮播放语料不匹配的 ASR 文本，需按误识别复核。"
        if metrics["media_error_count"] > 0:
            return "WARN_MEDIA_ERROR", "有在线响应链路，但窗口内出现媒体/HTTP 播放错误标记。"
        if metrics["cloud_reply_count"] <= 0 and metrics["tts_count"] <= 0 and metrics["media_play_count"] <= 0:
            return "WARN_NO_ONLINE_RESPONSE", "有唤醒/ASR，但缺少 cloud reply/TTS/media 播放证据。"
        return "PASS", "唤醒/ASR/云端响应或媒体播放链路有证据。"

    def _stress_loop(self, end_at: datetime, preflight: Dict[str, Any]) -> None:
        round_index = 0
        while datetime.now() < end_at:
            if self.args.max_rounds and round_index >= self.args.max_rounds:
                break
            round_index += 1
            category, phrase, sequence = self._choose_interaction(round_index)
            observe_s = self.rng.uniform(self.args.min_observe_s, self.args.max_observe_s)
            next_gap_s = self.rng.uniform(self.args.min_gap_s, self.args.max_gap_s)
            round_dir = self.rounds_dir / f"{round_index:05d}_{category}"
            round_dir.mkdir(parents=True, exist_ok=True)
            audio_file = self.audio_dir / f"{round_index:05d}_{category}.wav"
            manifest = build_sequence(sequence, audio_file)
            snap = self._snapshot()
            started_at = now_iso()
            playback = play_audio(audio_file, self.args.device_key, skip_probe=True)
            time.sleep(observe_s)
            entries = self._entries_since(snap)
            metrics = self._metrics(entries)
            expected_utterances = [
                str(item.get("text", "")).strip()
                for item in sequence
                if isinstance(item, dict)
                and item.get("type") == "tts"
                and str(item.get("text", "")).strip()
                and str(item.get("text", "")).strip() != self.args.wake_text
            ]
            metrics["expected_utterances"] = expected_utterances
            metrics["unexpected_asr_texts"] = find_unexpected_texts(metrics.get("asr_texts", []), expected_utterances)
            metrics["unexpected_recognition_count"] = len(metrics["unexpected_asr_texts"])
            result, reason = self._classify_round(playback, metrics)
            payload = {
                "round": round_index,
                "started_at": started_at,
                "finished_at": now_iso(),
                "category": category,
                "phrase": phrase,
                "sequence": sequence,
                "audio_file": str(audio_file),
                "audio_manifest": manifest,
                "observe_s": round(observe_s, 3),
                "next_gap_s": round(next_gap_s, 3),
                "playback": playback,
                "result": result,
                "reason": reason,
                "metrics": metrics,
            }
            self._store_round(round_dir, entries, payload)
            self._record_round(payload)
            if result.startswith("FAIL") or result.startswith("BLOCKED") or result.startswith("WARN"):
                self.anomaly_counts[result] = self.anomaly_counts.get(result, 0) + 1
                payload["snapshot_incident"] = self._capture_round_snapshot(round_index, result, incident=True)
            if round_index % self.args.summary_every == 0 or result != "PASS":
                payload["snapshot_checkpoint"] = self._capture_round_snapshot(round_index, result, incident=False)
                write_json(round_dir / "result.json", payload)
                self._write_heartbeat("RUNNING", end_at, preflight)
            time.sleep(next_gap_s)
        self._write_heartbeat("FINISHING", end_at, preflight)

    def _store_round(self, round_dir: Path, entries: List[CapturedLine], payload: Dict[str, Any]) -> None:
        self._append_full_log_mirror()
        write_json(round_dir / "result.json", payload)
        if payload["result"] != "PASS" or payload["round"] % self.args.sample_window_every == 0:
            (round_dir / "window.log").write_text("\n".join(entries_to_lines(entries)) + ("\n" if entries else ""), encoding="utf-8")

    def _append_full_log_mirror(self) -> None:
        """Persist new in-memory serial entries again at round boundaries.

        The primary full logs are written directly by each serial thread. This
        mirror gives us a second complete-on-round-boundary copy so a mid-run
        crash does not hide prior serial evidence.
        """
        for reader in self.readers:
            start = self.dump_indices.get(reader.name, 0)
            new_entries = reader.since(start)
            if not new_entries:
                continue
            mirror = self.logs_dir / f"{reader.port}_{reader.name}.full.mirror.log"
            with mirror.open("a", encoding="utf-8", newline="") as handle:
                for line in entries_to_lines(new_entries):
                    handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.dump_indices[reader.name] = start + len(new_entries)

    def _record_round(self, payload: Dict[str, Any]) -> None:
        self.results.append(payload)
        result = payload["result"]
        category = payload["category"]
        self.counts[result] = self.counts.get(result, 0) + 1
        self.category_counts[category] = self.category_counts.get(category, 0) + 1
        metrics = payload["metrics"]
        with self.round_csv_path.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "round",
                "started_at",
                "finished_at",
                "category",
                "phrase",
                "result",
                "playback_returncode",
                "line_count",
                "ap_wake_count",
                "upper_wake_count",
                "cp_wake_count",
                "asr_wake_count",
                "asr_count",
                "tts_count",
                "cloud_reply_count",
                "media_play_count",
                "media_stop_count",
                "media_error_count",
                "boot_count",
                "serial_error_count",
                "asr_texts",
                "command_keywords",
                "expected_utterances",
                "unexpected_asr_texts",
                "unexpected_recognition_count",
                "asr_pinyin_texts",
                "online_mids",
                "online_session_ids",
                "online_record_ids",
                *LATENCY_FIELDS,
                "latency_samples",
                "wake_recognition_pairs",
                "observe_s",
                "next_gap_s",
                "reason",
            ])
            writer.writerow({
                "round": payload["round"],
                "started_at": payload["started_at"],
                "finished_at": payload["finished_at"],
                "category": category,
                "phrase": payload["phrase"],
                "result": result,
                "playback_returncode": payload["playback"].get("returncode"),
                "line_count": metrics.get("line_count"),
                "ap_wake_count": metrics.get("ap_wake_count"),
                "upper_wake_count": metrics.get("asr_wake_count"),
                "cp_wake_count": metrics.get("cp_wake_count"),
                "asr_wake_count": metrics.get("asr_wake_count"),
                "asr_count": metrics.get("asr_count"),
                "tts_count": metrics.get("tts_count"),
                "cloud_reply_count": metrics.get("cloud_reply_count"),
                "media_play_count": metrics.get("media_play_count"),
                "media_stop_count": metrics.get("media_stop_count"),
                "media_error_count": metrics.get("media_error_count"),
                "boot_count": metrics.get("boot_count"),
                "serial_error_count": metrics.get("serial_error_count"),
                "asr_texts": "|".join(metrics.get("asr_texts") or []),
                "command_keywords": "|".join(metrics.get("command_keywords") or []),
                "expected_utterances": "|".join(metrics.get("expected_utterances") or []),
                "unexpected_asr_texts": "|".join(metrics.get("unexpected_asr_texts") or []),
                "unexpected_recognition_count": metrics.get("unexpected_recognition_count", 0),
                "asr_pinyin_texts": "|".join(metrics.get("asr_pinyin_texts") or []),
                "online_mids": "|".join(str(item.get("mid", "")) for item in (metrics.get("online_request_ids") or []) if item.get("mid")),
                "online_session_ids": "|".join(str(item.get("sessionId", "")) for item in (metrics.get("online_request_ids") or []) if item.get("sessionId")),
                "online_record_ids": "|".join(str(item.get("recordId", "")) for item in (metrics.get("online_request_ids") or []) if item.get("recordId")),
                **{field: first_latency_value(metrics, field) for field in LATENCY_FIELDS},
                "latency_samples": json.dumps((metrics.get("latency_samples") or [])[:5], ensure_ascii=False, separators=(",", ":")),
                "wake_recognition_pairs": json.dumps(
                    (metrics.get("interaction_trace") or {}).get("interactions", [])[:5],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "observe_s": payload["observe_s"],
                "next_gap_s": payload["next_gap_s"],
                "reason": payload["reason"],
            })

    def _summary_payload(self, status: str, end_at: datetime, preflight: Dict[str, Any]) -> Dict[str, Any]:
        elapsed_s = None
        if self.results:
            try:
                first = parse_datetime(self.results[0]["started_at"])
                elapsed_s = round((datetime.now() - first).total_seconds(), 1)
            except Exception:
                pass
        total_boot = sum(int((item.get("metrics") or {}).get("boot_count", 0) or 0) for item in self.results)
        total_serial_err = sum(int((item.get("metrics") or {}).get("serial_error_count", 0) or 0) for item in self.results)
        total_unexpected = sum(int((item.get("metrics") or {}).get("unexpected_recognition_count", 0) or 0) for item in self.results)
        recent = [
            {
                "round": item["round"],
                "category": item["category"],
                "phrase": item["phrase"],
                "result": item["result"],
                "reason": item["reason"],
                "unexpected_asr_texts": (item.get("metrics") or {}).get("unexpected_asr_texts", []),
                "online_request_ids": (item.get("metrics") or {}).get("online_request_ids", []),
                "latency_samples": (item.get("metrics") or {}).get("latency_samples", [])[:3],
            }
            for item in self.results[-10:]
        ]
        return {
            "project_id": self.args.project_id,
            "status": status,
            "pid": os.getpid(),
            "run_dir": str(self.run_dir),
            "updated_at": now_iso(),
            "planned_end_at": end_at.isoformat(timespec="seconds"),
            "round_count": len(self.results),
            "elapsed_s": elapsed_s,
            "result_counts": self.counts,
            "category_counts": self.category_counts,
            "anomaly_counts": self.anomaly_counts,
            "total_boot_or_crash_markers": total_boot,
            "total_serial_error_markers": total_serial_err,
            "total_unexpected_recognition_count": total_unexpected,
            "serial_states": [asdict(reader.state) for reader in self.readers],
            "preflight": {
                "audio_ready": preflight.get("audio_ready"),
                "serial_ready": preflight.get("serial_ready"),
                "control_ready": preflight.get("control_ready"),
            },
            "snapshots": self.snapshot_refs,
            "snapshot_events": self.snapshot_events[-20:],
            "recent_rounds": recent,
        }

    def _write_heartbeat(self, status: str, end_at: datetime, preflight: Dict[str, Any]) -> None:
        payload = self._summary_payload(status, end_at, preflight)
        write_json(self.run_dir / "heartbeat.json", payload)
        write_json(self.run_dir / "summary_live.json", payload)

    def _write_final_summary(self, started_at: datetime, end_at: datetime) -> None:
        self._append_full_log_mirror()
        total_unexpected = sum(int((item.get("metrics") or {}).get("unexpected_recognition_count", 0) or 0) for item in self.results)
        online_request_rounds = [
            {
                "round": item.get("round"),
                "category": item.get("category"),
                "phrase": item.get("phrase"),
                "online_request_ids": (item.get("metrics") or {}).get("online_request_ids", []),
                "latency_samples": (item.get("metrics") or {}).get("latency_samples", [])[:3],
            }
            for item in self.results
            if (item.get("metrics") or {}).get("online_request_ids")
        ]
        unexpected_rounds = [
            {
                "round": item.get("round"),
                "category": item.get("category"),
                "phrase": item.get("phrase"),
                "unexpected_asr_texts": (item.get("metrics") or {}).get("unexpected_asr_texts", []),
            }
            for item in self.results
            if int((item.get("metrics") or {}).get("unexpected_recognition_count", 0) or 0) > 0
        ]
        payload = {
            "project_id": self.args.project_id,
            "status": "FINISHED",
            "pid": os.getpid(),
            "run_dir": str(self.run_dir),
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "planned_end_at": end_at.isoformat(timespec="seconds"),
            "round_count": len(self.results),
            "result_counts": self.counts,
            "category_counts": self.category_counts,
            "anomaly_counts": self.anomaly_counts,
            "total_unexpected_recognition_count": total_unexpected,
            "online_request_round_count": len(online_request_rounds),
            "online_request_rounds": online_request_rounds[:200],
            "unexpected_recognition_rounds": unexpected_rounds[:200],
            "serial_states": [asdict(reader.state) for reader in self.readers],
            "log_files": [str(reader.log_path) for reader in self.readers],
            "mirror_log_files": [str(self.logs_dir / f"{reader.port}_{reader.name}.full.mirror.log") for reader in self.readers],
            "round_csv": str(self.round_csv_path),
        }
        after_snapshot = self._capture_snapshot("after", file_name="snapshot_after.json", extra_state={"final_summary": payload})
        final_snapshot = self._capture_snapshot("final", file_name="snapshot_final.json", extra_state={"final_summary": payload})
        if self.before_snapshot:
            diff = diff_snapshots(self.before_snapshot, after_snapshot)
            diff_path = write_snapshot_diff(self.run_dir, diff)
            coverage_path = write_snapshot_coverage(self.run_dir, diff.get("coverage", {}))
            self.snapshot_refs = {
                "before": rel(self.run_dir / "snapshot_before.json"),
                "after": rel(self.run_dir / "snapshot_after.json"),
                "final": rel(self.run_dir / "snapshot_final.json"),
                "diff": rel(diff_path),
                "coverage": rel(coverage_path),
                "coverage_summary": diff.get("coverage", {}),
                "diff_summary": diff.get("summary", {}),
            }
        else:
            self.snapshot_refs = {"after": rel(self.run_dir / "snapshot_after.json"), "final": rel(self.run_dir / "snapshot_final.json")}
        payload["snapshots"] = self.snapshot_refs
        payload["snapshot_events"] = self.snapshot_events
        write_json(self.run_dir / "summary_final.json", payload)
        lines = [
            f"# {self.args.project_id} 在线随机交互压测报告",
            "",
            f"- Run dir: `{self.run_dir}`",
            f"- Started: `{payload['started_at']}`",
            f"- Finished: `{payload['finished_at']}`",
            f"- Planned end: `{payload['planned_end_at']}`",
            f"- Rounds: `{payload['round_count']}`",
            f"- Result counts: `{json.dumps(self.counts, ensure_ascii=False)}`",
            f"- Category counts: `{json.dumps(self.category_counts, ensure_ascii=False)}`",
            f"- Anomaly counts: `{json.dumps(self.anomaly_counts, ensure_ascii=False)}`",
            f"- Unexpected recognition count: `{total_unexpected}`",
            f"- Snapshot diff: `{self.snapshot_refs.get('diff', '')}`",
            f"- Snapshot coverage: `{self.snapshot_refs.get('coverage', '')}`",
            "",
            "## Full Logs",
            "",
        ]
        for reader in self.readers:
            lines.append(f"- `{reader.log_path}`")
        lines.extend(["", "## Round CSV", "", f"- `{self.round_csv_path}`"])
        (self.run_dir / "report_final.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def nested(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def read_strategy(path: Path, name: str) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    strategies = payload.get("strategies") if isinstance(payload.get("strategies"), dict) else {}
    strategy = strategies.get(name, {})
    return strategy if isinstance(strategy, dict) else {}


def apply_env_and_strategy_defaults(args: argparse.Namespace) -> argparse.Namespace:
    env_path = resolve_env_path(args.env_file, ROOT)
    env_payload = load_env_payload(env_path)
    ports = nested(env_payload, "serial", "ports")
    if not isinstance(ports, dict):
        ports = {}
    audio = env_payload.get("audio") if isinstance(env_payload.get("audio"), dict) else {}
    device = env_payload.get("device") if isinstance(env_payload.get("device"), dict) else {}
    serial_cfg = env_payload.get("serial") if isinstance(env_payload.get("serial"), dict) else {}
    project_type = str(env_payload.get("project_type", "") or "").strip().lower()
    topology = str(serial_cfg.get("topology", "") or "").strip().lower()

    args.project_id = first_non_empty(args.project, env_payload.get("project_id"), nested(env_payload, "_config_source", "active_project"), "cskwb01")
    args.ap_port = first_non_empty(args.ap_port, ports.get("ap"), "COM14")
    no_cp_project = project_type == "ws63" or "no_cp" in topology or args.project_id.lower() in {"venusws63", "ws63"}
    default_cp = "" if no_cp_project else "COM13"
    args.cp_port = first_non_empty(args.cp_port, ports.get("cp"), default_cp)
    args.asr_port = first_non_empty(args.asr_port, ports.get("asr"), ports.get("upper"), "COM12")
    args.control_port = first_non_empty(args.control_port, ports.get("control"), "COM15")
    args.data_baud = int(args.data_baud or serial_cfg.get("baudrate") or 115200)
    args.control_baud = int(args.control_baud or serial_cfg.get("control_baudrate") or serial_cfg.get("baudrate") or 115200)
    args.device_key = first_non_empty(args.device_key, audio.get("default_playback_device_key"), env_payload.get("default_playback_device_key"), "")
    args.wake_text = first_non_empty(args.wake_text, device.get("wake_word"), env_payload.get("current_wakeup_word"), "小美小美")
    args.env_file_resolved = str(env_path)

    strategy_path = Path(args.strategy_file) if args.strategy_file else DEFAULT_STRATEGY
    if not strategy_path.is_absolute():
        strategy_path = (ROOT / strategy_path).resolve()
    strategy = read_strategy(strategy_path, args.strategy_name)
    args.strategy_payload = strategy
    args.strategy_file = str(strategy_path)
    gap = strategy.get("random_gap_s", []) if isinstance(strategy.get("random_gap_s"), list) else []
    observe = strategy.get("observe_s", []) if isinstance(strategy.get("observe_s"), list) else []
    args.min_gap_s = float(args.min_gap_s if args.min_gap_s is not None else (gap[0] if len(gap) >= 1 else 6.0))
    args.max_gap_s = float(args.max_gap_s if args.max_gap_s is not None else (gap[1] if len(gap) >= 2 else 28.0))
    args.min_observe_s = float(args.min_observe_s if args.min_observe_s is not None else (observe[0] if len(observe) >= 1 else 18.0))
    args.max_observe_s = float(args.max_observe_s if args.max_observe_s is not None else (observe[1] if len(observe) >= 2 else 42.0))
    return args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Polaris randomized online mixed interaction stress")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--end-at", default="", help="default: next 08:30 local time")
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--env-file", default="", help="默认读取根目录 polaris.local.json")
    parser.add_argument("--project", default="", help="覆盖 project_id；默认使用 env active_project")
    parser.add_argument("--strategy-file", default=str(DEFAULT_STRATEGY))
    parser.add_argument("--strategy-name", default="online_mixed_stress")
    parser.add_argument("--ap-port", default="")
    parser.add_argument("--cp-port", default="")
    parser.add_argument("--asr-port", default="")
    parser.add_argument("--data-baud", type=int, default=0)
    parser.add_argument("--control-port", default="")
    parser.add_argument("--control-baud", type=int, default=0)
    parser.add_argument("--device-key", default="")
    parser.add_argument("--wake-text", default="")
    parser.add_argument("--min-gap-s", type=float, default=None)
    parser.add_argument("--max-gap-s", type=float, default=None)
    parser.add_argument("--min-observe-s", type=float, default=None)
    parser.add_argument("--max-observe-s", type=float, default=None)
    parser.add_argument("--summary-every", type=int, default=5)
    parser.add_argument("--sample-window-every", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260519)
    return parser


def main() -> int:
    args = apply_env_and_strategy_defaults(build_parser().parse_args())
    return StressRunner(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
