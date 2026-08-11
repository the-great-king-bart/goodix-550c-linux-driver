"""Safety-gated USB identity probe for Goodix 27c6:550c."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .protocol import (
    COMMAND_FIRMWARE_VERSION,
    COMMAND_NOP,
    COMMAND_PRESET_PSK_READ,
    COMMAND_READ_OTP,
    COMMAND_READ_SENSOR_REGISTER,
    FLAGS_MESSAGE_PROTOCOL,
    NOP_PAYLOAD,
    PSK_BACKUP_LENGTH,
    PSK_BACKUP_SLOT,
    READ_OTP_PAYLOAD,
    READ_PSK_BACKUP_PAYLOAD,
    READ_PSK_HASH_PAYLOAD,
    READ_SENSOR_CHIP_ID_PAYLOAD,
    ProtocolError,
    decode_ack,
    decode_frame,
    encode_frame,
)

VENDOR_ID = 0x27C6
PRODUCT_ID = 0x550C
KNOWN_DEVELOPMENT_PSK_HASH = "66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925"
DPAPI_PROVIDER_PREFIX = bytes.fromhex("01000000d08c9ddf0115d1118c7a00c04fc297eb")
USB_CHUNK_SIZE = 64
MAX_TRANSPORT_FRAME = 64 * 1024


@dataclass(frozen=True)
class TransferRecord:
    direction: str
    purpose: str
    data_hex: str


@dataclass(frozen=True)
class ProbeResult:
    timestamp_utc: str
    vendor_id: str
    product_id: str
    bus: int | None
    address: int | None
    interface: int
    endpoint_out: str
    endpoint_in: str
    manufacturer: str | None
    product: str | None
    firmware_version: str
    transfers: tuple[TransferRecord, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def packet_preview() -> dict[str, str]:
    return {
        "nop": encode_frame(COMMAND_NOP, NOP_PAYLOAD).hex(),
        "chip_id": encode_frame(COMMAND_READ_SENSOR_REGISTER, READ_SENSOR_CHIP_ID_PAYLOAD).hex(),
        "firmware_version": encode_frame(COMMAND_FIRMWARE_VERSION, b"\x00\x00").hex(),
        "otp": encode_frame(COMMAND_READ_OTP, READ_OTP_PAYLOAD).hex(),
        "psk_hash": encode_frame(COMMAND_PRESET_PSK_READ, READ_PSK_HASH_PAYLOAD).hex(),
        "psk_dpapi_backup": encode_frame(
            COMMAND_PRESET_PSK_READ, READ_PSK_BACKUP_PAYLOAD
        ).hex(),
    }


def _find_bulk_endpoint(interface: Any, direction: int, usb_util: Any) -> Any:
    endpoint = usb_util.find_descriptor(
        interface,
        custom_match=lambda item: (
            usb_util.endpoint_direction(item.bEndpointAddress) == direction
            and usb_util.endpoint_type(item.bmAttributes) == 2
        ),
    )
    if endpoint is None:
        label = "IN" if direction == 0x80 else "OUT"
        raise RuntimeError(f"bulk {label} endpoint not found")
    return endpoint


def _read(endpoint: Any, timeout_ms: int) -> bytes:
    return endpoint.read(USB_CHUNK_SIZE, timeout=timeout_ms).tobytes()


def _read_frame(endpoint: Any, timeout_ms: int, continuation_timeout_ms: int = 1000) -> bytes:
    """Read and strictly reassemble one outer transport frame."""
    data = bytearray(_read(endpoint, timeout_ms))
    if len(data) < 4:
        raise ProtocolError(f"short transport header: got {len(data)} bytes")
    if (sum(data[:3]) & 0xFF) != data[3]:
        raise ProtocolError("bad transport-header checksum")
    declared = int.from_bytes(data[1:3], "little")
    total = 4 + declared
    if declared < 4 or total > MAX_TRANSPORT_FRAME:
        raise ProtocolError(f"implausible transport-frame length: {total}")

    while len(data) < total:
        chunk = _read(endpoint, continuation_timeout_ms)
        if not chunk:
            raise ProtocolError("zero-length continuation while reassembling a frame")
        data.extend(chunk)
        if len(data) > MAX_TRANSPORT_FRAME:
            raise ProtocolError("transport frame exceeds the reassembly limit")

    trailing = data[total:]
    if any(trailing):
        raise ProtocolError("non-zero bytes follow the declared transport frame")
    return bytes(data[:total])


def _transfer_summaries(records: list[TransferRecord]) -> list[dict[str, object]]:
    """Return non-secret integrity metadata instead of raw device bytes."""
    summaries: list[dict[str, object]] = []
    for record in records:
        raw = bytes.fromhex(record.data_hex)
        summaries.append(
            {
                "direction": record.direction,
                "purpose": record.purpose,
                "length": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return summaries


def _send(endpoint: Any, packet: bytes) -> bytes:
    padded = packet + b"\x00" * ((-len(packet)) % 64)
    written = endpoint.write(padded, timeout=1000)
    if written != len(padded):
        raise RuntimeError(f"short USB write: {written}/{len(padded)}")
    return padded


def run_probe() -> ProbeResult:
    """Send only NOP and firmware-version commands and return decoded identity."""
    try:
        import usb.core
        import usb.util
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError("PyUSB is not installed; run the documented venv setup") from exc

    device = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if device is None:
        raise RuntimeError("Goodix 27c6:550c not found")

    records: list[TransferRecord] = []
    configuration = device.get_active_configuration()
    interface = usb.util.find_descriptor(
        configuration, custom_match=lambda item: item.bInterfaceClass == 0xFF
    )
    if interface is None:
        raise RuntimeError("vendor-specific USB interface not found")
    interface_number = int(interface.bInterfaceNumber)
    if interface_number != 0:
        raise RuntimeError(f"unexpected USB interface {interface_number}; expected 0")
    endpoint_in = _find_bulk_endpoint(interface, 0x80, usb.util)
    endpoint_out = _find_bulk_endpoint(interface, 0x00, usb.util)
    if int(endpoint_in.bEndpointAddress) != 0x83 or int(endpoint_out.bEndpointAddress) != 0x01:
        raise RuntimeError("unexpected endpoint layout; refusing to contact the device")

    if device.is_kernel_driver_active(interface_number):
        raise RuntimeError("a kernel driver is active; refusing to detach it")

    usb.util.claim_interface(device, interface_number)
    try:
        while True:
            try:
                stale = _read(endpoint_in, 30)
                records.append(TransferRecord("in", "pre-existing buffered data", stale.hex()))
            except usb.core.USBTimeoutError:
                break

        nop = encode_frame(COMMAND_NOP, NOP_PAYLOAD)
        padded_nop = _send(endpoint_out, nop)
        records.append(TransferRecord("out", "NOP/wake (non-persistent)", padded_nop.hex()))
        nop_reply = _read_frame(endpoint_in, 1000)
        records.append(TransferRecord("in", "NOP ACK", nop_reply.hex()))
        if not decode_ack(decode_frame(nop_reply), COMMAND_NOP):
            raise ProtocolError("device rejected the NOP request")

        request = encode_frame(COMMAND_FIRMWARE_VERSION, b"\x00\x00")
        padded_request = _send(endpoint_out, request)
        records.append(
            TransferRecord("out", "firmware-version request (read-only)", padded_request.hex())
        )

        ack_raw = _read_frame(endpoint_in, 1000)
        records.append(TransferRecord("in", "firmware-version ACK", ack_raw.hex()))
        ack = decode_frame(ack_raw)
        if not decode_ack(ack, COMMAND_FIRMWARE_VERSION):
            raise ProtocolError("device rejected the firmware-version request")

        version_raw = _read_frame(endpoint_in, 2000)
        records.append(TransferRecord("in", "firmware-version response", version_raw.hex()))
        version = decode_frame(version_raw)
        if version.flags != FLAGS_MESSAGE_PROTOCOL or version.command != COMMAND_FIRMWARE_VERSION:
            raise ProtocolError("unexpected firmware-version response")
        firmware = version.payload.split(b"\x00", 1)[0].decode("ascii", errors="strict")
        if not firmware or len(firmware) > 96 or not firmware.isprintable():
            raise ProtocolError("firmware version is empty or implausible")

        def safe_string(index: int) -> str | None:
            if not index:
                return None
            try:
                return usb.util.get_string(device, index)
            except usb.core.USBError:
                return None

        return ProbeResult(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            vendor_id=f"{VENDOR_ID:04x}",
            product_id=f"{PRODUCT_ID:04x}",
            bus=getattr(device, "bus", None),
            address=getattr(device, "address", None),
            interface=interface_number,
            endpoint_out=f"0x{int(endpoint_out.bEndpointAddress):02x}",
            endpoint_in=f"0x{int(endpoint_in.bEndpointAddress):02x}",
            manufacturer=safe_string(int(device.iManufacturer)),
            product=safe_string(int(device.iProduct)),
            firmware_version=firmware,
            transfers=tuple(records),
        )
    finally:
        usb.util.release_interface(device, interface_number)
        usb.util.dispose_resources(device)


def write_result(result: ProbeResult, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.to_json(), encoding="utf-8")


QUERY_SPECS: dict[str, tuple[int, bytes]] = {
    "chip-id": (COMMAND_READ_SENSOR_REGISTER, READ_SENSOR_CHIP_ID_PAYLOAD),
    "otp": (COMMAND_READ_OTP, READ_OTP_PAYLOAD),
    "psk-hash": (COMMAND_PRESET_PSK_READ, READ_PSK_HASH_PAYLOAD),
}


def _decode_psk_read_reply(payload: bytes, expected_slot: int, expected_length: int) -> bytes:
    if len(payload) < 9:
        raise ProtocolError("PSK-slot response is too short")
    if payload[0] != 0:
        raise ProtocolError(f"PSK-slot command returned status 0x{payload[0]:02x}")
    slot = int.from_bytes(payload[1:5], "little")
    length = int.from_bytes(payload[5:9], "little")
    if slot != expected_slot:
        raise ProtocolError(f"unexpected PSK slot 0x{slot:08x}")
    if length != expected_length:
        raise ProtocolError(
            f"unexpected PSK-slot length {length}; expected {expected_length}"
        )
    if len(payload) != 9 + length:
        raise ProtocolError("PSK-slot response length does not match its length field")
    return payload[9:]


def _decode_query_value(name: str, payload: bytes) -> dict[str, object]:
    if name == "chip-id":
        if len(payload) != 4:
            raise ProtocolError(f"chip-ID response is {len(payload)} bytes; expected 4")
        return {"chip_id_hex": payload.hex()}
    if name == "otp":
        if not payload:
            raise ProtocolError("OTP response is empty")
        return {
            "otp_length": len(payload),
            "otp_sha256": hashlib.sha256(payload).hexdigest(),
        }
    if name == "psk-hash":
        value = _decode_psk_read_reply(payload, 0xBB020001, 32).hex()
        return {
            "psk_flags": "0xbb020001",
            "psk_hash_hex": value,
            "matches_known_development_psk": value == KNOWN_DEVELOPMENT_PSK_HASH,
        }
    raise ValueError(f"unknown read-only query: {name}")


def _run_raw_query(
    name: str, command: int, request_payload: bytes
) -> tuple[bytes, list[TransferRecord]]:
    """Run a NOP plus one exact read command and return its checked payload."""
    try:
        import usb.core
        import usb.util
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError("PyUSB is not installed; run the documented venv setup") from exc

    device = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if device is None:
        raise RuntimeError("Goodix 27c6:550c not found")
    configuration = device.get_active_configuration()
    interface = usb.util.find_descriptor(
        configuration, custom_match=lambda item: item.bInterfaceClass == 0xFF
    )
    if interface is None:
        raise RuntimeError("vendor-specific USB interface not found")
    interface_number = int(interface.bInterfaceNumber)
    if interface_number != 0:
        raise RuntimeError(f"unexpected USB interface {interface_number}; expected 0")
    endpoint_in = _find_bulk_endpoint(interface, 0x80, usb.util)
    endpoint_out = _find_bulk_endpoint(interface, 0x00, usb.util)
    if int(endpoint_in.bEndpointAddress) != 0x83 or int(endpoint_out.bEndpointAddress) != 0x01:
        raise RuntimeError("unexpected endpoint layout; refusing to contact the device")
    if device.is_kernel_driver_active(interface_number):
        raise RuntimeError("a kernel driver is active; refusing to detach it")

    records: list[TransferRecord] = []
    usb.util.claim_interface(device, interface_number)
    try:
        while True:
            try:
                stale = _read(endpoint_in, 30)
                records.append(TransferRecord("in", "pre-existing buffered data", stale.hex()))
            except usb.core.USBTimeoutError:
                break

        nop = _send(endpoint_out, encode_frame(COMMAND_NOP, NOP_PAYLOAD))
        records.append(TransferRecord("out", "NOP/wake (non-persistent)", nop.hex()))
        reply = _read_frame(endpoint_in, 1000)
        records.append(TransferRecord("in", "NOP ACK", reply.hex()))
        if not decode_ack(decode_frame(reply), COMMAND_NOP):
            raise ProtocolError("device rejected the NOP request")

        request = _send(endpoint_out, encode_frame(command, request_payload))
        records.append(TransferRecord("out", f"{name} request (read-only)", request.hex()))

        ack_raw = _read_frame(endpoint_in, 1000)
        records.append(TransferRecord("in", f"{name} ACK", ack_raw.hex()))
        if not decode_ack(decode_frame(ack_raw), command):
            raise ProtocolError(f"device rejected {name} request")

        response_raw = _read_frame(endpoint_in, 2000)
        records.append(TransferRecord("in", f"{name} response", response_raw.hex()))
        response = decode_frame(response_raw)
        if response.flags != FLAGS_MESSAGE_PROTOCOL or response.command != command:
            raise ProtocolError(f"unexpected {name} response")

        return response.payload, records
    finally:
        usb.util.release_interface(device, interface_number)
        usb.util.dispose_resources(device)


def run_read_only_query(name: str) -> dict[str, object]:
    """Run one isolated, exact read-only query against the sensor."""
    if name not in QUERY_SPECS:
        raise ValueError(f"unknown read-only query: {name}")
    command, request_payload = QUERY_SPECS[name]
    payload, records = _run_raw_query(name, command, request_payload)
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "query": name,
        **_decode_query_value(name, payload),
        "transfer_summaries": _transfer_summaries(records),
    }


def read_dpapi_backup() -> tuple[bytes, dict[str, object]]:
    """Read the inert, machine-sealed PSK backup slot without exposing it in logs."""
    payload, records = _run_raw_query(
        "PSK DPAPI-backup",
        COMMAND_PRESET_PSK_READ,
        READ_PSK_BACKUP_PAYLOAD,
    )
    blob = _decode_psk_read_reply(payload, PSK_BACKUP_SLOT, PSK_BACKUP_LENGTH)
    if not blob.startswith(DPAPI_PROVIDER_PREFIX):
        raise ProtocolError("PSK backup does not have the expected Windows DPAPI header")
    metadata: dict[str, object] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "slot": f"0x{PSK_BACKUP_SLOT:08x}",
        "length": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "transfer_summaries": _transfer_summaries(records),
    }
    return blob, metadata


def write_private_blob(blob: bytes, output: Path) -> Path:
    """Create a mode-0600 secret below this repository's ignored secret area."""
    project_root = Path(__file__).resolve().parents[2]
    secrets_root = project_root / "research" / "secrets"
    secrets_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    secrets_root.chmod(0o700)

    candidate = output if output.is_absolute() else Path.cwd() / output
    candidate = candidate.resolve(strict=False)
    if not candidate.is_relative_to(secrets_root.resolve()):
        raise RuntimeError(f"secret output must be below {secrets_root}")
    candidate.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    return candidate
