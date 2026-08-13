from __future__ import annotations

import importlib.util
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
FDT_13021_PATCH = PATCH_DIR / "0004-goodix550c-use-13021-fdt-down-layout.patch"
MANUAL_FDT_PATCH = PATCH_DIR / "0005-goodix550c-add-guarded-manual-fdt-poll.patch"
DRIVER_SERIES = PATCH_DIR / "driver-series"
BUILD_SCRIPT = ROOT / "scripts" / "build_goodix550c_libfprint.sh"
RUN_SCRIPT = ROOT / "scripts" / "run_goodix550c_open_close.sh"
NATIVE_FDT_TEST = ROOT / "tests" / "native" / "test_goodix550c_fdt_down.c"
NATIVE_FDT_SHIM = ROOT / "tests" / "native" / "goodix550c_fdt_down_test_shim.h"
NATIVE_MANUAL_FDT_TEST = ROOT / "tests" / "native" / "test_goodix550c_manual_fdt.c"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_goodix550c_patch.py"


def _load_verifier():
    """Import the shipped audit so its option rule is what the tests exercise."""
    spec = importlib.util.spec_from_file_location("goodix550c_patch_verifier", VERIFY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = _load_verifier()


def test_default_off_option_rule_rejects_an_option_that_defaults_on():
    """A per-option rule, not a substring test that any other option satisfies."""
    options = (
        "option('goodix550c_volatile_init',\n"
        "       description: 'gate one (runtime opt-in required)',\n"
        "       type: 'boolean',\n"
        "       value: false)\n"
        "option('goodix550c_manual_fdt_poll',\n"
        "       description: 'gate two (runtime opt-in required)',\n"
        "       type: 'boolean',\n"
        "       value: true)\n"
    )

    assert VERIFIER.meson_option_is_default_off(options, "goodix550c_volatile_init")
    assert not VERIFIER.meson_option_is_default_off(options, "goodix550c_manual_fdt_poll")
    assert not VERIFIER.meson_option_is_default_off(options, "goodix550c_absent")


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


def test_13021_fdt_patch_uses_direct_dynamic_down_base_only():
    added = added_lines(FDT_13021_PATCH)

    assert "if (self->variant == GOODIX_VARIANT_TLS_PSK)" in added
    assert "GOODIX_PROTO_CMD_FDT_DOWN, fdt_base," in added
    assert "GOODIX_FDT_BASE_LEN, FALSE" in added
    assert "FDT down ACK validated; event read posted" in added
    assert "GOODIX_PROTO_CMD_FDT_UP, fdt_base," not in added
    assert "goodix_cmd_fdt_manual" not in added


def test_13021_fdt_down_wire_vector_matches_exact_yoga_capture():
    threshold = bytes.fromhex("9797a2a2a0a094949797a3a3a1a1989893939f9f9c9c9393")
    command = 0x32
    body = bytes([command]) + (len(threshold) + 1).to_bytes(2, "little") + threshold
    inner = body + bytes([(0xAA - sum(body)) & 0xFF])
    header = b"\xa0" + len(inner).to_bytes(2, "little")
    frame = header + bytes([sum(header) & 0xFF]) + inner

    assert frame.hex() == (
        "a01c00bc3219009797a2a2a0a094949797a3a3a1a1989893939f9f9c9c9393dd"
    )


def test_manual_fdt_fallback_is_dual_gated_debounced_and_non_logging():
    added = added_lines(MANUAL_FDT_PATCH)
    patch = MANUAL_FDT_PATCH.read_text(encoding="utf-8")

    assert "#define GOODIX550C_ENABLE_MANUAL_FDT_POLL 0" in added
    assert 'g_getenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL")' in added
    assert "GOODIX_MANUAL_FDT_CONFIRMATIONS 2" in added
    assert "GOODIX_MANUAL_FDT_POLL_DELAY_MS 100" in added
    assert "self->variant == GOODIX_VARIANT_TLS_PSK" in added
    assert "goodix_550c_manual_fdt_poll_allowed ()" in added
    assert "goodix_cmd_fdt_manual (ssm, dev, TRUE" in added
    assert "down = changed_pairs > 0" in added
    assert "state->consecutive >= GOODIX_MANUAL_FDT_CONFIRMATIONS" in added
    assert "touch_flag == 0" not in added
    assert "Remove finger before" not in added
    assert "GOODIX_FINGER_UP_SLEEP" in added
    assert "GOODIX_REF_CAPTURE_REG_OFF_DONE" in patch
    assert "state->provisional_baseline" in added
    assert "self->calib.delta_fdt" in added
    assert "goodix_run_cmd_cancellable" not in added
    assert "goodix_cmd_fdt_manual_cancellable" not in added
    for sensitive_log in ('touch_flag=0x', 'max_delta=', 'changed_pairs='):
        assert sensitive_log not in added


def test_manual_fdt_settle_phases_are_bounded_and_refuse_suspend():
    added = added_lines(MANUAL_FDT_PATCH)

    # Both phases that wait for a clear pad must give up rather than poll
    # forever; only the finger-down wait is deliberately unbounded.
    assert "GOODIX_MANUAL_FDT_MAX_REFERENCE_POLLS 100" in added
    assert "GOODIX_MANUAL_FDT_MAX_FINGER_UP_POLLS 600" in added
    assert "goodix_manual_fdt_budget_exhausted" in added
    assert "did not settle within %u polls" in added
    assert added.count("goodix_manual_fdt_budget_exhausted (ssm, state") == 2
    for bounded_phase in (
        "GOODIX_MANUAL_FDT_MAX_REFERENCE_POLLS,",
        "GOODIX_MANUAL_FDT_MAX_FINGER_UP_POLLS,",
    ):
        assert bounded_phase in added

    # A poll owns the device without a blocking read, so suspend must refuse
    # instead of reporting a clean suspend it cannot honor.
    assert "gboolean manual_fdt_poll_active;" in added
    assert "self->manual_fdt_poll_active && self->blocking_ssm == NULL" in added
    assert "FP_DEVICE_ERROR_NOT_SUPPORTED" in added
    assert "state->device->manual_fdt_poll_active = FALSE;" in added
    assert "self->manual_fdt_poll_active = TRUE;" in added


def test_manual_fdt_native_policy_covers_boundary_and_malformed_inputs():
    source = NATIVE_MANUAL_FDT_TEST.read_text(encoding="utf-8")

    assert "threshold equality was treated as a changed pair" in source
    assert "odd-length FDT input was accepted" in source
    assert "NULL baseline was accepted" in source
    assert "empty FDT input was accepted" in source
    assert "GOODIX550C_ALLOW_MANUAL_FDT_POLL" in source


def test_driver_patch_series_is_explicit_ordered_and_unique():
    entries = [
        line
        for line in DRIVER_SERIES.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]

    assert entries == [
        DRIVER_PATCH.name,
        HARDENING_PATCH.name,
        FDT_13021_PATCH.name,
        MANUAL_FDT_PATCH.name,
    ]
    assert len(entries) == len(set(entries))


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

    assert 'patches/goodix-550c/driver-series"' in script
    assert '"$BUILD_DIR/test-goodix550c-tls"' in script
    assert '"$PROJECT_ROOT/tools/goodix550c_open_close.c"' in script
    assert "'-Wl,-rpath,$ORIGIN/libfprint'" in script
    assert '-o "$BUILD_DIR/goodix550c-open-close"' in script


def test_build_pipeline_runs_compiled_13021_fdt_down_regression():
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    test_source = NATIVE_FDT_TEST.read_text(encoding="utf-8")
    shim = NATIVE_FDT_SHIM.read_text(encoding="utf-8")

    assert '"$integrated_driver/goodix53x5-commands.c"' in script
    assert '"$integrated_driver/goodix53x5-proto.c"' in script
    assert '"$BUILD_DIR/test-goodix550c-fdt-down"' in script
    assert "goodix_cmd_fdt_down_setup" in test_source
    assert "goodix_proto_build_message" in test_source
    assert "goodix_proto_wrap_pack" in test_source
    assert "expected_frame" in test_source
    assert "#define goodix_run_cmd goodix_test_run_cmd" in shim


def test_build_pipeline_compiles_guarded_manual_fdt_regression():
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--allow-manual-fdt-poll" in script
    assert '-Dgoodix550c_manual_fdt_poll="$MANUAL_FDT_POLL"' in script
    assert "-DGOODIX550C_ENABLE_MANUAL_FDT_POLL=1" in script
    assert '"$PROJECT_ROOT/tests/native/test_goodix550c_manual_fdt.c"' in script
    assert '"$BUILD_DIR/test-goodix550c-manual-fdt"' in script


def test_live_wrapper_refuses_holders_drivers_services_and_external_paths():
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert "--allow-manual-fdt-poll" in script
    assert "GOODIX550C_ALLOW_MANUAL_FDT_POLL=1" in script

    assert '"$PROJECT_ROOT"/build/*' in script
    assert '"$PROJECT_ROOT"/research/secrets/*' in script
    assert '[[ "$fprintd_state" != inactive ]]' in script
    assert '[[ -L "$interface/driver" ]]' in script
    assert 'fuser -s "$usb_node"' in script
    assert "-u GOODIX550C_PSK" in script

    # Open/close reaches no manual-FDT state, so the experimental gate is
    # optional and is exported only when this run acknowledged it.
    assert "if ((MANUAL_FDT_ACK == 1)); then" in script
    assert "MANUAL_FDT_ENV=(GOODIX550C_ALLOW_MANUAL_FDT_POLL=1)" in script
    assert '"${MANUAL_FDT_ENV[@]}"' in script
    assert "-u GOODIX550C_ALLOW_MANUAL_FDT_POLL" in script
    assert "--allow-manual-fdt-poll is required" not in script


def test_prepare_only_pipeline_applies_all_patches_without_hardware():
    driver_repo = ROOT / "research" / "upstream" / "goodix-550c-driver"
    libfprint_repo = ROOT / "research" / "upstream" / "libfprint"
    if not (driver_repo / ".git").exists() or not (libfprint_repo / ".git").exists():
        pytest.skip("run scripts/fetch_upstream_sources.sh before the integration test")
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
