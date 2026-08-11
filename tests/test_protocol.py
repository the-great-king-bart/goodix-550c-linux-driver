import pytest

from goodix550c.protocol import (
    COMMAND_FIRMWARE_VERSION,
    COMMAND_NOP,
    COMMAND_PRESET_PSK_READ,
    COMMAND_READ_SENSOR_REGISTER,
    NOP_PAYLOAD,
    READ_PSK_BACKUP_PAYLOAD,
    READ_SENSOR_CHIP_ID_PAYLOAD,
    ProtocolError,
    decode_frame,
    decode_message,
    encode_frame,
    encode_message,
)


def test_exact_nop_packet_matches_documented_geneva_framing():
    packet = encode_frame(COMMAND_NOP, NOP_PAYLOAD)
    assert packet.hex() == "a00600a60003000000a7"


def test_exact_firmware_request_packet():
    packet = encode_frame(COMMAND_FIRMWARE_VERSION, b"\x00\x00")
    assert packet.hex() == "a00600a6a803000000ff"


def test_exact_chip_id_request_packet():
    packet = encode_frame(COMMAND_READ_SENSOR_REGISTER, READ_SENSOR_CHIP_ID_PAYLOAD)
    assert packet.hex() == "a00900a982060000000004001e"


def test_exact_dpapi_backup_request_packet():
    packet = encode_frame(COMMAND_PRESET_PSK_READ, READ_PSK_BACKUP_PAYLOAD)
    assert packet.hex() == "a01400b4e411004401000000000000020001bb00000000b2"


def test_decode_firmware_response_round_trip_shape():
    payload = b"GF3208_ST411SEC_APP_10020\x00"
    inner_without_checksum = bytes([COMMAND_FIRMWARE_VERSION]) + (len(payload) + 1).to_bytes(
        2, "little"
    ) + payload
    inner = inner_without_checksum + bytes([(0xAA - sum(inner_without_checksum)) & 0xFF])
    header = b"\xa0" + len(inner).to_bytes(2, "little")
    wire = header + bytes([sum(header) & 0xFF]) + inner

    decoded = decode_frame(wire)
    assert decoded.command == COMMAND_FIRMWARE_VERSION
    assert decoded.payload.rstrip(b"\x00") == b"GF3208_ST411SEC_APP_10020"


def test_rejects_state_changing_command():
    with pytest.raises(ProtocolError, match="allow-list"):
        encode_message(0xF0, b"payload")


def test_rejects_mutated_payload_for_allowed_command():
    with pytest.raises(ProtocolError, match="payload"):
        encode_message(COMMAND_FIRMWARE_VERSION, b"\x01\x00")


def test_rejects_bad_checksum():
    packet = bytearray(encode_frame(COMMAND_FIRMWARE_VERSION, b"\x00\x00"))
    packet[-1] ^= 1
    with pytest.raises(ProtocolError, match="checksum"):
        decode_frame(bytes(packet))


def test_rejects_truncated_inner_message():
    with pytest.raises(ProtocolError, match="truncated"):
        decode_message(b"\xa8\x05\x00\x00")
