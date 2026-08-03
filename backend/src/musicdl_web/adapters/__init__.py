"""Platform search adapters."""

from .netease import NeteaseAdapter, NeteaseCapabilityParser
from .netease_catalog import NeteaseCatalog
from .netease_liked import NeteaseLikedCatalog
from .qq import QQAdapter, QQCapabilityParser

__all__ = [
    "NeteaseAdapter",
    "NeteaseCapabilityParser",
    "NeteaseCatalog",
    "NeteaseLikedCatalog",
    "QQAdapter",
    "QQCapabilityParser",
]
