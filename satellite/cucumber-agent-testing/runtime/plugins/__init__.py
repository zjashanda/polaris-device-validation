"""Default runtime plugins used by Polaris replay."""

from .asr import ASRPlugin
from .media import MediaPlugin
from .network import NetworkPlugin
from .reboot import RebootPlugin
from .wake import WakePlugin


def default_plugins():
    return [WakePlugin(), ASRPlugin(), MediaPlugin(), NetworkPlugin(), RebootPlugin()]


__all__ = ["ASRPlugin", "MediaPlugin", "NetworkPlugin", "RebootPlugin", "WakePlugin", "default_plugins"]
