#!/usr/bin/env python3
"""Recover the Goodix 550c live PSK from a Windows LocalMachine DPAPI blob.

This utility is deliberately offline. It has no USB dependency, opens the
Windows registry hives read-only, targets only the DPAPI_SYSTEM LSA secret,
and never writes or prints the recovered key unless an explicit repository-
local output path is supplied. Even then, it writes a mode-0600 hex file only
below the ignored ``research/secrets`` directory.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import string
import sys
import uuid
from binascii import unhexlify
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WINDOWS_ROOT = PROJECT_ROOT / ".dpapi-windows-ro"
DEFAULT_BLOB = PROJECT_ROOT / "research/secrets/psk-dpapi.bin"
DEFAULT_HASH = PROJECT_ROOT / "research/artifacts/psk-hash.json"
SECRET_ROOT = PROJECT_ROOT / "research/secrets"

GOODIX_DPAPI_SLOT = 0xBB010002
GOODIX_PSK_HASH_SLOT = 0xBB020001
GOODIX_DPAPI_BLOB_LEN = 324
GOODIX_PSK_LEN = 32


class RecoveryError(RuntimeError):
    """A safe, non-secret-bearing recovery error."""


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def project_input_path(value: str | Path, *, label: str) -> Path:
    """Resolve an existing input while enforcing the repository boundary."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RecoveryError(f"{label} does not exist") from exc
    if not _inside(resolved, PROJECT_ROOT):
        raise RecoveryError(f"{label} must resolve inside the repository")
    return resolved


def secret_output_path(value: str | Path) -> Path:
    """Resolve a new output below the ignored repository-local secret store."""
    SECRET_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(SECRET_ROOT, 0o700)

    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve(strict=False)
    secret_root = SECRET_ROOT.resolve(strict=True)
    if not _inside(resolved, secret_root) or resolved == secret_root:
        raise RecoveryError("output must be a file below research/secrets")
    if resolved.exists() or resolved.is_symlink():
        raise RecoveryError("refusing to overwrite an existing secret output")
    if resolved.parent != secret_root:
        raise RecoveryError("nested secret output directories are not supported")
    return resolved


def require_read_only_filesystem(path: Path) -> None:
    """Fail closed unless the source path is on a read-only mount."""
    readonly_flag = getattr(os, "ST_RDONLY", 1)
    if not os.statvfs(path).f_flag & readonly_flag:
        raise RecoveryError("Windows root is not on a read-only mount")


def goodix_dpapi_read_payload() -> bytes:
    """Return the offline E4 payload for slot 0xbb010002, length 324."""
    return (
        GOODIX_DPAPI_BLOB_LEN.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + GOODIX_DPAPI_SLOT.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )


def _decode_hex_text(data: bytes) -> bytes:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RecoveryError("blob input is not ASCII hex") from exc
    compact = "".join(text.split())
    if compact.startswith("0x"):
        compact = compact[2:]
    if not compact or len(compact) % 2 or any(char not in string.hexdigits for char in compact):
        raise RecoveryError("blob input is not valid hexadecimal")
    return bytes.fromhex(compact)


def extract_goodix_dpapi_blob(data: bytes, input_format: str = "auto") -> bytes:
    """Extract a 324-byte DPAPI blob from raw, hex, or framed E4 input."""
    if input_format not in {"auto", "raw", "hex", "e4-frame"}:
        raise RecoveryError("unknown blob input format")

    decoded = data
    if input_format == "hex":
        decoded = _decode_hex_text(data)
    elif input_format == "auto" and len(data) != GOODIX_DPAPI_BLOB_LEN:
        stripped = data.strip()
        if stripped and all(chr(byte) in string.hexdigits + "xX \t\r\n" for byte in stripped):
            decoded = _decode_hex_text(data)

    if input_format == "raw" or (input_format == "auto" and len(decoded) == GOODIX_DPAPI_BLOB_LEN):
        if len(decoded) != GOODIX_DPAPI_BLOB_LEN:
            raise RecoveryError("raw Goodix DPAPI blob must be exactly 324 bytes")
        return decoded

    try:
        from goodix550c.protocol import (
            COMMAND_PRESET_PSK_READ,
            FLAGS_MESSAGE_PROTOCOL,
            ProtocolError,
            decode_frame,
        )
    except ImportError as exc:
        raise RecoveryError("project package is not installed; run the documented setup") from exc

    if len(decoded) < 4:
        raise RecoveryError("E4 frame is truncated")
    frame_len = 4 + int.from_bytes(decoded[1:3], "little")
    if len(decoded) < frame_len:
        raise RecoveryError("E4 frame is truncated")
    if any(decoded[frame_len:]):
        raise RecoveryError("E4 frame has non-zero trailing data")

    try:
        frame = decode_frame(decoded[:frame_len])
    except ProtocolError as exc:
        raise RecoveryError("invalid Goodix E4 frame") from exc
    if frame.flags != FLAGS_MESSAGE_PROTOCOL or frame.command != COMMAND_PRESET_PSK_READ:
        raise RecoveryError("input is not a Goodix E4 preset-PSK-read response")
    if len(frame.payload) < 9:
        raise RecoveryError("Goodix E4 response payload is truncated")
    if frame.payload[0] != 0:
        raise RecoveryError("Goodix E4 response reports failure")

    slot = int.from_bytes(frame.payload[1:5], "little")
    declared = int.from_bytes(frame.payload[5:9], "little")
    if slot != GOODIX_DPAPI_SLOT:
        raise RecoveryError("Goodix E4 response is not slot 0xbb010002")
    if declared != GOODIX_DPAPI_BLOB_LEN:
        raise RecoveryError("Goodix DPAPI slot length is not 324 bytes")
    if len(frame.payload) != 9 + declared:
        raise RecoveryError("Goodix E4 response length does not match its payload")
    return frame.payload[9:]


def expected_hash_from_data(data: bytes) -> bytes:
    """Parse a raw digest, a hex digest, or the local query JSON artifact."""
    if len(data) == 32:
        return data

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecoveryError("expected-hash file has an unsupported format") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        compact = "".join(text.split())
    else:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("psk_hash_hex"), str):
            raise RecoveryError("expected-hash JSON lacks psk_hash_hex")
        compact = parsed["psk_hash_hex"]

    if len(compact) != 64 or any(char not in string.hexdigits for char in compact):
        raise RecoveryError("expected live-PSK hash must be 32 bytes")
    return bytes.fromhex(compact)


def verify_live_psk(psk: bytes, expected_hash: bytes) -> None:
    if len(psk) != GOODIX_PSK_LEN:
        raise RecoveryError("DPAPI plaintext is not a 32-byte Goodix PSK")
    if len(expected_hash) != 32:
        raise RecoveryError("expected live-PSK hash is not 32 bytes")
    if not hmac.compare_digest(hashlib.sha256(psk).digest(), expected_hash):
        raise RecoveryError("recovered PSK does not match the live sensor hash oracle")


def _load_impacket() -> dict[str, Any]:
    try:
        from impacket import winregistry
        from impacket.dpapi import DPAPI_BLOB, DPAPI_SYSTEM, MasterKey, MasterKeyFile
        from impacket.examples.secretsdump import LSA_SECRET, LSA_SECRET_BLOB, LSASecrets
        from impacket.uuid import bin_to_string
    except ImportError as exc:
        raise RecoveryError("Impacket is required; install the project's dpapi extra") from exc
    return {
        "winregistry": winregistry,
        "DPAPI_BLOB": DPAPI_BLOB,
        "DPAPI_SYSTEM": DPAPI_SYSTEM,
        "MasterKey": MasterKey,
        "MasterKeyFile": MasterKeyFile,
        "LSASecrets": LSASecrets,
        "LSA_SECRET": LSA_SECRET,
        "LSA_SECRET_BLOB": LSA_SECRET_BLOB,
        "bin_to_string": bin_to_string,
    }


@contextmanager
def _read_only_impacket_registry(bindings: dict[str, Any]) -> Iterator[None]:
    """Make Impacket use ``rb`` instead of its unnecessary ``r+b`` mode."""
    winregistry = bindings["winregistry"]
    original_parser = winregistry.saveRegistryParser

    class ReadOnlyRegistryParser(original_parser):
        def __init__(self, hive: str, isRemote: bool = False) -> None:  # noqa: N803
            if isRemote:
                raise RecoveryError("remote registry access is disabled")
            self._saveRegistryParser__hive = hive
            self.fd = open(hive, "rb")  # noqa: SIM115 - parser owns the handle until close()
            header = self.fd.read(4096)
            self._saveRegistryParser__regf = winregistry.REG_REGF(header)
            self.indent = ""
            self.rootKey = self._saveRegistryParser__findRootKey()
            if self.rootKey is None:
                self.fd.close()
                raise RecoveryError("registry hive root key was not found")

    impacket_log = logging.getLogger("impacket")
    previous_level = impacket_log.level
    impacket_log.setLevel(logging.CRITICAL)
    try:
        with patch.object(winregistry, "saveRegistryParser", ReadOnlyRegistryParser):
            yield
    finally:
        impacket_log.setLevel(previous_level)


def _read_boot_key(system_hive: Path, bindings: dict[str, Any]) -> bytearray:
    winregistry = bindings["winregistry"]
    registry = winregistry.get_registry_parser(str(system_hive), False)
    try:
        current_value = registry.getValue(r"\Select\Current")
        if current_value is None:
            raise RecoveryError("SYSTEM hive lacks the current control-set selector")
        control_set = f"ControlSet{current_value[1]:03d}"
        encoded = b""
        for name in ("JD", "Skew1", "GBG", "Data"):
            value = registry.getClass(f"\\{control_set}\\Control\\Lsa\\{name}")
            if value is None:
                raise RecoveryError("SYSTEM hive lacks boot-key material")
            encoded += value[:16].decode("utf-16le").encode("ascii")
    finally:
        registry.close()

    try:
        scrambled = unhexlify(encoded)
    except ValueError as exc:
        raise RecoveryError("SYSTEM boot-key material is malformed") from exc
    transform = (8, 5, 4, 2, 11, 9, 13, 3, 0, 6, 1, 12, 14, 10, 15, 7)
    return bytearray(scrambled[index] for index in transform)


def _load_dpapi_system_keys(
    system_hive: Path, security_hive: Path, bindings: dict[str, Any]
) -> tuple[bytearray, bytearray]:
    boot_key = _read_boot_key(system_hive, bindings)
    lsa = bindings["LSASecrets"](
        str(security_hive),
        bytes(boot_key),
        None,
        isRemote=False,
        history=False,
        perSecretCallback=lambda *_: None,
    )
    try:
        lsa._LSASecrets__getLSASecretKey()
        value = lsa.getValue(r"\Policy\Secrets\DPAPI_SYSTEM\CurrVal\default")
        if value is None or not value[1]:
            raise RecoveryError("SECURITY hive lacks the DPAPI_SYSTEM secret")
        encrypted = bindings["LSA_SECRET"](value[1])
        lsa_key = lsa._LSASecrets__LSAKey
        if not lsa_key:
            raise RecoveryError("SECURITY hive LSA key could not be decrypted")
        temporary_key = lsa._LSASecrets__sha256(lsa_key, encrypted["EncryptedData"][:32])
        clear = lsa._LSASecrets__cryptoCommon.decryptAES(
            temporary_key, encrypted["EncryptedData"][32:]
        )
        secret = bindings["LSA_SECRET_BLOB"](clear)["Secret"]
        dpapi_system = bindings["DPAPI_SYSTEM"](secret)
        machine_key = bytearray(dpapi_system["MachineKey"])
        user_key = bytearray(dpapi_system["UserKey"])
        if len(machine_key) != 20 or len(user_key) != 20:
            raise RecoveryError("DPAPI_SYSTEM keys have unexpected lengths")
        return machine_key, user_key
    finally:
        lsa.finish()
        _zero(boot_key)


def _masterkey_sections(data: bytes, bindings: dict[str, Any]) -> list[bytes]:
    try:
        header = bindings["MasterKeyFile"](data)
    except Exception as exc:
        raise RecoveryError("DPAPI master-key file header is malformed") from exc
    offset = len(header)
    sections: list[bytes] = []
    for field in ("MasterKeyLen", "BackupKeyLen"):
        length = int(header[field])
        if length:
            if offset + length > len(data):
                raise RecoveryError("DPAPI master-key file is truncated")
            sections.append(data[offset : offset + length])
        offset += length
    return sections


def _decrypt_masterkey(
    masterkey_file: Path,
    protection_keys: tuple[bytearray, ...],
    bindings: dict[str, Any],
) -> bytearray:
    sections = _masterkey_sections(masterkey_file.read_bytes(), bindings)
    for section in sections:
        for system_key in protection_keys:
            try:
                decrypted = bindings["MasterKey"](section).decrypt(bytes(system_key))
            except Exception:
                decrypted = None
            if decrypted is not None:
                return bytearray(decrypted)
    raise RecoveryError("matching DPAPI master key could not be decrypted")


def _parse_dpapi_blob(blob_data: bytes, bindings: dict[str, Any]) -> tuple[Any, str]:
    try:
        blob = bindings["DPAPI_BLOB"](blob_data)
    except Exception as exc:
        raise RecoveryError("Goodix slot does not contain a valid DPAPI blob") from exc
    if len(blob) != len(blob_data):
        raise RecoveryError("DPAPI blob has trailing or incomplete data")
    guid = bindings["bin_to_string"](blob["GuidMasterKey"]).strip("{}").lower()
    try:
        guid = str(uuid.UUID(guid))
    except ValueError as exc:
        raise RecoveryError("DPAPI blob master-key GUID is malformed") from exc
    return blob, guid


def _masterkey_path(protect_root: Path, guid: str) -> tuple[Path, str]:
    # DPAPI_BLOB.Flags does not reliably serialize the ProtectData input scope.
    # Select the DPAPI_SYSTEM wrapping key strictly from the LocalSystem master-
    # key namespace. The captured Goodix blob resolves to S-1-5-18/User and is
    # therefore tried only with UserKey; there is no cross-key fallback.
    candidates = (
        (protect_root / guid, "machine"),
        (protect_root / "User" / guid, "user"),
    )
    for candidate, key_kind in candidates:
        if candidate.is_file():
            return candidate, key_kind
    raise RecoveryError("DPAPI blob's matching LocalSystem master-key file is absent")


def recover_psk(windows_root: Path, blob_data: bytes) -> bytearray:
    """Perform targeted, offline LocalMachine DPAPI unprotection."""
    bindings = _load_impacket()
    system_hive = windows_root / "Windows/System32/config/SYSTEM"
    security_hive = windows_root / "Windows/System32/config/SECURITY"
    protect_root = windows_root / "Windows/System32/Microsoft/Protect/S-1-5-18"
    for source in (system_hive, security_hive, protect_root):
        if not source.exists():
            raise RecoveryError("Windows DPAPI source set is incomplete")

    with _read_only_impacket_registry(bindings):
        blob, guid = _parse_dpapi_blob(blob_data, bindings)
        masterkey_file, key_kind = _masterkey_path(protect_root, guid)
        machine_key, user_key = _load_dpapi_system_keys(system_hive, security_hive, bindings)
        master_key = bytearray()
        try:
            protection_key = machine_key if key_kind == "machine" else user_key
            master_key = _decrypt_masterkey(masterkey_file, (protection_key,), bindings)
            try:
                clear = blob.decrypt(bytes(master_key), None)
            except Exception as exc:
                raise RecoveryError("DPAPI integrity verification failed") from exc
            if clear is None:
                raise RecoveryError("DPAPI integrity verification failed")
            return bytearray(clear)
        finally:
            _zero(machine_key)
            _zero(user_key)
            _zero(master_key)


def preflight(windows_root: Path) -> tuple[int, int]:
    """Verify machine DPAPI prerequisites without a Goodix blob or secret output."""
    bindings = _load_impacket()
    system_hive = windows_root / "Windows/System32/config/SYSTEM"
    security_hive = windows_root / "Windows/System32/config/SECURITY"
    protect_root = windows_root / "Windows/System32/Microsoft/Protect/S-1-5-18"
    with _read_only_impacket_registry(bindings):
        machine_key, user_key = _load_dpapi_system_keys(system_hive, security_hive, bindings)
        examined = 0
        decryptable = 0
        try:
            for directory in (protect_root, protect_root / "User"):
                if not directory.is_dir():
                    continue
                for candidate in directory.iterdir():
                    try:
                        uuid.UUID(candidate.name)
                    except ValueError:
                        continue
                    if not candidate.is_file():
                        continue
                    examined += 1
                    clear = bytearray()
                    try:
                        key = machine_key if directory == protect_root else user_key
                        clear = _decrypt_masterkey(candidate, (key,), bindings)
                    except RecoveryError:
                        continue
                    else:
                        decryptable += 1
                    finally:
                        _zero(clear)
        finally:
            _zero(machine_key)
            _zero(user_key)
    if examined == 0 or decryptable == 0:
        raise RecoveryError("no usable LocalSystem DPAPI master keys were found")
    return examined, decryptable


def write_secret_hex(output: Path, psk: bytes) -> None:
    """Create a non-overwriting, mode-0600 driver-compatible hex key file."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(output, flags, 0o600)
    encoded = bytearray(psk.hex().encode("ascii") + b"\n")
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RecoveryError("short write while creating secret output")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
        _zero(encoded)


def _zero(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline, non-printing recovery of the Goodix 550c Windows DPAPI PSK"
    )
    parser.add_argument(
        "--windows-root",
        default=str(DEFAULT_WINDOWS_ROOT),
        help="repository-local read-only bind mount of the offline Windows volume",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="verify machine DPAPI prerequisites without reading a Goodix blob",
    )
    parser.add_argument(
        "--blob",
        default=str(DEFAULT_BLOB),
        help="repository-local raw 324-byte blob or complete Goodix E4 response",
    )
    parser.add_argument(
        "--blob-format",
        choices=("auto", "raw", "hex", "e4-frame"),
        default="auto",
    )
    parser.add_argument(
        "--expected-hash",
        default=str(DEFAULT_HASH),
        help="repository-local raw/hex/JSON SHA-256 oracle from slot 0xbb020001",
    )
    parser.add_argument(
        "--output",
        help="optional new hex file directly below ignored research/secrets",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    psk = bytearray()
    try:
        windows_root = project_input_path(args.windows_root, label="Windows root")
        if not windows_root.is_dir():
            raise RecoveryError("Windows root is not a directory")
        require_read_only_filesystem(windows_root)

        if args.preflight:
            if args.output:
                raise RecoveryError("preflight does not accept a secret output")
            examined, decryptable = preflight(windows_root)
            print(
                "Preflight passed: "
                f"{decryptable}/{examined} LocalSystem DPAPI master-key files decrypt; "
                "no secrets were printed or stored."
            )
            return 0

        blob_path = project_input_path(args.blob, label="Goodix DPAPI blob")
        hash_path = project_input_path(args.expected_hash, label="expected live-PSK hash")
        blob_data = extract_goodix_dpapi_blob(blob_path.read_bytes(), args.blob_format)
        expected_hash = expected_hash_from_data(hash_path.read_bytes())
        psk = recover_psk(windows_root, blob_data)
        verify_live_psk(psk, expected_hash)

        if args.output:
            output = secret_output_path(args.output)
            write_secret_hex(output, psk)
            print(
                "Recovery passed: DPAPI integrity and the live sensor hash oracle match; "
                "a mode-0600 repository-local secret file was created."
            )
        else:
            print(
                "Recovery passed: DPAPI integrity and the live sensor hash oracle match; "
                "the PSK was not printed or stored."
            )
        return 0
    except RecoveryError as exc:
        print(f"Recovery refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # fail closed without rendering secret-bearing values
        print(
            f"Recovery failed safely ({type(exc).__name__}); no key was printed.",
            file=sys.stderr,
        )
        return 1
    finally:
        _zero(psk)


if __name__ == "__main__":
    raise SystemExit(main())
