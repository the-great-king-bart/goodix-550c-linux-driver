"""Goodix 27c6:550c reverse-engineering helpers."""

from .protocol import (
    COMMAND_ACK,
    COMMAND_FIRMWARE_VERSION,
    COMMAND_NOP,
    COMMAND_PRESET_PSK_READ,
    COMMAND_READ_OTP,
    COMMAND_READ_SENSOR_REGISTER,
    Frame,
    ProtocolError,
    decode_frame,
    decode_message,
    encode_frame,
    encode_message,
)

__all__ = [
    "COMMAND_ACK",
    "COMMAND_FIRMWARE_VERSION",
    "COMMAND_NOP",
    "COMMAND_PRESET_PSK_READ",
    "COMMAND_READ_OTP",
    "COMMAND_READ_SENSOR_REGISTER",
    "Frame",
    "ProtocolError",
    "decode_frame",
    "decode_message",
    "encode_frame",
    "encode_message",
]

__version__ = "0.1.0"
