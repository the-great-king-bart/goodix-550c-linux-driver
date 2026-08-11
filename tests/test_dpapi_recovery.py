import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/recover_windows_dpapi_psk.py"
SPEC = importlib.util.spec_from_file_location("recover_windows_dpapi_psk", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def _response_frame(slot: int, blob: bytes, *, status: int = 0) -> bytes:
    payload = bytes([status]) + slot.to_bytes(4, "little") + len(blob).to_bytes(4, "little") + blob
    body = b"\xe4" + (len(payload) + 1).to_bytes(2, "little") + payload
    inner = body + bytes([(0xAA - sum(body)) & 0xFF])
    header = b"\xa0" + len(inner).to_bytes(2, "little")
    return header + bytes([sum(header) & 0xFF]) + inner


def test_exact_dpapi_slot_read_payload_uses_bb010002():
    assert recovery.goodix_dpapi_read_payload().hex() == "4401000000000000020001bb00000000"


def test_extracts_raw_324_byte_blob():
    blob = bytes(index % 256 for index in range(324))
    assert recovery.extract_goodix_dpapi_blob(blob) == blob


def test_extracts_exact_e4_bb010002_response_and_zero_padding():
    blob = bytes(index % 256 for index in range(324))
    frame = _response_frame(recovery.GOODIX_DPAPI_SLOT, blob) + b"\x00" * 43
    assert recovery.extract_goodix_dpapi_blob(frame, "e4-frame") == blob


def test_rejects_bb020002_task_typo():
    blob = b"A" * 324
    frame = _response_frame(0xBB020002, blob)
    with pytest.raises(recovery.RecoveryError, match="0xbb010002"):
        recovery.extract_goodix_dpapi_blob(frame, "e4-frame")


def test_rejects_failed_or_wrong_length_e4_response():
    with pytest.raises(recovery.RecoveryError, match="reports failure"):
        recovery.extract_goodix_dpapi_blob(
            _response_frame(recovery.GOODIX_DPAPI_SLOT, b"A" * 324, status=1),
            "e4-frame",
        )
    with pytest.raises(recovery.RecoveryError, match="not 324"):
        recovery.extract_goodix_dpapi_blob(
            _response_frame(recovery.GOODIX_DPAPI_SLOT, b"A" * 323),
            "e4-frame",
        )


def test_expected_hash_json_and_live_psk_verification():
    psk = bytes(range(32))
    digest = recovery.hashlib.sha256(psk).hexdigest()
    parsed = recovery.expected_hash_from_data(f'{{"psk_hash_hex":"{digest}"}}'.encode())
    recovery.verify_live_psk(psk, parsed)


def test_live_psk_hash_mismatch_fails_closed():
    with pytest.raises(recovery.RecoveryError, match="does not match"):
        recovery.verify_live_psk(b"A" * 32, b"B" * 32)


def test_masterkey_namespace_selects_only_corresponding_system_key():
    class FakePath:
        def __init__(self, value):
            self.value = value

        def __truediv__(self, child):
            return FakePath(f"{self.value}/{child}")

        def is_file(self):
            return self.value.endswith("/User/master-guid")

    path, key_kind = recovery._masterkey_path(FakePath("protect"), "master-guid")
    assert path.value == "protect/User/master-guid"
    assert key_kind == "user"


def test_secret_output_is_limited_to_ignored_secret_root():
    destination = recovery.secret_output_path("research/secrets/test-only-new-name.psk")
    assert destination.parent == recovery.SECRET_ROOT.resolve()
    with pytest.raises(recovery.RecoveryError, match="research/secrets"):
        recovery.secret_output_path("research/artifacts/not-allowed.psk")
