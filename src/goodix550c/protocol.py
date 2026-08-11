"""Pure codec for the framed Goodix Geneva USB protocol.

The format is derived from the open-source goodix-fp-dump project and checked
against strings in the original 27c6:550c Windows UMDF driver. This module has
no USB access and exposes no state-changing commands.
"""

from __future__ import annotations

from dataclasses import dataclass

FLAGS_MESSAGE_PROTOCOL = 0xA0
COMMAND_NOP = 0x00
COMMAND_READ_SENSOR_REGISTER = 0x82
COMMAND_READ_OTP = 0xA6
COMMAND_FIRMWARE_VERSION = 0xA8
COMMAND_ACK = 0xB0
COMMAND_PRESET_PSK_READ = 0xE4

NOP_PAYLOAD = b"\x00\x00"
READ_SENSOR_CHIP_ID_PAYLOAD = b"\x00\x00\x00\x04\x00"
READ_OTP_PAYLOAD = b"\x00\x00"
PSK_HASH_SLOT = 0xBB020001
PSK_BACKUP_SLOT = 0xBB010002
PSK_BACKUP_LENGTH = 324


def _psk_read_payload(length: int, flags: int, offset: int = 0) -> bytes:
    return (
        length.to_bytes(4, "little")
        + offset.to_bytes(4, "little")
        + flags.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )


READ_PSK_HASH_PAYLOAD = _psk_read_payload(32, PSK_HASH_SLOT)
READ_PSK_BACKUP_PAYLOAD = _psk_read_payload(PSK_BACKUP_LENGTH, PSK_BACKUP_SLOT)

READ_ONLY_REQUESTS: dict[int, tuple[bytes, ...]] = {
    COMMAND_NOP: (NOP_PAYLOAD,),
    COMMAND_READ_SENSOR_REGISTER: (READ_SENSOR_CHIP_ID_PAYLOAD,),
    COMMAND_READ_OTP: (READ_OTP_PAYLOAD,),
    COMMAND_FIRMWARE_VERSION: (b"\x00\x00",),
    COMMAND_PRESET_PSK_READ: (READ_PSK_HASH_PAYLOAD, READ_PSK_BACKUP_PAYLOAD),
}


class ProtocolError(ValueError):
    """The byte stream is not a valid Goodix message."""


@dataclass(frozen=True)
class Frame:
    flags: int
    command: int
    payload: bytes


def _u16le(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ProtocolError(f"length out of range: {value}")
    return value.to_bytes(2, "little")


def encode_message(command: int, payload: bytes) -> bytes:
    """Encode an inner, allow-listed command message."""
    if command not in READ_ONLY_REQUESTS:
        raise ProtocolError(f"command 0x{command:02x} is not in the read-only allow-list")
    if payload not in READ_ONLY_REQUESTS[command]:
        raise ProtocolError(f"payload is not allow-listed for command 0x{command:02x}")

    body = bytes([command]) + _u16le(len(payload) + 1) + payload
    trailer = (0xAA - sum(body)) & 0xFF
    return body + bytes([trailer])


def encode_frame(command: int, payload: bytes) -> bytes:
    """Encode an allow-listed command in the outer 0xa0 transport frame."""
    message = encode_message(command, payload)
    header = bytes([FLAGS_MESSAGE_PROTOCOL]) + _u16le(len(message))
    return header + bytes([sum(header) & 0xFF]) + message


def decode_message(data: bytes) -> tuple[int, bytes]:
    """Decode an inner device message, including responses and ACKs."""
    if len(data) < 4:
        raise ProtocolError("inner message is shorter than four bytes")
    declared = int.from_bytes(data[1:3], "little")
    total = 3 + declared
    if declared < 1 or len(data) < total:
        raise ProtocolError(f"truncated inner message: need {total}, got {len(data)}")

    content = data[:total]
    expected = (0xAA - sum(content[:-1])) & 0xFF
    if content[-1] != expected:
        raise ProtocolError(
            f"bad inner checksum: expected 0x{expected:02x}, got 0x{content[-1]:02x}"
        )
    return content[0], content[3:-1]


def decode_frame(data: bytes) -> Frame:
    """Decode one outer transport frame and its inner message."""
    if len(data) < 8:
        raise ProtocolError("transport frame is shorter than eight bytes")
    declared = int.from_bytes(data[1:3], "little")
    total = 4 + declared
    if len(data) < total:
        raise ProtocolError(f"truncated transport frame: need {total}, got {len(data)}")
    if (sum(data[:3]) & 0xFF) != data[3]:
        raise ProtocolError("bad transport-header checksum")

    if data[0] != FLAGS_MESSAGE_PROTOCOL:
        raise ProtocolError(f"unexpected transport flag 0x{data[0]:02x}")
    command, payload = decode_message(data[4:total])
    return Frame(flags=data[0], command=command, payload=payload)


def decode_ack(frame: Frame, expected_command: int) -> bool:
    """Validate an ACK frame and return the device success bit."""
    if frame.flags != FLAGS_MESSAGE_PROTOCOL or frame.command != COMMAND_ACK:
        raise ProtocolError("response is not a message-protocol ACK")
    if len(frame.payload) < 2 or frame.payload[0] != expected_command:
        raise ProtocolError(f"ACK does not refer to command 0x{expected_command:02x}")
    return bool(frame.payload[1] & 0x01)
