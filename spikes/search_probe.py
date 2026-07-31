#!/usr/bin/env python3
"""Manually probe platform search metadata without printing source payloads."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from musicdl_web.adapters import NeteaseAdapter, QQAdapter
from musicdl_web.errors import SearchError
from musicdl_web.models import SearchResults


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("both", "netease", "qq"), default="both")
    parser.add_argument("--query", required=True)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--limit", type=int, default=20)
    return parser.parse_args(argv)


def summarize(result: SearchResults, hosts: list[str]) -> dict[str, Any]:
    return {
        "source": result.source.value,
        "page": result.page,
        "has_more": result.has_more,
        "track_count": len(result.tracks),
        "tracks": [
            {
                "source": track.source.value,
                "track_id": track.track_id,
                "title": track.title,
                "artists": list(track.artists),
                "album": track.album,
                "duration_ms": track.duration_ms,
                "has_cover": track.cover_url is not None,
            }
            for track in result.tracks
        ],
        "accessed_hosts": sorted(set(hosts)),
    }


def run_probe(source: str, query: str, page: int, limit: int) -> dict[str, Any]:
    adapter = NeteaseAdapter() if source == "netease" else QQAdapter()
    try:
        result = adapter.search(query, page=page, limit=limit)
        return summarize(result, list(adapter.accessed_hosts))
    finally:
        adapter.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    sources = ("netease", "qq") if args.source == "both" else (args.source,)
    output: list[dict[str, Any]] = []
    exit_code = 0
    for source in sources:
        try:
            output.append(run_probe(source, args.query, args.page, args.limit))
        except (SearchError, ValueError) as exc:
            output.append({"source": source, "error": str(exc)})
            exit_code = 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
