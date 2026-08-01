"""Navidrome-oriented path generation and atomic publication."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from musicdl_web.artwork import cover_sidecar_name, detect_image_type
from musicdl_web.db import Repository
from musicdl_web.domain import JobRequest, MediaMetadata
from musicdl_web.media.quality_match import probe_matches_request


class PublishError(RuntimeError):
    pass


class MediaProbe(Protocol):
    def probe(self, path: Path) -> MediaMetadata: ...


class TagWriter(Protocol):
    def write(
        self,
        audio: Path,
        request: JobRequest,
        *,
        cover: bytes | None,
        lyrics: str | None,
    ) -> None: ...


class MediaPublisher:
    def __init__(
        self,
        music_root: Path,
        staging_root: Path,
        repository: Repository,
        probe: MediaProbe,
        tag_writer: TagWriter,
    ) -> None:
        self.music_root = music_root.resolve()
        self.staging_root = staging_root.resolve()
        self.repository = repository
        self.probe = probe
        self.tag_writer = tag_writer

    def publish(
        self,
        downloaded: Path,
        request: JobRequest,
        *,
        cover: bytes | None = None,
        lyrics: str | None = None,
    ) -> Path:
        before = self.probe.probe(downloaded)
        if not probe_matches_request(request, before.quality):
            raise PublishError("downloaded media quality does not match the selected tier")
        relative = self.relative_path(request, before.extension)
        destination = (self.music_root / relative).resolve()
        if not destination.is_relative_to(self.music_root):
            raise PublishError("normalized media path escaped music root")
        destination = self._resolve_conflict(destination, request)
        decision = self.repository.managed_decision(
            request.track.source, request.track.track_id, request.quality
        )
        if not decision.allowed:
            raise PublishError("same or lower quality is already managed")

        self.staging_root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="musicdl-tag-", dir=self.staging_root))
        staged_audio = stage / f"audio.{before.extension}"
        try:
            shutil.copyfile(downloaded, staged_audio)
            self.tag_writer.write(staged_audio, request, cover=cover, lyrics=lyrics)
            after = self.probe.probe(staged_audio)
            if after.extension != before.extension or after.codec != before.codec:
                raise PublishError("tagging changed the original media encoding")
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

        destination.parent.mkdir(parents=True, exist_ok=True)
        publish_stage = Path(
            tempfile.mkdtemp(prefix=".musicdl-publish-", dir=destination.parent)
        )
        try:
            final_stage = publish_stage / destination.name
            shutil.copyfile(staged_audio, final_stage)
            if lyrics:
                (publish_stage / f"{destination.stem}.lrc").write_text(
                    lyrics, encoding="utf-8"
                )
            if cover:
                cover_type = detect_image_type(cover) or "image/jpeg"
                cover_name = cover_sidecar_name(cover_type)
                (publish_stage / cover_name).write_bytes(cover)
            for sidecar in publish_stage.iterdir():
                if sidecar.is_file() and sidecar != final_stage:
                    sidecar_destination = destination.parent / sidecar.name
                    if sidecar.name in {"cover.jpg", "cover.png"} and sidecar_destination.exists():
                        sidecar.unlink()
                    else:
                        os.replace(sidecar, sidecar_destination)
            # The hidden staging directory lives on the music filesystem, so this replace is
            # the atomic Navidrome visibility point even when /tmp and /music are separate mounts.
            os.replace(final_stage, destination)
            _fsync_directory(destination.parent)
        finally:
            shutil.rmtree(publish_stage, ignore_errors=True)
            shutil.rmtree(stage, ignore_errors=True)

        old_path = decision.existing_path
        self.repository.register_media(
            request.track.source, request.track.track_id, request.quality, destination
        )
        if old_path and old_path != destination:
            old_path.unlink(missing_ok=True)
            old_path.with_suffix(".lrc").unlink(missing_ok=True)
        return destination

    def relative_path(self, request: JobRequest, extension: str) -> Path:
        track = request.track
        title = sanitize_component(track.title)
        artists = sanitize_component(", ".join(track.artists))
        if track.album:
            album_artist = sanitize_component(track.album_artist or artists)
            album = sanitize_component(track.album)
            if track.year:
                album = f"{album} ({track.year})"
            prefix = ""
            if track.disc and track.track_number:
                prefix = f"{track.disc}-{track.track_number:02d} - "
            elif track.track_number:
                prefix = f"{track.track_number:02d} - "
            return Path(album_artist, album, f"{prefix}{title}.{extension}")
        return Path(artists, "Singles", f"{title}.{extension}")

    @staticmethod
    def metadata_tags(request: JobRequest) -> Mapping[str, str]:
        track = request.track
        values = {
            "TITLE": track.title,
            "ARTIST": "; ".join(track.artists),
            "ALBUM": track.album or "",
            "ALBUMARTIST": track.album_artist or "",
            "TRACKNUMBER": str(track.track_number or ""),
            "DISCNUMBER": str(track.disc or ""),
            "DATE": str(track.year or ""),
        }
        return {key: value for key, value in values.items() if value}

    def _resolve_conflict(self, destination: Path, request: JobRequest) -> Path:
        lyrics_destination = destination.with_suffix(".lrc")
        if not destination.exists() and not lyrics_destination.exists():
            return destination
        decision = self.repository.managed_decision(
            request.track.source, request.track.track_id, request.quality
        )
        if decision.existing_path == destination:
            return destination
        suffix = f" [{request.track.source}-{sanitize_component(request.track.track_id)}]"
        suffixed = destination.with_name(f"{destination.stem}{suffix}{destination.suffix}")
        if suffixed.exists() or suffixed.with_suffix(".lrc").exists():
            raise PublishError("normalized platform path is already occupied by unmanaged media")
        return suffixed


_INVALID = re.compile(r"[\x00-\x1f<>:\"/\\|?*]+")
_SPACES = re.compile(r"\s+")


def sanitize_component(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = _INVALID.sub("_", value)
    value = _SPACES.sub(" ", value).strip(" .")
    if value in {"", ".", ".."}:
        return "Unknown"
    return value[:180].rstrip(" .") or "Unknown"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
