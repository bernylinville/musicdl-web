"""Production media inspection backed by ffprobe JSON output."""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from musicdl_web.domain import MediaMetadata, Quality


class MediaProbeError(RuntimeError):
    """A deliberately path-free media inspection failure."""


@dataclass(frozen=True, slots=True)
class ProbeDetails:
    extension: str
    container: str
    codec: str
    duration_seconds: float
    sample_rate_hz: int
    bit_depth: int | None
    bitrate_bps: int
    quality: Quality


Runner = Callable[..., subprocess.CompletedProcess[str]]


class FFprobeMediaProbe:
    """Inspect one audio stream without decoding or modifying the input."""

    def __init__(
        self,
        *,
        executable: str = "ffprobe",
        timeout_seconds: float = 20,
        runner: Runner = subprocess.run,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("ffprobe timeout must be positive")
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def probe(self, path: Path) -> MediaMetadata:
        details = self.inspect(path)
        return MediaMetadata(
            extension=details.extension,
            codec=details.codec,
            duration_seconds=details.duration_seconds,
            quality=details.quality,
        )

    def inspect(self, path: Path) -> ProbeDetails:
        command = [
            self._executable,
            "-v",
            "error",
            "-show_entries",
            (
                "format=format_name,duration,bit_rate:"
                "stream=index,codec_type,codec_name,duration,sample_rate,"
                "bits_per_sample,bits_per_raw_sample,bit_rate"
            ),
            "-of",
            "json",
            "--",
            str(path),
        ]
        try:
            completed = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise MediaProbeError("media probe could not inspect the downloaded file") from None
        if completed.returncode != 0:
            raise MediaProbeError("media probe could not inspect the downloaded file")
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError):
            raise MediaProbeError("media probe returned invalid data") from None
        if not isinstance(payload, dict):
            raise MediaProbeError("media probe returned invalid data")
        return _parse_probe(payload)


def _parse_probe(payload: Mapping[str, Any]) -> ProbeDetails:
    streams = payload.get("streams")
    media_format = payload.get("format")
    if not isinstance(streams, list) or not isinstance(media_format, dict):
        raise MediaProbeError("media probe returned invalid data")
    audio_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]
    if len(audio_streams) != 1:
        raise MediaProbeError("media must contain exactly one audio stream")
    stream = audio_streams[0]

    codec = _required_text(stream, "codec_name").lower()
    format_names = set(_required_text(media_format, "format_name").lower().split(","))
    extension, container = _media_kind(format_names, codec)
    duration = _positive_float(stream.get("duration") or media_format.get("duration"))
    sample_rate = _positive_int(stream.get("sample_rate"))
    bitrate = _positive_int(stream.get("bit_rate") or media_format.get("bit_rate"))
    bit_depth = _optional_positive_int(
        stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
    )
    if duration is None or sample_rate is None or bitrate is None:
        raise MediaProbeError("media technical properties are invalid")
    if codec in _LOSSLESS_CODECS and bit_depth is None:
        raise MediaProbeError("media technical properties are invalid")
    quality = _quality_for(codec, sample_rate, bit_depth, bitrate)
    return ProbeDetails(
        extension=extension,
        container=container,
        codec=codec,
        duration_seconds=duration,
        sample_rate_hz=sample_rate,
        bit_depth=bit_depth,
        bitrate_bps=bitrate,
        quality=quality,
    )


_LOSSLESS_CODECS = frozenset({"flac", "alac"})
_LOSSY_CODECS = frozenset({"mp3", "aac", "vorbis", "opus"})


def _media_kind(format_names: set[str], codec: str) -> tuple[str, str]:
    if "mp3" in format_names and codec == "mp3":
        return "mp3", "mp3"
    if "flac" in format_names and codec == "flac":
        return "flac", "flac"
    if "ogg" in format_names and codec in {"vorbis", "opus", "flac"}:
        return "ogg", "ogg"
    if format_names.intersection({"mov", "mp4", "m4a"}) and codec in {"aac", "alac"}:
        return "m4a", "mp4"
    raise MediaProbeError("media container or codec is unsupported")


def _quality_for(
    codec: str, sample_rate: int, bit_depth: int | None, bitrate: int
) -> Quality:
    if codec in _LOSSLESS_CODECS:
        if sample_rate > 48_000 or (bit_depth is not None and bit_depth > 16):
            return Quality.HI_RES
        return Quality.LOSSLESS
    if codec in _LOSSY_CODECS:
        return Quality.HIGH if bitrate >= 256_000 else Quality.STANDARD
    raise MediaProbeError("media container or codec is unsupported")


def _required_text(values: Mapping[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MediaProbeError("media probe returned invalid data")
    return value.strip()


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_positive_int(value: object) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    return _positive_int(value)
