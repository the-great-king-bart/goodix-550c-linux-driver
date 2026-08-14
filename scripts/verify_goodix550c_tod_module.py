#!/usr/bin/env python3
"""Static source-policy and ELF ABI audit for the Goodix 550c TOD module."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = ROOT / "tod" / "goodix550c-sources.txt"
TOD_ENTRY = ROOT / "tod" / "goodix550c-tod-entry.c"
TOD_SYMBOL_MAP = ROOT / "tod" / "goodix550c-tod.map"
TOD_MESON = ROOT / "tod" / "meson.build"
TOD_OPTIONS = ROOT / "tod" / "meson_options.txt"
TOD_HARNESS = ROOT / "tools" / "goodix550c_tod_open_close.c"
TOD_ENROLL_HARNESS = ROOT / "tools" / "goodix550c_tod_enroll.c"
TOD_VERIFY_HARNESS = ROOT / "tools" / "goodix550c_tod_verify.c"
TOD_PROBE_HARNESS = ROOT / "tools" / "goodix550c_tod_desync_probe.c"
TOD_RUNNER = ROOT / "scripts" / "run_goodix550c_tod_open_close.sh"
TOD_SMOKE = ROOT / "scripts" / "smoke_goodix550c_tod_fprintd.sh"

EXPECTED_SOURCE_FILES = (
    "drivers/goodix53x5/goodix53x5-auth.c",
    "drivers/goodix53x5/goodix53x5-auth.h",
    "drivers/goodix53x5/goodix53x5-calibration.c",
    "drivers/goodix53x5/goodix53x5-calibration.h",
    "drivers/goodix53x5/goodix53x5-commands.c",
    "drivers/goodix53x5/goodix53x5-commands.h",
    "drivers/goodix53x5/goodix53x5-crypto.c",
    "drivers/goodix53x5/goodix53x5-crypto.h",
    "drivers/goodix53x5/goodix53x5-enroll.c",
    "drivers/goodix53x5/goodix53x5-enroll.h",
    "drivers/goodix53x5/goodix53x5-image.c",
    "drivers/goodix53x5/goodix53x5-image.h",
    "drivers/goodix53x5/goodix53x5-match.c",
    "drivers/goodix53x5/goodix53x5-match.h",
    "drivers/goodix53x5/goodix53x5-private.h",
    "drivers/goodix53x5/goodix53x5-proto.c",
    "drivers/goodix53x5/goodix53x5-proto.h",
    "drivers/goodix53x5/goodix53x5-safety.h",
    "drivers/goodix53x5/goodix53x5-scan.c",
    "drivers/goodix53x5/goodix53x5-scan.h",
    "drivers/goodix53x5/goodix53x5-session.c",
    "drivers/goodix53x5/goodix53x5-session.h",
    "drivers/goodix53x5/goodix53x5-tls.c",
    "drivers/goodix53x5/goodix53x5-tls.h",
    "drivers/goodix53x5/goodix53x5-transport.c",
    "drivers/goodix53x5/goodix53x5-transport.h",
    "drivers/goodix53x5/goodix53x5.c",
    "drivers/goodix53x5/goodix53x5.h",
    "sigfm/binary.hpp",
    "sigfm/img-info.hpp",
    "sigfm/sigfm.cpp",
    "sigfm/sigfm.hpp",
)

PERSISTENT_HELPERS = (
    "goodix_cmd_production_write",
    "goodix_cmd_mcu_erase_app",
    "goodix_cmd_preset_psk_write_chunk",
    "goodix_cmd_write_firmware",
    "goodix_cmd_check_firmware",
    "goodix_550c_app_firmware_load",
    "goodix_maybe_start_selfheal",
)


def effective_source(text: str) -> str:
    """Remove literal #if 0 regions, including nested preprocessor blocks."""
    output: list[str] = []
    disabled_depth = 0
    for line in text.splitlines():
        if re.match(r"^\s*#\s*if\s+0(?:\s|/|$)", line):
            disabled_depth += 1
            continue
        if disabled_depth and re.match(r"^\s*#\s*(?:if|ifdef|ifndef)\b", line):
            disabled_depth += 1
            continue
        if disabled_depth and re.match(r"^\s*#\s*endif\b", line):
            disabled_depth -= 1
            continue
        if disabled_depth == 0:
            output.append(line)
    if disabled_depth:
        raise ValueError("unbalanced literal #if 0 block")
    return "\n".join(output)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def read_text(path: Path, failures: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(f"cannot read {path}: {error}")
        return ""


def meson_option_is_default_off(options: str, name: str) -> bool:
    """Report whether this exact option block declares ``value: false``.

    The declaration is delimited by the next ``option(`` rather than by a
    closing parenthesis: descriptions in these files contain parentheses. A
    substring test over the whole file would instead be satisfied by any other
    default-off option and would let this one silently default on.
    """
    start = re.search(r"option\(\s*'" + re.escape(name) + r"'\s*,", options)
    if start is None:
        return False

    remainder = options[start.end() :]
    following = re.search(r"\boption\(", remainder)
    block = remainder[: following.start()] if following else remainder
    return re.search(r"\bvalue:\s*false\b", block) is not None


def manifest_entries(failures: list[str]) -> tuple[str, ...]:
    text = read_text(SOURCE_MANIFEST, failures)
    return tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def audit_sources(source_root: Path, sdk_root: Path) -> list[str]:
    failures: list[str] = []
    manifest = manifest_entries(failures)
    require(
        manifest == EXPECTED_SOURCE_FILES,
        "TOD source manifest differs from the reviewed exact allowlist",
        failures,
    )

    if source_root.is_dir():
        actual_files = tuple(
            sorted(
                path.relative_to(source_root).as_posix()
                for path in source_root.rglob("*")
                if path.is_file()
            )
        )
    else:
        actual_files = ()
        failures.append(f"prepared source root is unavailable: {source_root}")
    require(
        actual_files == tuple(sorted(EXPECTED_SOURCE_FILES)),
        "prepared source tree contains missing or non-allowlisted files",
        failures,
    )

    driver_dir = source_root / "drivers" / "goodix53x5"
    session_raw = read_text(driver_dir / "goodix53x5-session.c", failures)
    commands_raw = read_text(driver_dir / "goodix53x5-commands.c", failures)
    transport_raw = read_text(driver_dir / "goodix53x5-transport.c", failures)
    calibration = read_text(driver_dir / "goodix53x5-calibration.c", failures)
    safety = read_text(driver_dir / "goodix53x5-safety.h", failures)
    tls = read_text(driver_dir / "goodix53x5-tls.c", failures)
    device = read_text(driver_dir / "goodix53x5.c", failures)
    scan = read_text(driver_dir / "goodix53x5-scan.c", failures)
    try:
        session = effective_source(session_raw)
        commands = effective_source(commands_raw)
    except ValueError as error:
        failures.append(str(error))
        session = session_raw
        commands = commands_raw

    for helper in PERSISTENT_HELPERS:
        require(
            helper not in session and helper not in commands,
            f"persistent helper remains in compiled source: {helper}",
            failures,
        )
    require(
        "goodix_550c_command_is_persistent_mutation (category, command)"
        in transport_raw,
        "transport persistent-command deny predicate is missing",
        failures,
    )
    for category, command in (
        ("0xA", "0x2"),
        ("0xE", "0x0"),
        ("0xE", "0x1"),
        ("0xF", "0x0"),
        ("0xF", "0x2"),
    ):
        require(
            re.search(rf"category == {category}\s*&&\s*command == {command}", safety)
            is not None,
            f"persistent opcode pair {category}/{command} is not denied",
            failures,
        )

    require(
        "#define GOODIX550C_ENABLE_VOLATILE_INIT 0" in safety,
        "volatile initialization is not compile-time default-off",
        failures,
    )
    require(
        'g_getenv ("GOODIX550C_ALLOW_VOLATILE_INIT"), "1"' in safety,
        "volatile initialization lacks exact runtime opt-in",
        failures,
    )
    require(
        "goodix_550c_command_needs_volatile_init (category, command)"
        in transport_raw,
        "transport volatile-command gate is missing",
        failures,
    )
    require(
        re.search(
            r"if\s*\(self->variant\s*==\s*GOODIX_VARIANT_TLS_PSK\).*?"
            r"GOODIX_PROTO_CMD_FDT_DOWN,\s*fdt_base,\s*"
            r"GOODIX_FDT_BASE_LEN,\s*FALSE",
            commands,
            re.DOTALL,
        )
        is not None,
        "firmware-13021 TLS branch does not send the raw 24-byte FDT-down base",
        failures,
    )
    require(
        'goodix_build_fdt_payload (0x0C, fdt_base, &payload, &payload_len)'
        in commands,
        "non-TLS FDT-down branch lost its prefixed payload builder",
        failures,
    )
    require(
        'goodix_build_fdt_payload (0x0E, fdt_base, &payload, &payload_len)'
        in commands,
        "FDT-up path lost its prefixed payload builder",
        failures,
    )
    require(
        "goodix_build_fdt_payload (op_code, fdt_base, &payload, &payload_len)"
        in commands,
        "manual FDT path lost its prefixed payload builder",
        failures,
    )
    require(
        'fp_dbg ("FDT down ACK validated; event read posted")' in scan,
        "FDT-down ACK/event diagnostic marker is missing",
        failures,
    )
    require(
        session.count("g_usb_device_reset (") == 1
        and "if (!goodix_550c_volatile_init_allowed ())" in session,
        "active USB reset is absent or not policy-guarded",
        failures,
    )
    require(
        "G_USB_DEVICE_CLAIM_INTERFACE_BIND_KERNEL_DRIVER" not in session,
        "driver may detach or rebind a kernel driver",
        failures,
    )

    for marker in (
        "#define GOODIX550C_ENABLE_MANUAL_FDT_POLL 0",
        'g_getenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL"), "1"',
    ):
        require(marker in safety, f"manual-FDT gate missing: {marker}", failures)
    for marker in (
        "GOODIX_MANUAL_FDT_CONFIRMATIONS 2",
        "GOODIX_MANUAL_FDT_POLL_DELAY_MS 100",
        "goodix_550c_manual_fdt_poll_allowed ()",
        "goodix_cmd_fdt_manual (ssm, dev, TRUE",
        "down = changed_pairs > 0",
        "state->consecutive >= GOODIX_MANUAL_FDT_CONFIRMATIONS",
        "state->provisional_baseline",
        "GOODIX_REF_CAPTURE_REG_OFF_DONE",
        "Manual FDT finger-down poll armed",
        "Manual FDT finger-up poll armed",
        "Manual FDT finger-up confirmed",
    ):
        require(marker in scan, f"manual-FDT invariant missing: {marker}", failures)
    require(
        "goodix_device_measure_fdt_delta" in calibration
        and "if (delta > threshold)" in calibration,
        "manual-FDT delta helper or strict threshold boundary is missing",
        failures,
    )
    require(
        "goodix_run_cmd_cancellable" not in transport_raw
        and "goodix_cmd_fdt_manual_cancellable" not in commands_raw,
        "manual polling may abandon a partially received command transaction",
        failures,
    )
    require(
        all(
            marker not in scan
            for marker in (
                "touch_flag=0x",
                "touch_flag == 0",
                "max_delta=",
                "changed_pairs=",
            )
        ),
        "manual-FDT diagnostics expose channel/contact measurements",
        failures,
    )
    require(
        "ACK diagnostic:" in transport_raw
        and "goodix_550c_manual_fdt_poll_allowed ()" in transport_raw,
        "ACK flags are not neutrally diagnosed under the experimental gate",
        failures,
    )

    require("/etc/goodix550c.psk" not in session_raw, "fixed PSK fallback remains", failures)
    require("memcpy (out, goodix_psk" not in session, "zero-PSK fallback remains", failures)
    for marker in (
        'g_getenv ("GOODIX550C_PSK")',
        'g_getenv ("GOODIX550C_PSK_FILE")',
        "Set exactly one of GOODIX550C_PSK",
        "g_path_is_absolute (path)",
        "O_RDONLY | O_CLOEXEC | O_NOFOLLOW",
        "fstat (fd, &st)",
        "st.st_uid != geteuid ()",
        "(st.st_mode & 0777) != 0600",
        "st.st_nlink != 1",
        "OPENSSL_cleanse (hex, hex_alloc_len)",
        "OPENSSL_cleanse (psk, sizeof (psk))",
    ):
        require(marker in session_raw, f"secret-loading invariant missing: {marker}", failures)
    require(
        '#define GOODIX_550C_EXPECTED_FW "GF5288_GM168SEC_APP_13021"'
        in session_raw,
        "exact firmware gate is missing",
        failures,
    )
    require(
        'SSL_CTX_set_cipher_list (tls->ctx, "PSK-AES128-CBC-SHA256")' in tls,
        "TLS cipher restriction is missing",
        failures,
    )
    require(
        'GOODIX_TLS_PSK_IDENTITY "Client_identity"' in tls
        and "strcmp (identity, GOODIX_TLS_PSK_IDENTITY)" in tls,
        "TLS identity restriction is missing",
        failures,
    )
    require(
        "OPENSSL_cleanse (tls->psk, sizeof (tls->psk))" in tls,
        "long-lived TLS PSK is not cleansed",
        failures,
    )

    pids = set(re.findall(r"\.pid\s*=\s*0x([0-9a-fA-F]+)", device))
    vids = set(re.findall(r"\.vid\s*=\s*0x([0-9a-fA-F]+)", device))
    require(pids == {"550c"}, f"unexpected USB product IDs: {sorted(pids)}", failures)
    require(vids == {"27c6"}, f"unexpected USB vendor IDs: {sorted(vids)}", failures)

    sdk_public = sdk_root / "usr/include/libfprint-2"
    sdk_tod = sdk_public / "tod-1"
    for required_header in (
        sdk_public / "fprint.h",
        sdk_tod / "drivers_api.h",
        sdk_tod / "fpi-device.h",
        sdk_tod / "fpi-print.h",
        sdk_tod / "fpi-ssm.h",
        sdk_tod / "fpi-usb-transfer.h",
    ):
        require(
            required_header.is_file(),
            f"exact SDK header is missing: {required_header}",
            failures,
        )
    pc_text = read_text(
        sdk_root
        / "usr/lib/x86_64-linux-gnu/pkgconfig/libfprint-2-tod-1.pc",
        failures,
    )
    require(
        "Version: 1.95.1+tod1+tod1" in pc_text,
        "TOD SDK pkg-config version is not the pinned Ubuntu ABI",
        failures,
    )

    entry = read_text(TOD_ENTRY, failures)
    require(
        "G_MODULE_EXPORT GType" in entry
        and "fpi_tod_shared_driver_get_type (void)" in entry
        and "return fpi_device_goodix53x5_get_type ();" in entry,
        "TOD registration wrapper is missing or unexpected",
        failures,
    )
    symbol_map = read_text(TOD_SYMBOL_MAP, failures)
    require(
        symbol_map.count("fpi_tod_shared_driver_get_type;") == 1
        and "local:" in symbol_map
        and "*;" in symbol_map,
        "TOD export map does not restrict the public module ABI",
        failures,
    )
    meson = read_text(TOD_MESON, failures)
    options = read_text(TOD_OPTIONS, failures)
    for marker in (
        "shared_module(",
        "'goodix550c'",
        "goodix550c-sources.txt",
        "gnu_symbol_visibility: 'hidden'",
        "'-Wl,-z,defs'",
        "--version-script=",
        "link_depends: symbol_map",
        "tod_library",
        "core_library",
        "-D_FORTIFY_SOURCE=3",
        "-fstack-protector-strong",
        "'-Wl,-z,relro'",
        "'-Wl,-z,now'",
        "'-Wl,-z,noexecstack'",
    ):
        require(marker in meson, f"TOD Meson invariant missing: {marker}", failures)
    require(
        meson_option_is_default_off(options, "goodix550c_volatile_init"),
        "TOD Meson volatile-init option is not default-off",
        failures,
    )
    require(
        "-DGOODIX550C_ENABLE_VOLATILE_INIT=1" in meson,
        "TOD Meson opt-in does not enable the compile-time gate",
        failures,
    )
    require(
        meson_option_is_default_off(options, "goodix550c_manual_fdt_poll"),
        "TOD Meson manual-FDT option is not default-off",
        failures,
    )
    require(
        "-DGOODIX550C_ENABLE_MANUAL_FDT_POLL=1" in meson,
        "TOD Meson manual-FDT opt-in does not enable the compile-time gate",
        failures,
    )

    harness = read_text(TOD_HARNESS, failures)
    for marker in (
        '#define EXPECTED_DRIVER "goodix53x5"',
        '#define EXPECTED_NAME "Goodix HTK32 Fingerprint Sensor"',
        'g_getenv ("FP_TOD_DRIVERS_DIR")',
        'g_getenv ("FP_DRIVERS_ALLOWLIST")',
        'g_getenv ("GOODIX550C_ALLOW_VOLATILE_INIT")',
        'g_getenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL")',
        'g_getenv ("GOODIX550C_PSK_FILE")',
        'g_getenv ("GOODIX550C_PSK") == NULL',
        'g_getenv ("LD_LIBRARY_PATH") == NULL',
        'g_getenv ("LD_PRELOAD") == NULL',
        "devices->len != 1",
        "fp_device_get_driver (device)",
        "fp_device_get_name (device)",
        "fp_device_get_scan_type (device) != FP_SCAN_TYPE_PRESS",
        "fp_device_open_sync (device, NULL, &error)",
        "fp_device_close_sync (device, NULL, &error)",
    ):
        require(marker in harness, f"TOD physical harness invariant missing: {marker}", failures)

    enroll_harness = read_text(TOD_ENROLL_HARNESS, failures)
    for marker in (
        '#define EXPECTED_DRIVER "goodix53x5"',
        '#define EXPECTED_NAME "Goodix HTK32 Fingerprint Sensor"',
        # Enrollment reaches the finger-wait states, so unlike open/close this
        # harness requires the manual-FDT gate rather than tolerating absence.
        'g_strcmp0 (manual_fdt_gate, "1") == 0',
        'g_getenv ("GOODIX550C_PSK") == NULL',
        'g_getenv ("LD_LIBRARY_PATH") == NULL',
        'g_getenv ("LD_PRELOAD") == NULL',
        "devices->len != 1",
        "fp_device_get_scan_type (device) != FP_SCAN_TYPE_PRESS",
        "fp_device_open_sync (device, NULL, &error)",
        "fp_device_enroll_sync (device",
        "fp_device_close_sync (device, NULL, &error)",
    ):
        require(
            marker in enroll_harness,
            f"TOD enrollment harness invariant missing: {marker}",
            failures,
        )
    for forbidden in (
        "fp_print_serialize",
        "fp_print_to_file",
        "g_file_set_contents",
        "fopen",
    ):
        require(
            forbidden not in enroll_harness,
            f"TOD enrollment harness must not persist template data: {forbidden}",
            failures,
        )

    # The finger stays PRESENT for the whole bounded finger-up wait, so a
    # status-driven lift prompt only arrives once that wait has already
    # succeeded or expired. The instruction has to come from the stage
    # callback, once per outcome branch.
    require(
        enroll_harness.count(">>> LIFT your finger off the sensor.") == 2,
        "TOD enrollment harness does not prompt the lift from both stage outcomes",
        failures,
    )
    require(
        'puts (">>> LIFT' not in enroll_harness and ">>> Finger detected" not in enroll_harness,
        "TOD enrollment harness issues operator instructions from the finger-status callback",
        failures,
    )
    # Worker threads from the module's OpenCV dependency outlive this harness
    # and cannot be joined, so global teardown must not run beside them.
    require(
        "_exit (enrolled_ok ? 0 : 1)" in enroll_harness
        and "return enrolled_ok ? 0 : 1;" not in enroll_harness,
        "TOD enrollment harness runs global destructors alongside unjoinable threads",
        failures,
    )

    verify_harness = read_text(TOD_VERIFY_HARNESS, failures)
    for marker in (
        '#define EXPECTED_DRIVER "goodix53x5"',
        '#define EXPECTED_NAME "Goodix HTK32 Fingerprint Sensor"',
        # Verification reaches the same finger-wait states as enrollment.
        'g_strcmp0 (manual_fdt_gate, "1") == 0',
        'g_getenv ("GOODIX550C_PSK") == NULL',
        'g_getenv ("LD_LIBRARY_PATH") == NULL',
        'g_getenv ("LD_PRELOAD") == NULL',
        "devices->len != 1",
        "fp_device_get_scan_type (device) != FP_SCAN_TYPE_PRESS",
        "fp_device_has_feature (device, FP_DEVICE_FEATURE_VERIFY)",
        "fp_device_open_sync (device, NULL, &error)",
        "fp_device_enroll_sync (device",
        "fp_device_verify_sync (device, enrolled, NULL, on_match_reported, NULL,",
        "fp_device_close_sync (device, NULL, &error)",
        "_exit (verified_ok ? 0 : 1)",
    ):
        require(
            marker in verify_harness,
            f"TOD verification harness invariant missing: {marker}",
            failures,
        )
    for forbidden in (
        "fp_print_serialize",
        "fp_print_to_file",
        "g_file_set_contents",
        "fopen",
    ):
        require(
            forbidden not in verify_harness,
            f"TOD verification harness must not persist template data: {forbidden}",
            failures,
        )
    # A positive-only test would pass against a driver that matched everything,
    # so at least one trial must expect a rejection.
    require(
        "#define VERIFY_DIFFERENT_FINGER_TRIALS 1" in verify_harness
        and "expect no match" in verify_harness,
        "TOD verification harness does not test a non-matching finger",
        failures,
    )
    require(
        "observed_matches == expected_matches" in verify_harness,
        "TOD verification harness does not compare observed verdicts against expected ones",
        failures,
    )
    probe_harness = read_text(TOD_PROBE_HARNESS, failures)
    for marker in (
        'g_strcmp0 (manual_fdt_gate, "1") == 0',
        'g_getenv ("GOODIX550C_PSK") == NULL',
        "fp_device_identify_sync (device, gallery, NULL, on_match_reported,",
        "#define PROBE_ACTIONS 3",
    ):
        require(
            marker in probe_harness,
            f"TOD desync probe invariant missing: {marker}",
            failures,
        )
    # The probe exists to exercise the capture path, never to enrol or match.
    for forbidden in (
        "fp_device_enroll_sync",
        "fp_print_serialize",
        "g_file_set_contents",
        "fopen",
    ):
        require(
            forbidden not in probe_harness,
            f"TOD desync probe must not enrol or persist data: {forbidden}",
            failures,
        )
    require(
        "g_ptr_array_new_with_free_func (g_object_unref)" in probe_harness
        and "g_ptr_array_add (gallery" not in probe_harness,
        "TOD desync probe gallery is not empty by construction",
        failures,
    )

    # A retry is the device asking for another placement, not a verdict.
    require(
        "#define VERIFY_MAX_RETRIES" in verify_harness
        and "error->domain == FP_DEVICE_RETRY" in verify_harness,
        "TOD verification harness counts a retry request as a lost trial",
        failures,
    )

    runner = read_text(TOD_RUNNER, failures)
    for marker in (
        "--allow-volatile-init",
        "--allow-manual-fdt-poll",
        "volatile_init || true)",
        "manual_fdt_poll || true)",
        "expected_firmware || true)",
        "expected_usb_id || true)",
        "--expected-psk-hash",
        "source_checksums_sha256 || true)",
        'sha256sum --check --strict "$SOURCE_CHECKSUMS"',
        "hmac.compare_digest(hashlib.sha256(psk).digest(), expected)",
        '[[ "$fprintd_state" != inactive ]]',
        '[[ -L "$interface/driver" ]]',
        'fuser -s "$usb_node"',
        "FP_TOD_DRIVERS_DIR=\"$BUILD_DIR\"",
        "FP_DRIVERS_ALLOWLIST=goodix53x5",
        "GOODIX550C_ALLOW_VOLATILE_INIT=1",
        "GOODIX550C_ALLOW_MANUAL_FDT_POLL=1",
        'HOME="$LIVE_RUN_DIR/home"',
        'TMPDIR="$LIVE_RUN_DIR/tmp"',
        'XDG_RUNTIME_DIR="$LIVE_RUN_DIR/runtime"',
        "env -i",
    ):
        require(marker in runner, f"TOD physical runner invariant missing: {marker}", failures)
    require("systemctl stop" not in runner, "TOD runner stops fprintd", failures)
    require("kill " not in runner, "TOD runner signals an existing process", failures)

    smoke = read_text(TOD_SMOKE, failures)
    for marker in (
        '--tmpfs "$PROJECT_ROOT"',
        '--bind "$RUN_DIR" "$SANDBOX_RUN_DIR"',
        'test ! -e "$1/.env"',
        'test ! -e "$1/research/secrets"',
        "recorded_child_is_live",
        '"${stat_fields[1]}" == "$$"',
        '"${stat_fields[19]}" == "$expected_start"',
        "done < <(jobs -pr)",
        'realpath -e "/proc/$pid/exe"',
    ):
        require(marker in smoke, f"TOD no-USB smoke invariant missing: {marker}", failures)
    require(
        '--bind "$PROJECT_ROOT" "$PROJECT_ROOT"' not in smoke,
        "TOD no-USB smoke exposes the entire project read/write",
        failures,
    )
    return failures


def run_tool(arguments: list[str], failures: list[str]) -> str:
    try:
        result = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        failures.append(f"cannot execute {' '.join(arguments)}: {error}")
        return ""
    if result.returncode != 0:
        failures.append(
            f"{' '.join(arguments)} failed with status {result.returncode}: "
            f"{result.stderr.strip()}"
        )
        return ""
    return result.stdout


def symbol_names(output: str) -> set[str]:
    symbols: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if not fields:
            continue
        candidate = fields[-1]
        candidate = candidate.split("@", 1)[0]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", candidate):
            symbols.add(candidate)
    return symbols


def audit_build_option(
    build_dir: Path,
    expected_volatile_init: bool,
    expected_manual_fdt_poll: bool,
    failures: list[str],
) -> None:
    build_options = build_dir / "meson-info" / "intro-buildoptions.json"
    try:
        options = json.loads(build_options.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        failures.append(f"cannot inspect Meson build options: {error}")
        return
    values = {item.get("name"): item.get("value") for item in options}
    require(
        values.get("goodix550c_volatile_init") is expected_volatile_init,
        "compiled Meson volatile-init value differs from the requested gate",
        failures,
    )
    require(
        values.get("goodix550c_manual_fdt_poll") is expected_manual_fdt_poll,
        "compiled Meson manual-FDT value differs from the requested gate",
        failures,
    )

    compile_commands = build_dir / "compile_commands.json"
    commands = read_text(compile_commands, failures)
    marker_present = "-DGOODIX550C_ENABLE_VOLATILE_INIT=1" in commands
    require(
        marker_present is expected_volatile_init,
        "compile command volatile-init macro differs from the Meson option",
        failures,
    )
    manual_marker_present = "-DGOODIX550C_ENABLE_MANUAL_FDT_POLL=1" in commands
    require(
        manual_marker_present is expected_manual_fdt_poll,
        "compile command manual-FDT macro differs from the Meson option",
        failures,
    )
    for hardening_flag in ("-D_FORTIFY_SOURCE=3", "-fstack-protector-strong"):
        require(
            hardening_flag in commands,
            f"module compile commands lack hardening flag {hardening_flag}",
            failures,
        )


def audit_module(
    module: Path,
    core_library: Path,
    tod_library: Path,
    build_dir: Path | None,
    expected_volatile_init: bool,
    expected_manual_fdt_poll: bool,
) -> list[str]:
    failures: list[str] = []
    for path in (module, core_library, tod_library):
        require(path.is_file(), f"ELF input is unavailable: {path}", failures)
    if failures:
        return failures

    header = run_tool(["readelf", "--file-header", str(module)], failures)
    require("Class:                             ELF64" in header, "module is not ELF64", failures)
    require(
        "Type:                              DYN (Shared object file)" in header,
        "module is not an ELF shared object",
        failures,
    )
    require(
        "Machine:                           Advanced Micro Devices X86-64" in header,
        "module is not amd64",
        failures,
    )

    dynamic = run_tool(["readelf", "--dynamic", str(module)], failures)
    needed = set(re.findall(r"Shared library: \[([^]]+)\]", dynamic))
    require("libfprint-2.so.2" in needed, "module does not link the exact core SONAME", failures)
    require(
        "libfprint-2-tod.so.1" in needed,
        "module does not link the exact TOD SONAME",
        failures,
    )
    require(
        "(RPATH)" not in dynamic and "(RUNPATH)" not in dynamic,
        "module contains an RPATH or RUNPATH",
        failures,
    )
    require("BIND_NOW" in dynamic, "module does not have full RELRO/BIND_NOW", failures)
    program_headers = run_tool(
        ["readelf", "--wide", "--program-headers", str(module)], failures
    )
    require("GNU_RELRO" in program_headers, "module lacks GNU_RELRO", failures)
    module_stack = [
        line for line in program_headers.splitlines() if "GNU_STACK" in line
    ]
    require(
        bool(module_stack) and all("RWE" not in line for line in module_stack),
        "module requests an executable stack",
        failures,
    )

    defined_output = run_tool(["nm", "-D", "--defined-only", str(module)], failures)
    defined = symbol_names(defined_output)
    require(
        "fpi_tod_shared_driver_get_type" in defined,
        "module does not export the TOD registration symbol",
        failures,
    )
    require(
        defined == {"fpi_tod_shared_driver_get_type"},
        f"module has unexpected exported definitions: {sorted(defined)}",
        failures,
    )
    unexpected_driver_exports = {
        name
        for name in defined
        if (name.startswith("fp_") or name.startswith("fpi_"))
        and name != "fpi_tod_shared_driver_get_type"
    }
    require(
        not unexpected_driver_exports,
        f"private driver symbols are unexpectedly exported: {sorted(unexpected_driver_exports)}",
        failures,
    )

    undefined_output = run_tool(["nm", "-D", "--undefined-only", str(module)], failures)
    undefined = symbol_names(undefined_output)
    fprint_imports = {
        name for name in undefined if name.startswith("fp_") or name.startswith("fpi_")
    }
    runtime_exports: set[str] = set()
    for runtime in (core_library, tod_library):
        runtime_exports |= symbol_names(
            run_tool(["nm", "-D", "--defined-only", str(runtime)], failures)
        )
    missing = fprint_imports - runtime_exports
    require(
        not missing,
        f"module imports libfprint symbols absent from installed core/TOD: {sorted(missing)}",
        failures,
    )

    all_symbols = run_tool(["nm", str(module)], failures)
    require(
        "__stack_chk_fail" in all_symbols,
        "module has no observable stack-protector reference",
        failures,
    )
    for forbidden in PERSISTENT_HELPERS:
        require(
            forbidden not in all_symbols,
            f"forbidden helper linked into module: {forbidden}",
            failures,
        )

    tod_versions = run_tool(["readelf", "--version-info", str(tod_library)], failures)
    require(
        "LIBFPRINT_TOD_1_1.95.1" in tod_versions,
        "installed TOD runtime lacks the expected 1.95.1 symbol version",
        failures,
    )
    if build_dir is not None:
        audit_build_option(
            build_dir,
            expected_volatile_init,
            expected_manual_fdt_poll,
            failures,
        )
    return failures


def audit_harness(
    harness: Path,
    core_library: Path,
    tod_library: Path,
) -> list[str]:
    """Verify that the helper is a hardened PIE for the installed TOD stack."""
    failures: list[str] = []
    for path in (harness, core_library, tod_library):
        require(path.is_file(), f"harness ELF input is unavailable: {path}", failures)
    if failures:
        return failures

    header = run_tool(["readelf", "--file-header", str(harness)], failures)
    require(
        "Class:                             ELF64" in header,
        "TOD harness is not ELF64",
        failures,
    )
    require(
        "Type:                              DYN (Position-Independent Executable file)"
        in header,
        "TOD harness is not a position-independent executable",
        failures,
    )
    require(
        "Machine:                           Advanced Micro Devices X86-64" in header,
        "TOD harness is not amd64",
        failures,
    )

    dynamic = run_tool(["readelf", "--dynamic", str(harness)], failures)
    needed = set(re.findall(r"Shared library: \[([^]]+)\]", dynamic))
    require(
        "libfprint-2.so.2" in needed,
        "TOD harness does not link the installed core SONAME",
        failures,
    )
    require(
        "libfprint-2-tod.so.1" in needed,
        "TOD harness does not link the installed TOD SONAME",
        failures,
    )
    require(
        "(RPATH)" not in dynamic and "(RUNPATH)" not in dynamic,
        "TOD harness contains an RPATH or RUNPATH",
        failures,
    )
    require(
        "BIND_NOW" in dynamic,
        "TOD harness does not request immediate relocation binding",
        failures,
    )
    program_headers = run_tool(
        ["readelf", "--wide", "--program-headers", str(harness)], failures
    )
    require("GNU_RELRO" in program_headers, "TOD harness lacks GNU_RELRO", failures)
    harness_stack = [
        line for line in program_headers.splitlines() if "GNU_STACK" in line
    ]
    require(
        bool(harness_stack) and all("RWE" not in line for line in harness_stack),
        "TOD harness requests an executable stack",
        failures,
    )

    undefined_output = run_tool(["nm", "-D", "--undefined-only", str(harness)], failures)
    undefined = symbol_names(undefined_output)
    require(
        "__stack_chk_fail" in undefined,
        "TOD harness has no observable stack-protector reference",
        failures,
    )
    required_calls = {
        "fp_context_get_devices",
        "fp_context_new",
        "fp_device_close_sync",
        "fp_device_get_driver",
        "fp_device_get_name",
        "fp_device_get_scan_type",
        "fp_device_open_sync",
    }
    require(
        required_calls <= undefined,
        f"TOD harness lacks expected installed-runtime calls: {sorted(required_calls - undefined)}",
        failures,
    )

    runtime_exports: set[str] = set()
    for runtime in (core_library, tod_library):
        runtime_exports |= symbol_names(
            run_tool(["nm", "-D", "--defined-only", str(runtime)], failures)
        )
    missing = {name for name in undefined if name.startswith("fp_")} - runtime_exports
    require(
        not missing,
        f"TOD harness imports libfprint calls absent from installed runtime: {sorted(missing)}",
        failures,
    )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--module", type=Path)
    parser.add_argument("--harness", type=Path)
    parser.add_argument("--enroll-harness", type=Path)
    parser.add_argument("--verify-harness", type=Path)
    parser.add_argument("--probe-harness", type=Path)
    parser.add_argument("--core-library", type=Path)
    parser.add_argument("--tod-library", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument(
        "--expected-volatile-init",
        choices=("true", "false"),
        default="false",
    )
    parser.add_argument(
        "--expected-manual-fdt-poll",
        choices=("true", "false"),
        default="false",
    )
    args = parser.parse_args(argv)

    failures = audit_sources(args.source_root.resolve(), args.sdk_root.resolve())
    if args.module is not None:
        if (
            args.core_library is None
            or args.tod_library is None
            or args.harness is None
        ):
            parser.error(
                "--module requires --harness, --core-library, and --tod-library"
            )
        failures.extend(
            audit_module(
                args.module.resolve(),
                args.core_library.resolve(),
                args.tod_library.resolve(),
                args.build_dir.resolve() if args.build_dir else None,
                args.expected_volatile_init == "true",
                args.expected_manual_fdt_poll == "true",
            )
        )
        failures.extend(
            audit_harness(
                args.harness.resolve(),
                args.core_library.resolve(),
                args.tod_library.resolve(),
            )
        )
        if args.enroll_harness is not None:
            failures.extend(
                audit_harness(
                    args.enroll_harness.resolve(),
                    args.core_library.resolve(),
                    args.tod_library.resolve(),
                )
            )
        if args.verify_harness is not None:
            failures.extend(
                audit_harness(
                    args.verify_harness.resolve(),
                    args.core_library.resolve(),
                    args.tod_library.resolve(),
                )
            )
        if args.probe_harness is not None:
            failures.extend(
                audit_harness(
                    args.probe_harness.resolve(),
                    args.core_library.resolve(),
                    args.tod_library.resolve(),
                )
            )

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    if args.module is None:
        print("Goodix 550c TOD source policy audit passed")
    else:
        print("Goodix 550c TOD source and ABI audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
