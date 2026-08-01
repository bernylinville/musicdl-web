"""Map platform exact tiers to what ffprobe can classify after download."""

from __future__ import annotations

from musicdl_web.domain import JobRequest, Quality


def probe_matches_request(request: JobRequest, probed: Quality) -> bool:
    """Return True when *probed* is consistent with the already-validated selection.

    Exact platform revalidation has already locked the Netease ``level``. FFprobe
    only reports coarse classes (lossy bitrate / lossless / hi-res) and cannot name
    commercial packages such as 超清母带, nor always separate 较高 (~192k) from 标准.
    """

    quality_id = request.quality_id
    selected = request.quality
    if quality_id == "jymaster" or selected is Quality.MASTER:
        return probed in {Quality.LOSSLESS, Quality.HI_RES, Quality.MASTER}
    if quality_id == "higher":
        return probed in {Quality.STANDARD, Quality.HIGH}
    if quality_id == "hires" or selected is Quality.HI_RES:
        return probed is Quality.HI_RES
    return probed is selected
