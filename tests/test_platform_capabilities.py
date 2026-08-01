from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest
from musicdl_web.adapters import NeteaseCapabilityParser, QQCapabilityParser
from musicdl_web.models import Source
from musicdl_web.platforms.errors import (
    CapabilityUnavailable,
    ExactQualityMismatch,
    PreviewUnavailable,
    QualitySnapshotExpired,
    QualitySnapshotMismatch,
)
from musicdl_web.platforms.preview import PreviewRegistry
from musicdl_web.platforms.quality import (
    DownloadGrant,
    FidelityFamily,
    QualityOption,
    QualitySnapshotStore,
)


def test_netease_snapshot_is_bound_and_exact_response_cannot_downgrade(fixture_json) -> None:
    fixture = fixture_json("platforms/netease_capabilities.json")
    now = datetime.now(UTC)
    snapshots = QualitySnapshotStore(ttl=timedelta(minutes=5))
    parser = NeteaseCapabilityParser(
        snapshots,
        capability_enabled=True,
        approved_media_hosts=frozenset({"m10.music.126.net"}),
    )
    snapshot = parser.parse_quality_snapshot(
        fixture["quality"], track_id="101", session_version=4, now=now
    )

    assert [option.quality_id for option in snapshot.options] == ["standard", "lossless"]
    binding = snapshots.bind(
        snapshot_id=snapshot.snapshot_id,
        quality_id="lossless",
        source=Source.NETEASE,
        track_id="101",
        session_version=4,
        now=now,
    )
    grant = parser.parse_exact_response(fixture["exact"], binding, now=now)
    assert grant.quality_id == "lossless"
    assert "https" not in repr(grant)

    downgraded = copy.deepcopy(fixture["exact"])
    downgraded["data"][0]["level"] = "standard"
    with pytest.raises(ExactQualityMismatch, match="different quality"):
        parser.parse_exact_response(downgraded, binding, now=now)


def test_qq_snapshot_and_exact_filename_tier_must_match(fixture_json) -> None:
    fixture = fixture_json("platforms/qq_capabilities.json")
    snapshots = QualitySnapshotStore()
    parser = QQCapabilityParser(
        snapshots,
        capability_enabled=True,
        approved_media_hosts=frozenset({"isure.stream.qqmusic.qq.com"}),
    )
    snapshot = parser.parse_quality_snapshot(
        fixture["quality"], track_id="fixture-track-mid", session_version=2
    )
    assert [option.quality_id for option in snapshot.options] == ["M500", "M800", "F000"]
    binding = snapshots.bind(
        snapshot_id=snapshot.snapshot_id,
        quality_id="F000",
        source=Source.QQ,
        track_id="fixture-track-mid",
        session_version=2,
    )
    grant = parser.parse_exact_response(fixture["exact"], binding)
    assert grant.expected_bytes == 23456789
    assert grant.allowed_hosts == frozenset({"isure.stream.qqmusic.qq.com"})

    mismatched = copy.deepcopy(fixture["exact"])
    mismatched["data"]["midurlinfo"][0]["filename"] = "M800fixture-track-mid.mp3"
    with pytest.raises(ExactQualityMismatch):
        parser.parse_exact_response(mismatched, binding)


def test_snapshot_expiry_session_change_and_cross_track_are_hard_failures() -> None:
    now = datetime.now(UTC)
    snapshots = QualitySnapshotStore(ttl=timedelta(seconds=10))
    snapshot = snapshots.create(
        source=Source.NETEASE,
        track_id="101",
        session_version=1,
        options=(
            QualityOption(
                quality_id="standard",
                label="标准",
                family=FidelityFamily.LINEAR,
                rank=10,
            ),
        ),
        now=now,
    )
    arguments = {
        "snapshot_id": snapshot.snapshot_id,
        "quality_id": "standard",
        "source": Source.NETEASE,
        "track_id": "101",
        "session_version": 1,
    }

    with pytest.raises(QualitySnapshotMismatch):
        snapshots.bind(**(arguments | {"session_version": 2}), now=now)
    with pytest.raises(QualitySnapshotMismatch):
        snapshots.bind(**(arguments | {"track_id": "other"}), now=now)
    with pytest.raises(QualitySnapshotExpired):
        snapshots.bind(**arguments, now=now + timedelta(seconds=10))


@pytest.mark.parametrize(
    ("parser_factory", "fixture_name", "track_id"),
    [
        (NeteaseCapabilityParser, "platforms/netease_capabilities.json", "101"),
        (QQCapabilityParser, "platforms/qq_capabilities.json", "fixture-track-mid"),
    ],
)
def test_live_quality_capability_is_unavailable_until_explicitly_enabled(
    parser_factory, fixture_name: str, track_id: str, fixture_json
) -> None:
    fixture = fixture_json(fixture_name)
    parser = parser_factory(QualitySnapshotStore())

    with pytest.raises(CapabilityUnavailable, match="unavailable"):
        parser.parse_quality_snapshot(
            fixture["quality"], track_id=track_id, session_version=1
        )


@pytest.mark.parametrize("parser_factory", [NeteaseCapabilityParser, QQCapabilityParser])
def test_capability_parser_rejects_third_party_media_allowlist(parser_factory) -> None:
    with pytest.raises(ValueError, match="media host is invalid"):
        parser_factory(
            QualitySnapshotStore(),
            capability_enabled=True,
            approved_media_hosts=frozenset({"evil.example"}),
        )


def test_download_grant_is_memory_only_and_exact_binding_is_reconstructable() -> None:
    snapshots = QualitySnapshotStore()
    snapshot = snapshots.create(
        source=Source.QQ,
        track_id="track",
        session_version=8,
        options=(
            QualityOption(
                quality_id="F000",
                label="无损",
                family=FidelityFamily.LINEAR,
                rank=40,
            ),
        ),
    )

    selected = snapshots.bind(
        snapshot_id=snapshot.snapshot_id,
        quality_id="F000",
        source=Source.QQ,
        track_id="track",
        session_version=8,
    )
    grant = DownloadGrant(
        source=selected.source,
        track_id=selected.track_id,
        quality_id=selected.option.quality_id,
        quality_rank=selected.option.rank,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        allowed_hosts=frozenset({"isure.stream.qqmusic.qq.com"}),
        _source_url="https://isure.stream.qqmusic.qq.com/redacted/audio.flac",
    )

    assert selected.option.rank == 40
    assert grant.quality_id == selected.option.quality_id
    assert "https" not in repr(grant)


def test_preview_lease_is_opaque_one_shot_and_unavailable_is_explicit() -> None:
    registry = PreviewRegistry()
    with pytest.raises(PreviewUnavailable):
        registry.register(
            source=Source.NETEASE,
            track_id="101",
            duration_ms=None,
            source_url=None,
            allowed_hosts=frozenset({"music.126.net"}),
        )

    lease = registry.register(
        source=Source.NETEASE,
        track_id="101",
        duration_ms=30_000,
        source_url="https://music.126.net/redacted/preview.mp3",
        allowed_hosts=frozenset({"music.126.net"}),
    )
    assert "url" not in lease.model_dump(mode="json")
    target = registry.claim(lease.preview_id, source=Source.NETEASE, track_id="101")
    assert "https" not in repr(target)
    with pytest.raises(PreviewUnavailable):
        registry.claim(lease.preview_id, source=Source.NETEASE, track_id="101")
