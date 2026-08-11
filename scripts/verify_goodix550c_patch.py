#!/usr/bin/env python3
"""Offline policy audit for a prepared fail-closed Goodix 550c tree."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

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


def read(path: Path, failures: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(f"cannot read {path}: {error}")
        return ""


def audit(driver_tree: Path, libfprint_tree: Path) -> list[str]:
    failures: list[str] = []
    driver_dir = driver_tree / "drivers" / "goodix53x5"
    integrated_dir = libfprint_tree / "libfprint" / "drivers" / "goodix53x5"

    session_raw = read(driver_dir / "goodix53x5-session.c", failures)
    commands_raw = read(driver_dir / "goodix53x5-commands.c", failures)
    transport_raw = read(driver_dir / "goodix53x5-transport.c", failures)
    safety = read(driver_dir / "goodix53x5-safety.h", failures)
    tls = read(driver_dir / "goodix53x5-tls.c", failures)
    device = read(driver_dir / "goodix53x5.c", failures)
    lib_meson = read(libfprint_tree / "libfprint" / "meson.build", failures)
    root_meson = read(libfprint_tree / "meson.build", failures)
    options = read(libfprint_tree / "meson_options.txt", failures)

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
        "goodix_550c_command_is_persistent_mutation (category, command)" in transport_raw,
        "transport does not enforce the persistent-command deny predicate",
        failures,
    )
    for category, command in (
        ("0xA", "0x2"),
        ("0xE", "0x0"),
        ("0xE", "0x1"),
        ("0xF", "0x0"),
        ("0xF", "0x2"),
    ):
        pattern = rf"category == {category}\s*&&\s*command == {command}"
        require(
            re.search(pattern, safety) is not None,
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
        "volatile initialization lacks the exact runtime opt-in",
        failures,
    )
    require(
        "goodix_550c_command_needs_volatile_init (category, command)" in transport_raw,
        "volatile commands do not pass through the runtime policy gate",
        failures,
    )
    require(
        session.count("g_usb_device_reset (") == 1
        and "if (!goodix_550c_volatile_init_allowed ())" in session,
        "active USB reset is absent or not guarded by volatile-init policy",
        failures,
    )
    require(
        "G_USB_DEVICE_CLAIM_INTERFACE_BIND_KERNEL_DRIVER" not in session,
        "compiled session may detach or rebind an existing kernel driver",
        failures,
    )

    require("/etc/goodix550c.psk" not in session_raw, "fixed /etc PSK fallback remains", failures)
    require(
        "memcpy (out, goodix_psk" not in session,
        "550c can still fall back to the all-zero PSK",
        failures,
    )
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
        require(marker in session_raw, f"external-PSK invariant missing: {marker}", failures)
    require(
        '#define GOODIX_550C_EXPECTED_FW "GF5288_GM168SEC_APP_13021"' in session_raw,
        "exact validated firmware gate is missing",
        failures,
    )
    require(
        'SSL_CTX_set_cipher_list (tls->ctx, "PSK-AES128-CBC-SHA256")' in tls,
        "TLS cipher list is broader than the captured 550c suite",
        failures,
    )
    require(
        'GOODIX_TLS_PSK_IDENTITY "Client_identity"' in tls
        and "strcmp (identity, GOODIX_TLS_PSK_IDENTITY)" in tls,
        "TLS callback does not restrict the captured PSK identity",
        failures,
    )
    require(
        "OPENSSL_cleanse (tls->psk, sizeof (tls->psk))" in tls,
        "long-lived TLS PSK is not securely cleansed",
        failures,
    )

    pids = set(re.findall(r"\.pid\s*=\s*0x([0-9a-fA-F]+)", device))
    require(pids == {"550c"}, f"driver binds unexpected USB PIDs: {sorted(pids)}", failures)

    require(
        not (integrated_dir / "goodix53x5-firmware550c.c").exists()
        and not (integrated_dir / "goodix53x5-firmware550c.h").exists(),
        "firmware loader was copied into the integration tree",
        failures,
    )
    require(
        "goodix53x5-firmware550c" not in lib_meson,
        "firmware loader is registered in Meson",
        failures,
    )
    for component in (
        "goodix53x5-tls.c",
        "goodix53x5-scan.c",
        "goodix53x5-enroll.c",
        "goodix53x5-auth.c",
        "goodix53x5-match.c",
        "goodix53x5-image.c",
    ):
        require(
            component in lib_meson,
            f"functional component missing from build: {component}",
            failures,
        )

    require(
        "option('goodix550c_volatile_init'" in options and "value: false" in options,
        "Meson volatile-init option is missing or not default-off",
        failures,
    )
    require(
        "-DGOODIX550C_ENABLE_VOLATILE_INIT=1" in lib_meson,
        "Meson opt-in does not enable the compile-time gate",
        failures,
    )
    require("'goodix53x5' : [ 'openssl' ]" in root_meson, "OpenSSL helper missing", failures)

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver-tree", type=Path, required=True)
    parser.add_argument("--libfprint-tree", type=Path, required=True)
    args = parser.parse_args(argv)

    failures = audit(args.driver_tree.resolve(), args.libfprint_tree.resolve())
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    print("Goodix 550c fail-closed policy audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
