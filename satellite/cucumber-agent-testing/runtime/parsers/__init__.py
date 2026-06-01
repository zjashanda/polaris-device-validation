"""Parsers that convert raw artifacts into runtime events."""

from pathlib import Path

from .json_artifact_parser import parse_json_artifacts
from .serial_log_parser import parse_log_file, parse_log_tree


def parse_artifact_tree(root: Path):
    return [*parse_log_tree(root), *parse_json_artifacts(root)]


__all__ = ["parse_artifact_tree", "parse_json_artifacts", "parse_log_file", "parse_log_tree"]
