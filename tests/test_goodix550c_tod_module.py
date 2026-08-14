from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FETCH_SCRIPT = ROOT / "scripts" / "fetch_ubuntu_tod_sdk.sh"
FETCH_UPSTREAM_SCRIPT = ROOT / "scripts" / "fetch_upstream_sources.sh"
BUILD_SCRIPT = ROOT / "scripts" / "build_goodix550c_tod_module.sh"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_goodix550c_tod_module.py"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_goodix550c_tod_fprintd.sh"
INSTALL_SCRIPT = ROOT / "scripts" / "goodix550c_install_system.sh"
LIVE_RUNNER = ROOT / "scripts" / "run_goodix550c_tod_open_close.sh"
TOD_ENTRY = ROOT / "tod" / "goodix550c-tod-entry.c"
TOD_SYMBOL_MAP = ROOT / "tod" / "goodix550c-tod.map"
TOD_MESON = ROOT / "tod" / "meson.build"
TOD_OPTIONS = ROOT / "tod" / "meson_options.txt"
BUS_CONFIG = ROOT / "tod" / "private-bus.conf"
TOD_HARNESS = ROOT / "tools" / "goodix550c_tod_open_close.c"
TOD_ENROLL_HARNESS = ROOT / "tools" / "goodix550c_tod_enroll.c"
TOD_VERIFY_HARNESS = ROOT / "tools" / "goodix550c_tod_verify.c"


def _load_verifier():
    """Import the shipped audit so its option rule is what the tests exercise."""
    spec = importlib.util.spec_from_file_location(
        "goodix550c_tod_verifier", VERIFY_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = _load_verifier()


def test_sdk_fetcher_pins_exact_ubuntu_packages_and_hashes():
    script = FETCH_SCRIPT.read_text(encoding="utf-8")
    upstream = FETCH_UPSTREAM_SCRIPT.read_text(encoding="utf-8")

    assert "1:1.95.1+tod1-0ubuntu2" in script
    assert "https://archive.ubuntu.com/ubuntu/pool/main/libf/libfprint" in script
    assert "db01d591d13f81312d618d2e247ceed66d72ee42cd07130b19637425df32ee28" in script
    assert "d1f99081a0d314b7416bdc9d5d70bea47237787dea946a95d8de3e21bb0b8b91" in script
    assert "--offline" in script
    assert '"$PROJECT_ROOT"/build/*|"$PROJECT_ROOT"/research/*' in script
    assert "dpkg-deb --extract" in script
    assert "https://github.com/brianporeilly/goodix-550c-driver.git" in upstream
    assert "https://gitlab.freedesktop.org/libfprint/libfprint.git" in upstream
    assert "a3de5a1b6174ace5db0bb2a8796c5be6e55428f0" in upstream
    assert "0c97a47d8ef405cd577b87058c1e89cae9d242e7" in upstream
    assert "refs/tags/v1.94.10" in upstream
    assert "--depth=1" in upstream
    assert "--offline" in upstream


def test_tod_target_has_one_exported_registration_contract():
    entry = TOD_ENTRY.read_text(encoding="utf-8")
    meson = TOD_MESON.read_text(encoding="utf-8")
    options = TOD_OPTIONS.read_text(encoding="utf-8")
    symbol_map = TOD_SYMBOL_MAP.read_text(encoding="utf-8")

    assert "fpi_tod_shared_driver_get_type (void)" in entry
    assert "return fpi_device_goodix53x5_get_type ();" in entry
    assert "G_MODULE_EXPORT GType" in entry
    assert "shared_module(" in meson
    assert "'goodix550c'" in meson
    assert "gnu_symbol_visibility: 'hidden'" in meson
    assert "'-Wl,-z,defs'" in meson
    assert "--version-script=" in meson
    assert "fpi_tod_shared_driver_get_type;" in symbol_map
    assert "local:" in symbol_map and "*;" in symbol_map
    assert "tod_library" in meson and "core_library" in meson
    assert "install: false" in meson
    assert VERIFIER.meson_option_is_default_off(options, "goodix550c_volatile_init")
    assert VERIFIER.meson_option_is_default_off(options, "goodix550c_manual_fdt_poll")
    assert "-DGOODIX550C_ENABLE_MANUAL_FDT_POLL=1" in meson


def test_default_off_option_rule_rejects_an_option_that_defaults_on():
    """A per-option rule, not a substring test that any other option satisfies."""
    options = (
        "option(\n  'goodix550c_volatile_init',\n  type: 'boolean',\n"
        "  value: false,\n  description: 'gate one (runtime opt-in required)',\n)\n"
        "option(\n  'goodix550c_manual_fdt_poll',\n  type: 'boolean',\n"
        "  value: true,\n  description: 'gate two (runtime opt-in required)',\n)\n"
    )

    assert VERIFIER.meson_option_is_default_off(options, "goodix550c_volatile_init")
    assert not VERIFIER.meson_option_is_default_off(
        options, "goodix550c_manual_fdt_poll"
    )
    assert not VERIFIER.meson_option_is_default_off(options, "goodix550c_absent")


def test_build_and_verifier_preserve_fail_closed_and_abi_gates():
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    verifier = VERIFY_SCRIPT.read_text(encoding="utf-8")

    assert "driver-series" in build
    assert "goodix550c-sources.txt" in build
    assert '-Dgoodix550c_volatile_init="$VOLATILE_INIT"' in build
    assert '-Dgoodix550c_manual_fdt_poll="$MANUAL_FDT_POLL"' in build
    assert '--expected-manual-fdt-poll "$MANUAL_FDT_POLL"' in build
    assert '"$PROJECT_ROOT/tests/native/test_goodix550c_manual_fdt.c"' in build
    assert '"$BUILD_DIR/test-goodix550c-manual-fdt"' in build
    assert '--build-dir "$BUILD_DIR"' in build
    assert '"$PROJECT_ROOT/tools/goodix550c_tod_open_close.c"' in build
    assert '"$CORE_LIBRARY" "$TOD_LIBRARY"' in build
    assert "-D_FORTIFY_SOURCE=3" in build
    assert "-fstack-protector-strong" in build
    assert "-Wl,-z,relro,-z,now,-z,noexecstack" in build
    assert '--harness "$HARNESS"' in build
    assert "harness_sha256=" in build
    assert "source-checksums.sha256" in build
    assert "source_checksums_sha256=" in build
    assert "meson install" not in build
    assert "systemctl" not in build
    assert "fpi_tod_shared_driver_get_type" in verifier
    assert "libfprint-2.so.2" in verifier
    assert "libfprint-2-tod.so.1" in verifier
    assert "RPATH" in verifier and "RUNPATH" in verifier
    assert "GOODIX_PROTO_CMD_FDT_DOWN" in verifier
    assert "GOODIX_FDT_BASE_LEN" in verifier
    assert "FDT down ACK validated; event read posted" in verifier
    assert "GOODIX550C_ENABLE_MANUAL_FDT_POLL" in verifier
    assert "GOODIX550C_ALLOW_MANUAL_FDT_POLL" in verifier
    assert "Manual FDT finger-down poll armed" in verifier
    assert "--expected-manual-fdt-poll" in verifier


def test_tod_physical_runner_and_harness_are_explicit_and_fail_closed():
    runner = LIVE_RUNNER.read_text(encoding="utf-8")
    harness = TOD_HARNESS.read_text(encoding="utf-8")

    for marker in (
        "--allow-volatile-init",
        "--allow-manual-fdt-poll",
        "--expected-psk-hash",
        "source_checksums_sha256 || true)",
        'sha256sum --check --strict "$SOURCE_CHECKSUMS"',
        "expected_firmware || true)",
        "expected_usb_id || true)",
        "hmac.compare_digest(hashlib.sha256(psk).digest(), expected)",
        '[[ "$fprintd_state" != inactive ]]',
        '[[ -L "$interface/driver" ]]',
        'fuser -s "$usb_node"',
        "FP_TOD_DRIVERS_DIR=\"$BUILD_DIR\"",
        "FP_DRIVERS_ALLOWLIST=goodix53x5",
        "GOODIX550C_ALLOW_VOLATILE_INIT=1",
        "GOODIX550C_ALLOW_MANUAL_FDT_POLL=1",
        "GOODIX550C_PSK_FILE=\"$PSK_FILE\"",
        'HOME="$LIVE_RUN_DIR/home"',
        'TMPDIR="$LIVE_RUN_DIR/tmp"',
        'XDG_RUNTIME_DIR="$LIVE_RUN_DIR/runtime"',
        "env -i",
    ):
        assert marker in runner
    assert "systemctl stop" not in runner
    assert "meson install" not in runner
    assert "kill " not in runner

    # The open/close harness reaches no manual-FDT state, so the experimental
    # gate is optional here and is exported only when this run acknowledged it.
    assert "if ((MANUAL_FDT_ACK == 1)); then" in runner
    assert "MANUAL_FDT_ENV=(GOODIX550C_ALLOW_MANUAL_FDT_POLL=1)" in runner
    assert '"${MANUAL_FDT_ENV[@]}"' in runner
    assert "--allow-manual-fdt-poll is required" not in runner

    # The per-run private HOME/TMP/XDG tree is removed on every exit path.
    assert "trap cleanup_live_run_dir EXIT" in runner
    assert 'rm -rf -- "$LIVE_RUN_DIR"' in runner
    assert '"$PROJECT_ROOT"/build/goodix550c-tod-run.??????)' in runner
    assert "exec timeout" not in runner
    assert 'exit "$HARNESS_STATUS"' in runner

    for marker in (
        '#define EXPECTED_DRIVER "goodix53x5"',
        '#define EXPECTED_NAME "Goodix HTK32 Fingerprint Sensor"',
        'g_getenv ("GOODIX550C_PSK") == NULL',
        'g_getenv ("LD_LIBRARY_PATH") == NULL',
        "devices->len != 1",
        "fp_device_get_scan_type (device) != FP_SCAN_TYPE_PRESS",
        "fp_device_open_sync (device, NULL, &error)",
        "fp_device_close_sync (device, NULL, &error)",
        # Absent is allowed; an unrecognized value is not the guarded profile.
        'g_getenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL")',
        'g_strcmp0 (manual_fdt_gate, "1") == 0',
    ):
        assert marker in harness

    result = subprocess.run(
        [str(LIVE_RUNNER), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert "--allow-volatile-init" in result.stdout


def test_enroll_harness_cues_the_lift_on_time_and_exits_beside_live_threads():
    harness = TOD_ENROLL_HARNESS.read_text(encoding="utf-8")

    # The finger stays PRESENT for the whole bounded finger-up wait, so a
    # status-driven prompt reaches the operator only after that wait has
    # already succeeded or expired. Both stage outcomes must cue the lift, and
    # the stage callback runs before that wait begins.
    assert harness.count(">>> LIFT your finger off the sensor.") == 2
    assert 'puts (">>> LIFT' not in harness
    assert ">>> Finger detected" not in harness
    assert "Finger detected; hold until the stage is reported." in harness

    # OpenCV worker threads from the module outlive the harness and cannot be
    # joined, so process exit must not run global destructors beside them.
    assert "_exit (enrolled_ok ? 0 : 1)" in harness
    assert "return enrolled_ok ? 0 : 1;" not in harness
    assert "#include <unistd.h>" in harness

    # The template is still never persisted.
    for forbidden in ("fp_print_serialize", "fp_print_to_file", "g_file_set_contents", "fopen"):
        assert forbidden not in harness


def test_verify_harness_tests_a_rejection_and_never_stores_a_template():
    harness = TOD_VERIFY_HARNESS.read_text(encoding="utf-8")
    runner = LIVE_RUNNER.read_text(encoding="utf-8")

    # A positive-only test would pass against a driver that matched everything.
    assert "#define VERIFY_DIFFERENT_FINGER_TRIALS 1" in harness
    assert "expect no match" in harness
    assert "expect a match" in harness
    assert "observed_matches == expected_matches" in harness
    assert "verdicts == VERIFY_TRIALS" in harness

    # The template and every scanned print stay in process memory.
    for forbidden in ("fp_print_serialize", "fp_print_to_file", "g_file_set_contents", "fopen"):
        assert forbidden not in harness
    assert "g_autoptr(FpPrint) scanned = NULL;" in harness

    # A retry is the device asking for another placement, not a verdict.
    assert "#define VERIFY_MAX_RETRIES" in harness
    assert "error->domain == FP_DEVICE_RETRY" in harness

    # The lift cue must come from the match callback, which runs before the
    # bounded finger-up wait. Cueing it from the return path arrives after the
    # wait it is meant to satisfy, and a shutdown that aborts on a spent budget
    # leaves the sensor unable to read the next frame.
    # Three cues: one per enrollment stage outcome, plus the match callback.
    assert harness.count(">>> LIFT your finger off the sensor.") == 3
    assert "on_match_reported" in harness
    assert "fp_device_verify_sync (device, enrolled, NULL, on_match_reported, NULL," in harness

    # Same fail-closed profile as the other harnesses.
    assert 'g_strcmp0 (manual_fdt_gate, "1") == 0' in harness
    assert "fp_device_has_feature (device, FP_DEVICE_FEATURE_VERIFY)" in harness
    assert "_exit (verified_ok ? 0 : 1)" in harness

    # The wrapper selects it under the same acknowledgement, with its own digest.
    assert "--verify requires --allow-manual-fdt-poll" in runner
    assert "HARNESS_METADATA_KEY=verify_harness_sha256" in runner
    assert "goodix550c-tod-verify" in runner


def test_private_bus_smoke_is_non_activating_and_hides_usb():
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")
    bus_config = BUS_CONFIG.read_text(encoding="utf-8")

    assert "env -i" in script
    assert "--unshare-all" in script
    assert "--dev /dev" in script
    assert "--tmpfs /sys" in script
    assert "--tmpfs /run" in script
    assert '--tmpfs "$PROJECT_ROOT"' in script
    assert '--bind "$RUN_DIR" "$SANDBOX_RUN_DIR"' in script
    assert '--bind "$PROJECT_ROOT" "$PROJECT_ROOT"' not in script
    assert "test ! -e /dev/bus/usb" in script
    assert "test ! -e /sys/bus/usb/devices" in script
    assert 'test ! -e "$1/.env"' in script
    assert 'test ! -e "$1/research/secrets"' in script
    assert "STATE_DIRECTORY" in script
    assert "FP_TOD_DRIVERS_DIR" in script
    assert "FP_DRIVERS_ALLOWLIST goodix53x5" in script
    assert "/usr/libexec/fprintd --no-timeout" in script
    assert "BUS_PID=$!" in script and "SANDBOX_PID=$!" in script
    assert 'stop_recorded_pid "$SANDBOX_PID" "$SANDBOX_START" sandbox' in script
    assert 'stop_recorded_pid "$BUS_PID" "$BUS_START" bus' in script
    assert "recorded_child_is_live" in script
    assert '"${stat_fields[19]}" == "$expected_start"' in script
    assert 'done < <(jobs -pr)' in script
    assert 'realpath -e "/proc/$pid/exe"' in script
    assert "systemctl" not in script
    assert "GOODIX550C_PSK" not in script
    assert "<servicedir>" not in bus_config
    assert "<standard_session_servicedirs/>" not in bus_config


def test_the_driver_declines_openssl_s_process_wide_atexit_handler():
    patch = (
        ROOT
        / "patches"
        / "goodix-550c"
        / "0014-goodix550c-stop-openssl-installing-an-atexit-handler.patch"
    ).read_text(encoding="utf-8")
    series = (ROOT / "patches" / "goodix-550c" / "driver-series").read_text(encoding="utf-8")

    assert "0014-goodix550c-stop-openssl-installing-an-atexit-handler.patch" in series

    # The flag has to reach OpenSSL before anything initializes it implicitly,
    # so the call belongs on the path every TLS session already runs through.
    assert "+  OPENSSL_init_ssl (OPENSSL_INIT_NO_ATEXIT, NULL);" in patch
    assert "+  goodix_tls_ensure_openssl ();" in patch
    assert "goodix53x5-tls.c" in patch

    # The audit refuses a build whose TLS engine lost the flag again.
    assert VERIFIER.__file__
    verifier = Path(VERIFIER.__file__).read_text(encoding="utf-8")
    assert "OPENSSL_init_ssl (OPENSSL_INIT_NO_ATEXIT, NULL)" in verifier
    assert "process-wide atexit handler" in verifier


def test_system_installer_stages_the_login_factor_and_stays_reversible():
    script = INSTALL_SCRIPT.read_text(encoding="utf-8")

    # Installing the driver and making it an authentication factor are separate
    # actions, so the driver can be proven against the host service first.
    assert "--install-driver" in script
    assert "--enable-login" in script
    assert "--disable-login" in script
    assert "--uninstall" in script

    # Every stage validates the same live profile the private harness does; a
    # module built for another firmware or libfprint must not reach the host.
    assert "a3de5a1b6174ace5db0bb2a8796c5be6e55428f0" in script
    assert "GF5288_GM168SEC_APP_13021" in script
    assert "1:1.95.1+tod1-0ubuntu2" in script
    assert "sha256sum --check --strict --quiet" in script
    assert "module digest does not match its build metadata" in script

    # Both runtime gates stay opt-in, exactly as they are for the private run.
    assert "--allow-volatile-init" in script
    assert "--allow-manual-fdt-poll" in script
    assert "GOODIX550C_ALLOW_VOLATILE_INIT=1" in script
    assert "GOODIX550C_ALLOW_MANUAL_FDT_POLL=1" in script
    assert "FP_DRIVERS_ALLOWLIST=goodix53x5" in script

    # The secret is root-only on disk and never enters the unit file itself.
    assert "install -m 0600 -o root -g root" in script
    assert "install -d -m 0700 -o root -g root" in script
    assert "Environment=GOODIX550C_PSK_FILE=$INSTALLED_PSK" in script

    # Password fallback is the property that keeps a sensor fault from locking
    # the account out, so it is checked rather than assumed.
    assert "default=ignore" in script
    assert "could make a sensor fault block login entirely" in script
    assert "--yes-enable-login" in script

    # Enrolled templates are never destroyed as a side effect of any stage.
    assert "--replace-prints" in script
    assert "--purge-prints" in script
    assert "Left templates in" in script


def test_full_default_off_tod_build_offline_when_inputs_are_present():
    driver_repo = ROOT / "research" / "upstream" / "goodix-550c-driver"
    sdk_downloads = ROOT / "build" / "ubuntu-libfprint-tod-sdk" / "downloads"
    required = (
        sdk_downloads / "libfprint-2-dev_1.95.1+tod1-0ubuntu2_amd64.deb",
        sdk_downloads / "libfprint-2-tod-dev_1.95.1+tod1-0ubuntu2_amd64.deb",
    )
    commands = ("cc", "meson", "ninja", "patch", "readelf", "nm")
    if not (driver_repo / ".git").exists() or not all(path.is_file() for path in required):
        pytest.skip(
            "run scripts/fetch_upstream_sources.sh and "
            "scripts/fetch_ubuntu_tod_sdk.sh before the native integration test"
        )
    if any(shutil.which(command) is None for command in commands):
        pytest.skip("native TOD build toolchain is unavailable")

    build_root = ROOT / "build"
    build_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pytest-goodix550c-tod-", dir=build_root) as stage:
        result = subprocess.run(
            [
                str(BUILD_SCRIPT),
                "--offline-sdk",
                "--stage-dir",
                stage,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            # A full Meson configure plus Ninja build of the driver and
            # sigfm.cpp against OpenCV, then three cc runs and two audits.
            timeout=900,
        )
        module = Path(stage) / "builddir" / "libgoodix550c.so"
        harness = Path(stage) / "builddir" / "goodix550c-tod-open-close"
        metadata = Path(stage) / "build-metadata.txt"
        source_checksums = Path(stage) / "source-checksums.sha256"
        assert module.is_file(), result.stdout + result.stderr
        assert harness.is_file(), result.stdout + result.stderr
        assert harness.stat().st_mode & 0o111, result.stdout + result.stderr
        assert metadata.is_file(), result.stdout + result.stderr
        assert source_checksums.is_file(), result.stdout + result.stderr
        metadata_text = metadata.read_text(encoding="utf-8")
        assert "expected_usb_id=27c6:550c" in metadata_text
        assert "expected_firmware=GF5288_GM168SEC_APP_13021" in metadata_text
        assert "volatile_init=false" in metadata_text
        assert "manual_fdt_poll=false" in metadata_text
        assert "harness_sha256=" in metadata_text
        assert "source_checksums_sha256=" in metadata_text
        assert str(ROOT) not in metadata_text
        assert str(ROOT) not in source_checksums.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Goodix 550c TOD source and ABI audit passed" in result.stdout
    assert "Goodix TLS offline policy test passed" in result.stdout
    assert "Built installed-runtime harness" in result.stdout
