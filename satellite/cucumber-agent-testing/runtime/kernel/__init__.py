"""Validation kernel primitives.

The kernel owns deterministic runtime concerns only. Product-specific parsing,
state decoration and future assertions should enter through plugins instead of
being added directly to replay.py or assertion_engine.py.
"""

from .plugin import PluginContext, PluginManager, RuntimePlugin, tag_event

__all__ = ["PluginContext", "PluginManager", "RuntimePlugin", "tag_event"]
