"""Sanitized errors exposed by the platform boundary."""

from __future__ import annotations

from .models import Source


class SearchError(Exception):
    """Base error for a failed platform search."""


class NetworkPolicyError(SearchError):
    """A request or redirect violated the platform network policy."""


class NetworkRequestError(SearchError):
    """A platform request failed without exposing its URL or credentials."""


class PlatformResponseError(SearchError):
    """A platform returned a response that cannot be mapped safely."""

    def __init__(self, source: Source, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"{source.value} search response is invalid: {reason}")
