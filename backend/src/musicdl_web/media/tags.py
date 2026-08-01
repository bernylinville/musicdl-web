"""Original-encoding metadata writes for Navidrome-supported audio formats."""

from __future__ import annotations

import base64
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

from musicdl_web.artwork import detect_image_type
from musicdl_web.domain import JobRequest


class TaggingError(RuntimeError):
    """A deliberately path- and metadata-free tagging failure."""


AudioLoader = Callable[[Path], Any]


class MutagenTagWriter:
    """Write tags and cover art in place; Mutagen never transcodes media."""

    def __init__(self, loader: AudioLoader | None = None) -> None:
        self._loader = loader or _load_audio

    def write(
        self,
        audio: Path,
        request: JobRequest,
        *,
        cover: bytes | None,
        lyrics: str | None,
    ) -> None:
        extension = audio.suffix.lower()
        if extension not in {".mp3", ".flac", ".m4a", ".ogg"}:
            raise TaggingError("media format does not support metadata writing")
        cover_mime = detect_image_type(cover) if cover is not None else None
        if cover is not None and cover_mime is None:
            raise TaggingError("cover image is not a valid JPEG or PNG")
        try:
            media = self._loader(audio)
            if media is None:
                raise TaggingError("media tagging failed")
            if extension == ".mp3":
                _write_mp3(media, request, cover, cover_mime, lyrics)
            elif extension == ".flac":
                _write_flac(media, request, cover, cover_mime, lyrics)
            elif extension == ".m4a":
                _write_mp4(media, request, cover, cover_mime, lyrics)
            else:
                _write_ogg(media, request, cover, cover_mime, lyrics)
            media.save()
        except TaggingError:
            raise
        except Exception:
            raise TaggingError("media tagging failed") from None


def _load_audio(path: Path) -> Any:
    mutagen = import_module("mutagen")
    return mutagen.File(path, easy=False)


def _write_mp3(
    media: Any,
    request: JobRequest,
    cover: bytes | None,
    cover_mime: str | None,
    lyrics: str | None,
) -> None:
    id3 = import_module("mutagen.id3")
    if media.tags is None:
        media.add_tags()
    tags = media.tags
    frames: tuple[tuple[str, str, list[str]], ...] = (
        ("TIT2", "TIT2", [request.track.title]),
        ("TPE1", "TPE1", list(request.track.artists)),
    )
    optional = (
        ("TALB", "TALB", request.track.album),
        ("TPE2", "TPE2", request.track.album_artist),
        ("TRCK", "TRCK", _number(request.track.track_number)),
        ("TPOS", "TPOS", _number(request.track.disc)),
        ("TDRC", "TDRC", _number(request.track.year)),
    )
    for frame_id, class_name, text in frames:
        tags.delall(frame_id)
        tags.add(getattr(id3, class_name)(encoding=3, text=text))
    for frame_id, class_name, optional_text in optional:
        tags.delall(frame_id)
        if optional_text is not None:
            tags.add(getattr(id3, class_name)(encoding=3, text=[optional_text]))
    tags.delall("APIC")
    if cover is not None and cover_mime is not None:
        tags.add(
            id3.APIC(
                encoding=3,
                mime=cover_mime,
                type=3,
                desc="Cover",
                data=cover,
            )
        )
    tags.delall("USLT")
    if lyrics:
        tags.add(id3.USLT(encoding=3, lang="eng", desc="", text=lyrics))


def _write_flac(
    media: Any,
    request: JobRequest,
    cover: bytes | None,
    cover_mime: str | None,
    lyrics: str | None,
) -> None:
    _write_vorbis_comments(media, request, lyrics)
    media.clear_pictures()
    if cover is not None and cover_mime is not None:
        picture_type = import_module("mutagen.flac").Picture
        picture = picture_type()
        picture.type = 3
        picture.mime = cover_mime
        picture.desc = "Cover"
        picture.data = cover
        media.add_picture(picture)


def _write_ogg(
    media: Any,
    request: JobRequest,
    cover: bytes | None,
    cover_mime: str | None,
    lyrics: str | None,
) -> None:
    _write_vorbis_comments(media, request, lyrics)
    media.pop("metadata_block_picture", None)
    if cover is not None and cover_mime is not None:
        picture_type = import_module("mutagen.flac").Picture
        picture = picture_type()
        picture.type = 3
        picture.mime = cover_mime
        picture.desc = "Cover"
        picture.data = cover
        encoded = base64.b64encode(picture.write()).decode("ascii")
        media["metadata_block_picture"] = [encoded]


def _write_vorbis_comments(media: Any, request: JobRequest, lyrics: str | None) -> None:
    if media.tags is None:
        media.add_tags()
    values: dict[str, list[str] | None] = {
        "TITLE": [request.track.title],
        "ARTIST": list(request.track.artists),
        "ALBUM": _optional_list(request.track.album),
        "ALBUMARTIST": _optional_list(request.track.album_artist),
        "TRACKNUMBER": _optional_list(_number(request.track.track_number)),
        "DISCNUMBER": _optional_list(_number(request.track.disc)),
        "DATE": _optional_list(_number(request.track.year)),
        "LYRICS": _optional_list(lyrics),
    }
    for key, value in values.items():
        if value is None:
            media.pop(key, None)
        else:
            media[key] = value


def _write_mp4(
    media: Any,
    request: JobRequest,
    cover: bytes | None,
    cover_mime: str | None,
    lyrics: str | None,
) -> None:
    if media.tags is None:
        media.add_tags()
    tags = media.tags
    values: dict[str, Any | None] = {
        "\xa9nam": [request.track.title],
        "\xa9ART": list(request.track.artists),
        "\xa9alb": _optional_list(request.track.album),
        "aART": _optional_list(request.track.album_artist),
        "trkn": [(request.track.track_number, 0)] if request.track.track_number else None,
        "disk": [(request.track.disc, 0)] if request.track.disc else None,
        "\xa9day": _optional_list(_number(request.track.year)),
        "\xa9lyr": _optional_list(lyrics),
    }
    for key, value in values.items():
        if value is None:
            tags.pop(key, None)
        else:
            tags[key] = value
    tags.pop("covr", None)
    if cover is not None and cover_mime is not None:
        mp4 = import_module("mutagen.mp4")
        image_format = (
            mp4.MP4Cover.FORMAT_PNG
            if cover_mime == "image/png"
            else mp4.MP4Cover.FORMAT_JPEG
        )
        tags["covr"] = [mp4.MP4Cover(cover, imageformat=image_format)]


def _optional_list(value: str | None) -> list[str] | None:
    return [value] if value else None


def _number(value: int | None) -> str | None:
    return str(value) if value is not None else None
