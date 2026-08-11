from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH_DIR = ROOT / "patches" / "goodix-550c"
DRIVER_PATCH = PATCH_DIR / "0001-goodix53x5-add-fail-closed-550c-policy.patch"
INTEGRATION_PATCH = PATCH_DIR / "0002-libfprint-v1.94.10-integration.patch"
HARDENING_PATCH = PATCH_DIR / "0003-goodix53x5-harden-secret-loading.patch"
BUILD_SCRIPT = ROOT / "scripts" / "build_goodix550c_libfprint.sh"
RUN_SCRIPT = ROOT / "scripts" / "run_goodix550c_open_close.sh"


def added_lines(path: Path) -> str:
    return "\n".join(
        line[1:]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def test_driver_patch_has_irreversible_mutation_barrier():
    added = added_lines(DRIVER_PATCH)

    for pair in (
        "category == 0xA && command == 0x2",
        "category == 0xE && command == 0x0",
        "category == 0xE && command == 0x1",
        "category == 0xF && command == 0x0",
        "category == 0xF && command == 0x2",
    ):
        assert pair in added
    assert "goodix_550c_command_is_persistent_mutation (category, command)" in added
    assert "goodix53x5-firmware550c.h" not in added


def test_driver_patch_requires_external_secret_and_cleans_temporaries():
    added = added_lines(DRIVER_PATCH)
    patch = DRIVER_PATCH.read_text(encoding="utf-8")

    assert 'g_getenv ("GOODIX550C_PSK")' in patch
    assert 'g_getenv ("GOODIX550C_PSK_FILE")' in added
    assert "Set exactly one of GOODIX550C_PSK" in added
    assert "g_path_is_absolute (path)" in added
    assert "st.st_uid != geteuid ()" in added
    assert "(st.st_mode & 0777) != 0600" in added
    assert "OPENSSL_cleanse (hex, hex_alloc_len)" in added
    assert "OPENSSL_cleanse (psk, sizeof (psk))" in added
    assert "/etc/goodix550c.psk" not in added
    assert "memcpy (out, goodix_psk" not in added


def test_hardening_patch_uses_race_free_file_open_and_fixed_tls_identity():
    added = added_lines(HARDENING_PATCH)

    assert "O_RDONLY | O_CLOEXEC | O_NOFOLLOW" in added
    assert "fstat (fd, &st)" in added
    assert "st.st_nlink != 1" in added
    assert 'GOODIX_TLS_PSK_IDENTITY "Client_identity"' in added
    assert "strcmp (identity, GOODIX_TLS_PSK_IDENTITY)" in added
    assert "OPENSSL_cleanse (tls->psk, sizeof (tls->psk))" in added


def test_driver_patch_gates_exact_firmware_cipher_and_volatile_init():
    added = added_lines(DRIVER_PATCH)

    assert 'GOODIX_550C_EXPECTED_FW "GF5288_GM168SEC_APP_13021"' in added
    assert 'SSL_CTX_set_cipher_list (tls->ctx, "PSK-AES128-CBC-SHA256")' in added
    assert "#include <set>" in added
    assert "#define GOODIX550C_ENABLE_VOLATILE_INIT 0" in added
    assert 'g_getenv ("GOODIX550C_ALLOW_VOLATILE_INIT"), "1"' in added
    assert "G_USB_DEVICE_CLAIM_INTERFACE_BIND_KERNEL_DRIVER" not in added


def test_libfprint_patch_is_default_off_and_excludes_firmware_loader():
    added = added_lines(INTEGRATION_PATCH)

    assert "option('goodix550c_volatile_init'" in added
    assert "value: false" in added
    assert "-DGOODIX550C_ENABLE_VOLATILE_INIT=1" in added
    assert "goodix53x5-tls.c" in added
    assert "goodix53x5-enroll.c" in added
    assert "goodix53x5-auth.c" in added
    assert "goodix53x5-match.c" in added
    assert "goodix53x5-firmware550c" not in added


def test_build_pipeline_emits_minimal_harness_with_local_runpath():
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "0003-goodix53x5-harden-secret-loading.patch" in script
    assert '"$BUILD_DIR/test-goodix550c-tls"' in script
    assert '"$PROJECT_ROOT/tools/goodix550c_open_close.c"' in script
    assert "'-Wl,-rpath,$ORIGIN/libfprint'" in script
    assert '-o "$BUILD_DIR/goodix550c-open-close"' in script


def test_live_wrapper_refuses_holders_drivers_services_and_external_paths():
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert '"$PROJECT_ROOT"/build/*' in script
    assert '"$PROJECT_ROOT"/research/secrets/*' in script
    assert '[[ "$fprintd_state" != inactive ]]' in script
    assert '[[ -L "$interface/driver" ]]' in script
    assert 'fuser -s "$usb_node"' in script
    assert "-u GOODIX550C_PSK" in script


def test_prepare_only_pipeline_applies_all_patches_without_hardware():
    driver_repo = ROOT / "research" / "upstream" / "goodix-550c-driver"
    libfprint_repo = ROOT / "research" / "upstream" / "libfprint"
    if not (driver_repo / ".git").exists() or not (libfprint_repo / ".git").exists():
        pytest.skip("ignored pinned research clones are not present")
    if shutil.which("patch") is None:
        pytest.skip("patch command is unavailable")

    build_root = ROOT / "build"
    build_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pytest-goodix550c-", dir=build_root) as stage:
        result = subprocess.run(
            [
                str(ROOT / "scripts" / "build_goodix550c_libfprint.sh"),
                "--prepare-only",
                "--stage-dir",
                stage,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Goodix 550c fail-closed policy audit passed" in result.stdout
    assert "Prepared fail-closed source tree" in result.stdout
    assert "meson setup" not in result.stdout
