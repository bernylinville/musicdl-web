from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from musicdl_web.domain import Delivery, JobRequest, PlatformTrack, Quality
from musicdl_web.media import FFprobeMediaProbe, MediaProbeError, MutagenTagWriter, TaggingError
from musicdl_web.models import Source


def request() -> JobRequest:
    return JobRequest(
        track=PlatformTrack(
            source=Source.NETEASE,
            track_id="42",
            title="Song",
            artists=("First", "Second"),
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


def probe_runner(payload: object, returncode: int = 0):
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command[:3] == ["ffprobe", "-v", "error"]
        assert command[-2:] == ["--", "/private/download.flac"]
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "timeout": 20,
            "check": False,
        }
        return subprocess.CompletedProcess(command, returncode, json.dumps(payload), "ignored")

    return run


def test_ffprobe_validates_and_maps_lossless_media() -> None:
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "audio",
                "codec_name": "flac",
                "sample_rate": "44100",
                "bits_per_raw_sample": "16",
                "bit_rate": "900000",
            }
        ],
        "format": {"format_name": "flac", "duration": "183.25", "bit_rate": "900000"},
    }
    probe = FFprobeMediaProbe(runner=probe_runner(payload))

    details = probe.inspect(Path("/private/download.flac"))
    metadata = probe.probe(Path("/private/download.flac"))

    assert details.container == "flac"
    assert details.codec == "flac"
    assert details.duration_seconds == 183.25
    assert details.sample_rate_hz == 44_100
    assert details.bit_depth == 16
    assert details.bitrate_bps == 900_000
    assert details.quality is Quality.LOSSLESS
    assert metadata.quality is Quality.LOSSLESS


def test_ffprobe_maps_hires_and_high_bitrate_lossy_media() -> None:
    hires = {
        "streams": [{
            "codec_type": "audio", "codec_name": "flac", "sample_rate": "96000",
            "bits_per_sample": "24", "bit_rate": "2500000",
        }],
        "format": {"format_name": "flac", "duration": "10"},
    }
    high = {
        "streams": [{
            "codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100",
            "bits_per_sample": "0", "bit_rate": "320000", "duration": "10",
        }],
        "format": {"format_name": "mp3", "duration": "10"},
    }

    assert FFprobeMediaProbe(runner=probe_runner(hires)).inspect(
        Path("/private/download.flac")
    ).quality is Quality.HI_RES
    assert FFprobeMediaProbe(runner=probe_runner(high)).inspect(
        Path("/private/download.flac")
    ).quality is Quality.HIGH


@pytest.mark.parametrize(
    "payload",
    [
        {"streams": [], "format": {"format_name": "flac"}},
        {
            "streams": [
                {"codec_type": "audio", "codec_name": "flac"},
                {"codec_type": "audio", "codec_name": "flac"},
            ],
            "format": {"format_name": "flac"},
        },
    ],
)
def test_ffprobe_rejects_missing_or_multiple_audio_streams_without_path(
    payload: object,
) -> None:
    probe = FFprobeMediaProbe(runner=probe_runner(payload))

    with pytest.raises(MediaProbeError) as caught:
        probe.inspect(Path("/private/download.flac"))

    assert str(caught.value) == "media must contain exactly one audio stream"
    assert "/private" not in str(caught.value)


@pytest.mark.parametrize(
    ("stream", "media_format", "message"),
    [
        (
            {
                "codec_type": "audio",
                "codec_name": "flac",
                "sample_rate": "44100",
                "bit_rate": "900000",
            },
            {"format_name": "flac", "duration": "10"},
            "media technical properties are invalid",
        ),
        (
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "bit_rate": "256000",
            },
            {"format_name": "matroska", "duration": "10"},
            "media container or codec is unsupported",
        ),
    ],
)
def test_ffprobe_rejects_invalid_technical_properties_and_container(
    stream: object, media_format: object, message: str
) -> None:
    probe = FFprobeMediaProbe(
        runner=probe_runner({"streams": [stream], "format": media_format})
    )

    with pytest.raises(MediaProbeError, match=message):
        probe.inspect(Path("/private/download.flac"))


def test_ffprobe_hides_process_stderr() -> None:
    probe = FFprobeMediaProbe(
        runner=probe_runner({"error": "secret platform response"}, returncode=1)
    )

    with pytest.raises(MediaProbeError) as caught:
        probe.inspect(Path("/private/download.flac"))

    assert str(caught.value) == "media probe could not inspect the downloaded file"
    assert "secret" not in str(caught.value)


class FakeFrame:
    def __init__(self, **values: Any) -> None:
        self.values = values


class FakeID3(dict[str, Any]):
    def __init__(self) -> None:
        super().__init__()
        self.frames: list[FakeFrame] = []

    def delall(self, frame_id: str) -> None:
        self[frame_id] = []

    def add(self, frame: FakeFrame) -> None:
        self.frames.append(frame)


class FakeMedia(dict[str, Any]):
    def __init__(self, tags: Any | None = None) -> None:
        super().__init__()
        self.tags = tags
        self.saved = False
        self.pictures: list[Any] = []

    def add_tags(self) -> None:
        self.tags = FakeID3()

    def clear_pictures(self) -> None:
        self.pictures.clear()

    def add_picture(self, picture: Any) -> None:
        self.pictures.append(picture)

    def save(self) -> None:
        self.saved = True


class FakePicture:
    type: int
    mime: str
    desc: str
    data: bytes

    def write(self) -> bytes:
        return b"picture:" + self.data


def fake_import(name: str) -> Any:
    if name == "mutagen.id3":
        return SimpleNamespace(
            **{
                frame: type(frame, (FakeFrame,), {})
                for frame in (
                    "TIT2", "TPE1", "TALB", "TPE2", "TRCK", "TPOS", "TDRC", "APIC", "USLT"
                )
            }
        )
    if name == "mutagen.flac":
        return SimpleNamespace(Picture=FakePicture)
    if name == "mutagen.mp4":
        class MP4Cover(bytes):
            FORMAT_JPEG = 13

            def __new__(cls, data: bytes, *, imageformat: int) -> MP4Cover:
                assert imageformat == cls.FORMAT_JPEG
                return super().__new__(cls, data)

        cover = MP4Cover
        return SimpleNamespace(MP4Cover=cover)
    raise AssertionError(name)


@pytest.mark.parametrize("suffix", [".mp3", ".flac", ".m4a", ".ogg"])
def test_mutagen_writer_writes_supported_metadata_and_jpeg_cover(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    import musicdl_web.media.tags as tags_module

    monkeypatch.setattr(tags_module, "import_module", fake_import)
    media = FakeMedia(FakeID3() if suffix in {".mp3", ".m4a"} else None)
    writer = MutagenTagWriter(loader=lambda _: media)
    cover = b"\xff\xd8\xffjpeg\xff\xd9"

    writer.write(Path(f"track{suffix}"), request(), cover=cover, lyrics="lyrics")

    assert media.saved
    if suffix == ".mp3":
        assert len(media.tags.frames) == 9
        assert any(frame.values.get("data") == cover for frame in media.tags.frames)
    elif suffix == ".flac":
        assert media["TITLE"] == ["Song"]
        assert media["ARTIST"] == ["First", "Second"]
        assert media["LYRICS"] == ["lyrics"]
        assert media.pictures[0].data == cover
    elif suffix == ".m4a":
        assert media.tags["\xa9nam"] == ["Song"]
        assert media.tags["trkn"] == [(2, 0)]
        assert bytes(media.tags["covr"][0]) == cover
    else:
        assert media["TITLE"] == ["Song"]
        assert media["metadata_block_picture"]


def test_mutagen_writer_rejects_non_jpeg_without_disclosing_metadata() -> None:
    with pytest.raises(TaggingError) as caught:
        MutagenTagWriter(loader=lambda _: FakeMedia()).write(
            Path("/private/track.flac"), request(), cover=b"not-jpeg", lyrics="secret lyric"
        )

    assert str(caught.value) == "cover image is not a valid JPEG or PNG"
    assert "private" not in str(caught.value)
    assert "secret" not in str(caught.value)
