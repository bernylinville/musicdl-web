"""Core package for the musicdl-web service."""

from .adapters import NeteaseAdapter, QQAdapter
from .models import SearchResult, SearchResults, Source, Track

__all__ = [
    "NeteaseAdapter",
    "QQAdapter",
    "SearchResult",
    "SearchResults",
    "Source",
    "Track",
]
