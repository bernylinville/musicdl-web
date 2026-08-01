from __future__ import annotations

from pathlib import Path

from musicdl_web.db import Repository
from musicdl_web.domain import (
    Delivery,
    JobRequest,
    MediaMetadata,
    PlatformTrack,
    Quality,
)
from musicdl_web.media import MediaPublisher, sanitize_component
from musicdl_web.models import Source


class FakeProbe:
    def probe(self, path: Path) -> MediaMetadata:
        assert path.read_bytes().startswith(b"fLaC")
        return MediaMetadata(
            extension="flac", codec="flac", duration_seconds=120, quality=Quality.LOSSLESS
        )


class NoopTagger:
    def write(
        self,
        audio: Path,
        request: JobRequest,
        *,
        cover: bytes | None,
        lyrics: str | None,
    ) -> None:
        assert request.track.title
        assert audio.exists()


def request(track_id: str, title: str = "A/B: Song") -> JobRequest:
    return JobRequest(
        track=PlatformTrack(
            source=Source.NETEASE,
            track_id=track_id,
            title=title,
            artists=("Artist",),
            album="Album",
            album_artist="Album Artist",
            year=2026,
            disc=1,
            track_number=2,
        ),
        quality=Quality.LOSSLESS,
        quality_id="lossless",
        quality_snapshot_id="snapshot-1234567890123456",
        delivery=Delivery.SERVER,
    )


def publisher(tmp_path: Path, repository: Repository) -> MediaPublisher:
    return MediaPublisher(
        tmp_path / "music", tmp_path / "stage", repository, FakeProbe(), NoopTagger()
    )


def test_master_selection_accepts_probed_lossless_or_hi_res(tmp_path: Path) -> None:
    """Netease 超清母带 is FLAC; ffprobe cannot label it MASTER."""

    class MasterProbe:
        def probe(self, path: Path) -> MediaMetadata:
            return MediaMetadata(
                extension="flac",
                codec="flac",
                duration_seconds=200,
                quality=Quality.HI_RES,
            )

    repository = Repository(tmp_path / "app.db")
    media = MediaPublisher(
        tmp_path / "music",
        tmp_path / "stage",
        repository,
        MasterProbe(),
        NoopTagger(),
    )
    source = tmp_path / "source.flac"
    source.write_bytes(b"fLaC" + b"x" * 32)
    job = JobRequest(
        track=PlatformTrack(
            source=Source.NETEASE,
            track_id="167827",
            title="素颜",
            artists=("许嵩", "何曼婷"),
            album="自定义",
        ),
        quality=Quality.MASTER,
        quality_id="jymaster",
        quality_label="超清母带",
        quality_snapshot_id="snapshot-master-1234567890",
        delivery=Delivery.SERVER,
    )
    output = media.publish(source, job)
    assert output.suffix == ".flac"
    assert output.is_file()


def test_path_normalization_sidecars_and_managed_index(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    source = tmp_path / "source.flac"
    source.write_bytes(b"fLaC" + b"x" * 32)

    output = publisher(tmp_path, repository).publish(
        source, request("42"), cover=b"jpeg", lyrics="[00:01]hello"
    )

    assert output.relative_to(tmp_path / "music") == Path(
        "Album Artist/Album (2026)/1-02 - A_B_ Song.flac"
    )
    assert output.read_bytes() == source.read_bytes()
    assert output.with_suffix(".lrc").read_text() == "[00:01]hello"
    assert (output.parent / "cover.jpg").read_bytes() == b"jpeg"
    decision = repository.managed_decision(Source.NETEASE, "42", Quality.LOSSLESS)
    assert not decision.allowed
    assert decision.reason == "same_or_lower"


def test_nonmanaged_collision_gets_platform_suffix_and_is_never_deleted(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    media = publisher(tmp_path, repository)
    target = tmp_path / "music" / media.relative_path(request("99"), "flac")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"personal")
    source = tmp_path / "source.flac"
    source.write_bytes(b"fLaC" + b"x" * 32)

    output = media.publish(source, request("99"))

    assert target.read_bytes() == b"personal"
    assert output.name.endswith("[netease-99].flac")
    assert sanitize_component("../bad\\name") == "_bad_name"


def test_nonmanaged_lyrics_collision_suffixes_audio_and_lyrics_together(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    media = publisher(tmp_path, repository)
    target = tmp_path / "music" / media.relative_path(request("lyrics"), "flac")
    target.parent.mkdir(parents=True)
    unmanaged_lyrics = target.with_suffix(".lrc")
    unmanaged_lyrics.write_text("personal lyrics", encoding="utf-8")
    source = tmp_path / "source.flac"
    source.write_bytes(b"fLaC" + b"x" * 32)

    output = media.publish(source, request("lyrics"), lyrics="managed lyrics")

    assert output.name.endswith("[netease-lyrics].flac")
    assert output.with_suffix(".lrc").read_text(encoding="utf-8") == "managed lyrics"
    assert unmanaged_lyrics.read_text(encoding="utf-8") == "personal lyrics"


def test_missing_managed_file_allows_redownload(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    missing = tmp_path / "music/missing.flac"
    repository.register_media(Source.QQ, "missing", Quality.HIGH, missing)

    decision = repository.managed_decision(Source.QQ, "missing", Quality.HIGH)

    assert decision.allowed
    assert decision.reason == "missing"


def test_quality_upgrade_removes_previous_managed_lyrics(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    old_audio = tmp_path / "music/Artist/Singles/Old.flac"
    old_audio.parent.mkdir(parents=True)
    old_audio.write_bytes(b"fLaC" + b"o" * 32)
    old_lyrics = old_audio.with_suffix(".lrc")
    old_lyrics.write_text("old managed lyrics", encoding="utf-8")
    unrelated = old_audio.with_suffix(".txt")
    unrelated.write_text("personal note", encoding="utf-8")
    cover = old_audio.parent / "cover.jpg"
    cover.write_bytes(b"personal cover")
    repository.register_media(Source.NETEASE, "upgrade", Quality.HIGH, old_audio)
    source = tmp_path / "source.flac"
    source.write_bytes(b"fLaC" + b"n" * 32)

    output = publisher(tmp_path, repository).publish(
        source, request("upgrade", title="New"), lyrics="new lyrics", cover=b"new cover"
    )

    assert output.exists()
    assert output.with_suffix(".lrc").read_text(encoding="utf-8") == "new lyrics"
    assert not old_audio.exists()
    assert not old_lyrics.exists()
    assert unrelated.read_text(encoding="utf-8") == "personal note"
    assert cover.read_bytes() == b"personal cover"
