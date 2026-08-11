from __future__ import annotations

from dataclasses import dataclass

import pytest

from goodix550c.protocol import ProtocolError
from goodix550c.usb_probe import _decode_psk_read_reply, _read_frame


@dataclass
class _Array:
    value: bytes

    def tobytes(self) -> bytes:
        return self.value


class _Endpoint:
    def __init__(self, chunks: list[bytes]):
        self.chunks = iter(chunks)

    def read(self, length: int, timeout: int) -> _Array:
        assert length == 64
        assert timeout > 0
        return _Array(next(self.chunks))


def _outer(payload: bytes) -> bytes:
    header = b"\xa0" + len(payload).to_bytes(2, "little")
    return header + bytes([sum(header) & 0xFF]) + payload


def test_read_frame_reassembles_raw_64_byte_chunks():
    wire = _outer(bytes(range(100)))
    endpoint = _Endpoint([wire[:64], wire[64:]])
    assert _read_frame(endpoint, 1000) == wire


def test_read_frame_accepts_only_zero_usb_padding():
    wire = _outer(b"abcd")
    endpoint = _Endpoint([wire + b"\x00" * 56])
    assert _read_frame(endpoint, 1000) == wire

    endpoint = _Endpoint([wire + b"\x01"])
    with pytest.raises(ProtocolError, match="non-zero bytes"):
        _read_frame(endpoint, 1000)


def test_dpapi_slot_parser_requires_exact_slot_and_size():
    blob = bytes(range(16))
    payload = b"\x00" + (0xBB010002).to_bytes(4, "little")
    payload += len(blob).to_bytes(4, "little") + blob
    assert _decode_psk_read_reply(payload, 0xBB010002, len(blob)) == blob

    with pytest.raises(ProtocolError, match="unexpected PSK slot"):
        _decode_psk_read_reply(payload, 0xBB020001, len(blob))
