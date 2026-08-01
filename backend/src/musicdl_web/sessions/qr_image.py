"""Dependency-free QR images for short platform login URLs."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Callable

_VERSION = 5
_SIZE = _VERSION * 4 + 17
_DATA_CODEWORDS = 108
_ERROR_CORRECTION_CODEWORDS = 26
_QUIET_ZONE = 4
_MASK = 0


def qr_svg_data_url(payload: str) -> str:
    """Encode *payload* as a self-contained, standards-compliant QR SVG."""
    data = payload.encode("utf-8")
    if len(data) > 106:
        raise ValueError("QR payload is too long")

    matrix = _encode_matrix(data)
    dimension = _SIZE + _QUIET_ZONE * 2
    modules = "".join(
        f"M{x + _QUIET_ZONE} {y + _QUIET_ZONE}h1v1h-1z"
        for y, row in enumerate(matrix)
        for x, dark in enumerate(row)
        if dark
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dimension} {dimension}" '
        'shape-rendering="crispEdges">'
        f'<path fill="#fff" d="M0 0h{dimension}v{dimension}H0z"/>'
        f'<path fill="#000" d="{modules}"/></svg>'
    )
    encoded = b64encode(svg.encode("ascii")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _encode_matrix(data: bytes) -> tuple[tuple[bool, ...], ...]:
    data_codewords = _make_data_codewords(data)
    error_correction = _reed_solomon_remainder(
        data_codewords, _reed_solomon_divisor(_ERROR_CORRECTION_CODEWORDS)
    )
    codewords = data_codewords + error_correction
    bits = tuple((byte >> bit) & 1 != 0 for byte in codewords for bit in range(7, -1, -1))

    modules = [[False] * _SIZE for _ in range(_SIZE)]
    functions = [[False] * _SIZE for _ in range(_SIZE)]

    def set_function(x: int, y: int, dark: bool) -> None:
        modules[y][x] = dark
        functions[y][x] = True

    def draw_finder(center_x: int, center_y: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = center_x + dx, center_y + dy
                if 0 <= x < _SIZE and 0 <= y < _SIZE:
                    distance = max(abs(dx), abs(dy))
                    set_function(x, y, distance not in (2, 4))

    def draw_alignment(center_x: int, center_y: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                set_function(center_x + dx, center_y + dy, max(abs(dx), abs(dy)) != 1)

    draw_finder(3, 3)
    draw_finder(_SIZE - 4, 3)
    draw_finder(3, _SIZE - 4)
    draw_alignment(30, 30)

    for index in range(_SIZE):
        if not functions[6][index]:
            set_function(index, 6, index % 2 == 0)
        if not functions[index][6]:
            set_function(6, index, index % 2 == 0)

    _draw_format_bits(set_function)

    bit_index = 0
    right = _SIZE - 1
    upward = True
    while right >= 1:
        if right == 6:
            right = 5
        for vertical in range(_SIZE):
            y = _SIZE - 1 - vertical if upward else vertical
            for x in (right, right - 1):
                if functions[y][x]:
                    continue
                dark = bits[bit_index] if bit_index < len(bits) else False
                if (x + y) % 2 == 0:  # QR mask pattern 0
                    dark = not dark
                modules[y][x] = dark
                bit_index += 1
        upward = not upward
        right -= 2

    if bit_index < len(bits):
        raise AssertionError("QR matrix did not have enough data modules")
    return tuple(tuple(row) for row in modules)


def _make_data_codewords(data: bytes) -> bytes:
    bits = [False, True, False, False]  # Byte mode indicator: 0100
    bits.extend(bool((len(data) >> bit) & 1) for bit in range(7, -1, -1))
    bits.extend(bool((byte >> bit) & 1) for byte in data for bit in range(7, -1, -1))

    capacity = _DATA_CODEWORDS * 8
    bits.extend(False for _ in range(min(4, capacity - len(bits))))
    bits.extend(False for _ in range((-len(bits)) % 8))
    codewords = bytearray(
        sum(int(bits[offset + bit]) << (7 - bit) for bit in range(8))
        for offset in range(0, len(bits), 8)
    )
    pads = (0xEC, 0x11)
    while len(codewords) < _DATA_CODEWORDS:
        codewords.append(pads[(len(codewords) - len(bits) // 8) % 2])
    return bytes(codewords)


def _reed_solomon_divisor(degree: int) -> bytes:
    result = bytearray(degree)
    result[-1] = 1
    root = 1
    for _ in range(degree):
        for index in range(degree):
            result[index] = _gf_multiply(result[index], root)
            if index + 1 < degree:
                result[index] ^= result[index + 1]
        root = _gf_multiply(root, 0x02)
    return bytes(result)


def _reed_solomon_remainder(data: bytes, divisor: bytes) -> bytes:
    result = bytearray(len(divisor))
    for byte in data:
        factor = byte ^ result[0]
        result[:-1] = result[1:]
        result[-1] = 0
        for index, coefficient in enumerate(divisor):
            result[index] ^= _gf_multiply(coefficient, factor)
    return bytes(result)


def _gf_multiply(left: int, right: int) -> int:
    result = 0
    for bit in range(7, -1, -1):
        result = (result << 1) ^ ((result >> 7) * 0x11D)
        result ^= ((right >> bit) & 1) * left
    return result


def _draw_format_bits(set_function: Callable[[int, int, bool], None]) -> None:
    data = (1 << 3) | _MASK  # Error correction level L is encoded as 01.
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    format_bits = ((data << 10) | remainder) ^ 0x5412

    for index in range(6):
        set_function(8, index, _bit(format_bits, index))
    set_function(8, 7, _bit(format_bits, 6))
    set_function(8, 8, _bit(format_bits, 7))
    set_function(7, 8, _bit(format_bits, 8))
    for index in range(9, 15):
        set_function(14 - index, 8, _bit(format_bits, index))

    for index in range(8):
        set_function(_SIZE - 1 - index, 8, _bit(format_bits, index))
    for index in range(8, 15):
        set_function(8, _SIZE - 15 + index, _bit(format_bits, index))
    set_function(8, _SIZE - 8, True)


def _bit(value: int, index: int) -> bool:
    return ((value >> index) & 1) != 0
