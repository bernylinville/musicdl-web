"""Sanitized capability errors."""


class PlatformCapabilityError(Exception):
    """Base error for post-search platform capabilities."""


class CapabilityUnavailable(PlatformCapabilityError):
    """The platform cannot prove this capability in the current environment."""


class QualitySnapshotExpired(PlatformCapabilityError):
    """The selected per-track capability snapshot has expired."""


class QualitySnapshotMismatch(PlatformCapabilityError):
    """A snapshot is bound to another track, platform, or session version."""


class ExactQualityMismatch(PlatformCapabilityError):
    """The platform response did not match the one requested native tier."""


class PreviewUnavailable(CapabilityUnavailable):
    """No platform-authorized short preview was present."""
