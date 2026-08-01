from __future__ import annotations

from base64 import b64decode
from hashlib import sha256

import pytest
from musicdl_web.sessions.qr_image import _encode_matrix, qr_svg_data_url

PAYLOAD = "https://music.163.com/login?codekey=12345678-1234-1234-1234-123456789abc"


def test_qr_matrix_matches_reference_version_5_l_mask_0_vector() -> None:
    matrix = _encode_matrix(PAYLOAD.encode())
    flattened = [dark for row in matrix for dark in row]
    flattened.extend(False for _ in range((-len(flattened)) % 8))
    packed = bytes(
        sum(int(flattened[offset + bit]) << (7 - bit) for bit in range(8))
        for offset in range(0, len(flattened), 8)
    )

    assert len(matrix) == 37
    assert all(len(row) == 37 for row in matrix)
    # Generated independently with qrcode@1.5.4, forcing byte mode, V5-L, mask 0.
    assert sha256(packed).hexdigest() == (
        "d6f85d08abe2ef4fc4376305c0012afc75144241f8a899d9c956a817fb469f67"
    )


def test_qr_svg_is_self_contained_and_does_not_expose_payload() -> None:
    image = qr_svg_data_url(PAYLOAD)

    assert image.startswith("data:image/svg+xml;base64,")
    assert PAYLOAD not in image
    assert PAYLOAD not in repr(image)
    svg = b64decode(image.partition(",")[2]).decode("ascii")
    assert PAYLOAD not in svg
    assert 'viewBox="0 0 45 45"' in svg
    assert svg.count("<path") == 2
    assert PAYLOAD not in repr(qr_svg_data_url)


def test_qr_svg_rejects_payload_that_exceeds_version_capacity() -> None:
    with pytest.raises(ValueError, match="too long"):
        qr_svg_data_url("x" * 107)
