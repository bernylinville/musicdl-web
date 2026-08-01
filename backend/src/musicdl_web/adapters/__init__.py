"""Platform search adapters."""

from .netease import NeteaseAdapter, NeteaseCapabilityParser
from .qq import QQAdapter, QQCapabilityParser

__all__ = ["NeteaseAdapter", "NeteaseCapabilityParser", "QQAdapter", "QQCapabilityParser"]
