"""Platform capability contracts shared by adapters and orchestration."""

from .errors import (
    CapabilityUnavailable,
    ExactQualityMismatch,
    PreviewUnavailable,
    QualitySnapshotExpired,
    QualitySnapshotMismatch,
)
from .preview import (
    PlatformPreviewResolver,
    PreviewLease,
    PreviewRegistry,
    UnavailablePreviewResolver,
)
from .quality import (
    DownloadGrant,
    FidelityFamily,
    QualityOption,
    QualitySnapshot,
    QualitySnapshotStore,
)

__all__ = [
    "CapabilityUnavailable",
    "DownloadGrant",
    "ExactQualityMismatch",
    "FidelityFamily",
    "PlatformPreviewResolver",
    "PreviewLease",
    "PreviewRegistry",
    "PreviewUnavailable",
    "QualityOption",
    "QualitySnapshot",
    "QualitySnapshotExpired",
    "QualitySnapshotMismatch",
    "QualitySnapshotStore",
    "UnavailablePreviewResolver",
]
