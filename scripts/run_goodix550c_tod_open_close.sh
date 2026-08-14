#!/usr/bin/env bash
# Physically open only the exact 550c through a verified project-local TOD module.
# This wrapper never installs files, changes services, or signals existing holders.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_DRIVER_COMMIT='a3de5a1b6174ace5db0bb2a8796c5be6e55428f0'
EXPECTED_FIRMWARE='GF5288_GM168SEC_APP_13021'
EXPECTED_UBUNTU_VERSION='1:1.95.1+tod1-0ubuntu2'
EXPECTED_ARCH='amd64'
CORE_LIBRARY='/usr/lib/x86_64-linux-gnu/libfprint-2.so.2'
TOD_LIBRARY='/usr/lib/x86_64-linux-gnu/libfprint-2-tod.so.1'
STAGE_DIR=''
PSK_FILE=''
EXPECTED_HASH_FILE=''
VOLATILE_ACK=0
MANUAL_FDT_ACK=0
ACTION=open-close
DEBUG_LOG=0

usage() {
    printf '%s\n' \
        "Usage: sudo $0 --stage-dir BUILD_STAGE --psk-file SECRET_FILE \\" \
        '  --expected-psk-hash LIVE_HASH_FILE --allow-volatile-init \\' \
        '  [--allow-manual-fdt-poll] [--enroll|--verify] [--debug]' \
        '' \
        'All paths must resolve below this project. The stage must have been' \
        'built with both experimental options. This wrapper stops no service or' \
        'holder and performs no installation or persistent sensor write.' \
        '' \
        '--allow-manual-fdt-poll is optional for the default open/close, which' \
        'reaches no manual-FDT state; omit it for a control run. --enroll runs the' \
        'enrollment harness instead and requires that acknowledgement, because on' \
        'firmware 13021 the finger-wait states depend on manual polling. Enrollment' \
        'discards its template; it stores no fingerprint.' \
        '' \
        '--verify enrolls, keeps the template in process memory only, then matches' \
        'live fingers against it and reports each verdict. It requires the same' \
        'acknowledgement and stores neither the template nor any scanned print.' \
        '' \
        '--debug adds driver diagnostic logging to stderr. It changes no device' \
        'behaviour and logs no key, raw reading, image, or template.'
}

while (($#)); do
    case "$1" in
        --stage-dir)
            shift
            (($#)) || { printf 'Missing value for --stage-dir\n' >&2; exit 2; }
            STAGE_DIR="$1"
            ;;
        --psk-file)
            shift
            (($#)) || { printf 'Missing value for --psk-file\n' >&2; exit 2; }
            PSK_FILE="$1"
            ;;
        --expected-psk-hash)
            shift
            (($#)) || { printf 'Missing value for --expected-psk-hash\n' >&2; exit 2; }
            EXPECTED_HASH_FILE="$1"
            ;;
        --allow-volatile-init)
            VOLATILE_ACK=1
            ;;
        --allow-manual-fdt-poll)
            MANUAL_FDT_ACK=1
            ;;
        --enroll)
            ACTION=enroll
            ;;
        --verify)
            ACTION=verify
            ;;
        --debug)
            DEBUG_LOG=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if ((EUID != 0)); then
    printf 'Refusing: physical USB open requires running this wrapper through sudo.\n' >&2
    exit 2
fi
if ((VOLATILE_ACK != 1)); then
    printf 'Refusing: --allow-volatile-init is required for this one run.\n' >&2
    exit 2
fi
if [[ -z "$STAGE_DIR" || -z "$PSK_FILE" || -z "$EXPECTED_HASH_FILE" ]]; then
    usage >&2
    exit 2
fi

for command in awk chmod dpkg dpkg-query env find fuser grep install mktemp nm \
    python3 readelf realpath sha256sum stat systemctl timeout; do
    command -v "$command" >/dev/null || {
        printf 'Required command is unavailable: %s\n' "$command" >&2
        exit 1
    }
done

for supplied_path in "$STAGE_DIR" "$PSK_FILE" "$EXPECTED_HASH_FILE"; do
    if [[ -L "$supplied_path" ]]; then
        printf 'Refusing a symlink argument: %s\n' "$supplied_path" >&2
        exit 2
    fi
done
STAGE_DIR="$(realpath -e "$STAGE_DIR")"
PSK_FILE="$(realpath -e "$PSK_FILE")"
EXPECTED_HASH_FILE="$(realpath -e "$EXPECTED_HASH_FILE")"
case "$STAGE_DIR" in
    "$PROJECT_ROOT"/build/*) ;;
    *) printf 'Refusing stage outside project build/.\n' >&2; exit 2 ;;
esac
case "$PSK_FILE" in
    "$PROJECT_ROOT"/research/secrets/*) ;;
    *) printf 'Refusing PSK outside project research/secrets/.\n' >&2; exit 2 ;;
esac
case "$EXPECTED_HASH_FILE" in
    "$PROJECT_ROOT"/research/artifacts/*) ;;
    *) printf 'Refusing hash oracle outside project research/artifacts/.\n' >&2; exit 2 ;;
esac

MODULE="$STAGE_DIR/builddir/libgoodix550c.so"
if [[ "$ACTION" == enroll ]]; then
    if ((MANUAL_FDT_ACK != 1)); then
        printf 'Refusing: --enroll requires --allow-manual-fdt-poll.\n' >&2
        exit 2
    fi
    HARNESS="$STAGE_DIR/builddir/goodix550c-tod-enroll"
    HARNESS_METADATA_KEY=enroll_harness_sha256
    HARNESS_TIMEOUT=240s
elif [[ "$ACTION" == verify ]]; then
    if ((MANUAL_FDT_ACK != 1)); then
        printf 'Refusing: --verify requires --allow-manual-fdt-poll.\n' >&2
        exit 2
    fi
    # Verification enrolls first, then runs its match trials, so it needs the
    # enrollment budget plus room for those trials.
    HARNESS="$STAGE_DIR/builddir/goodix550c-tod-verify"
    HARNESS_METADATA_KEY=verify_harness_sha256
    HARNESS_TIMEOUT=420s
else
    HARNESS="$STAGE_DIR/builddir/goodix550c-tod-open-close"
    HARNESS_METADATA_KEY=harness_sha256
    HARNESS_TIMEOUT=45s
fi
METADATA="$STAGE_DIR/build-metadata.txt"
SOURCE_CHECKSUMS="$STAGE_DIR/source-checksums.sha256"
SOURCE_ROOT="$STAGE_DIR/source/module"
BUILD_DIR="$STAGE_DIR/builddir"
SDK_ROOT="$PROJECT_ROOT/build/ubuntu-libfprint-tod-sdk/root"
for regular_file in "$MODULE" "$HARNESS" "$METADATA" "$SOURCE_CHECKSUMS" "$PSK_FILE" \
    "$EXPECTED_HASH_FILE"; do
    if [[ ! -f "$regular_file" || -L "$regular_file" ]]; then
        printf 'Refusing missing, non-regular, or symlink input: %s\n' "$regular_file" >&2
        exit 2
    fi
done
if [[ ! -x "$HARNESS" || ! -d "$SOURCE_ROOT" || ! -d "$BUILD_DIR" || \
      ! -d "$SDK_ROOT" ]]; then
    printf 'Refusing: stage lacks the audited source, build, SDK, or harness.\n' >&2
    exit 2
fi

mapfile -t module_candidates < <(
    find "$BUILD_DIR" -maxdepth 1 -type f -name 'lib*.so' -print
)
if ((${#module_candidates[@]} != 1)) || [[ "${module_candidates[0]}" != "$MODULE" ]]; then
    printf 'Refusing: TOD loader directory must contain exactly libgoodix550c.so.\n' >&2
    exit 2
fi

metadata_value() {
    local key="$1"
    local -a values=()
    mapfile -t values < <(awk -F= -v key="$key" '$1 == key {print substr($0, length(key) + 2)}' "$METADATA")
    if ((${#values[@]} != 1)); then
        return 1
    fi
    printf '%s' "${values[0]}"
}

if [[ "$(metadata_value driver_commit || true)" != "$EXPECTED_DRIVER_COMMIT" || \
      "$(metadata_value expected_firmware || true)" != "$EXPECTED_FIRMWARE" || \
      "$(metadata_value expected_usb_id || true)" != '27c6:550c' || \
      "$(metadata_value ubuntu_version || true)" != "$EXPECTED_UBUNTU_VERSION" || \
      "$(metadata_value ubuntu_arch || true)" != "$EXPECTED_ARCH" || \
      "$(metadata_value volatile_init || true)" != true || \
      "$(metadata_value manual_fdt_poll || true)" != true ]]; then
    printf 'Refusing: TOD build metadata does not match the exact live profile.\n' >&2
    exit 2
fi
if [[ "$(metadata_value module_sha256 || true)" != "$(sha256sum "$MODULE" | awk '{print $1}')" || \
      "$(metadata_value "$HARNESS_METADATA_KEY" || true)" != "$(sha256sum "$HARNESS" | awk '{print $1}')" ]]; then
    printf 'Refusing: TOD module or harness digest differs from build metadata.\n' >&2
    exit 2
fi
if [[ "$(metadata_value source_checksums_sha256 || true)" != \
      "$(sha256sum "$SOURCE_CHECKSUMS" | awk '{print $1}')" ]] || \
   ! (cd "$PROJECT_ROOT" && sha256sum --check --strict "$SOURCE_CHECKSUMS" >/dev/null); then
    printf 'Refusing: tracked inputs differ from the audited TOD build. Rebuild it.\n' >&2
    exit 2
fi

actual_arch="$(dpkg --print-architecture)"
if [[ "$actual_arch" != "$EXPECTED_ARCH" ]]; then
    printf 'Refusing architecture %s; expected %s.\n' "$actual_arch" "$EXPECTED_ARCH" >&2
    exit 2
fi
for package_name in libfprint-2-2 libfprint-2-tod1; do
    actual_version="$(dpkg-query -W -f='${Version}' "$package_name" 2>/dev/null || true)"
    if [[ "$actual_version" != "$EXPECTED_UBUNTU_VERSION" ]]; then
        printf 'Refusing %s version %s; expected %s.\n' \
            "$package_name" "${actual_version:-missing}" "$EXPECTED_UBUNTU_VERSION" >&2
        exit 2
    fi
done
for runtime_library in "$CORE_LIBRARY" "$TOD_LIBRARY"; do
    if [[ ! -f "$runtime_library" ]]; then
        printf 'Refusing missing runtime library: %s\n' "$runtime_library" >&2
        exit 2
    fi
done

python3 "$PROJECT_ROOT/scripts/verify_goodix550c_tod_module.py" \
    --source-root "$SOURCE_ROOT" \
    --sdk-root "$SDK_ROOT" \
    --module "$MODULE" \
    --harness "$HARNESS" \
    --core-library "$CORE_LIBRARY" \
    --tod-library "$TOD_LIBRARY" \
    --build-dir "$BUILD_DIR" \
    --expected-volatile-init true \
    --expected-manual-fdt-poll true

read -r psk_mode psk_uid psk_links < <(stat -c '%a %u %h' "$PSK_FILE")
if [[ "$psk_mode" != 600 || "$psk_uid" != "$EUID" || "$psk_links" != 1 ]]; then
    printf 'Refusing: PSK must be root-owned, mode 0600, with one link.\n' >&2
    exit 2
fi
read -r hash_links < <(stat -c '%h' "$EXPECTED_HASH_FILE")
if [[ "$hash_links" != 1 ]]; then
    printf 'Refusing: live key-hash oracle must have one link.\n' >&2
    exit 2
fi

# Decode and compare entirely in-process. Neither key nor digest is emitted.
python3 - "$PSK_FILE" "$EXPECTED_HASH_FILE" <<'PY'
from __future__ import annotations

import hashlib
import hmac
import json
import string
import sys
from pathlib import Path

psk_text = Path(sys.argv[1]).read_text(encoding="ascii")
if len(psk_text) != 65 or not psk_text.endswith("\n"):
    raise SystemExit("Refusing: PSK must be 64 lowercase hex characters plus newline.")
compact_psk = psk_text[:-1]
if any(char not in string.hexdigits.lower() for char in compact_psk):
    raise SystemExit("Refusing: PSK encoding is not lowercase hexadecimal.")
psk = bytes.fromhex(compact_psk)

oracle_data = Path(sys.argv[2]).read_bytes()
if len(oracle_data) == 32:
    expected = oracle_data
else:
    try:
        oracle_text = oracle_data.decode("ascii")
    except UnicodeDecodeError as error:
        raise SystemExit("Refusing: hash oracle is not ASCII or a raw digest.") from error
    try:
        parsed = json.loads(oracle_text)
    except json.JSONDecodeError:
        compact_hash = "".join(oracle_text.split())
    else:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("psk_hash_hex"), str):
            raise SystemExit("Refusing: hash-oracle JSON lacks psk_hash_hex.")
        compact_hash = parsed["psk_hash_hex"]
    if len(compact_hash) != 64 or any(char not in string.hexdigits for char in compact_hash):
        raise SystemExit("Refusing: live key-hash oracle is not a 32-byte digest.")
    expected = bytes.fromhex(compact_hash)

if not hmac.compare_digest(hashlib.sha256(psk).digest(), expected):
    raise SystemExit("Refusing: PSK does not match the live sensor hash oracle.")
PY

fprintd_state="$(systemctl is-active fprintd.service 2>/dev/null || true)"
if [[ "$fprintd_state" != inactive ]]; then
    printf 'Refusing: fprintd state is %s, not inactive.\n' "$fprintd_state" >&2
    exit 2
fi

usb_devices=()
for candidate in /sys/bus/usb/devices/*; do
    [[ -r "$candidate/idVendor" && -r "$candidate/idProduct" ]] || continue
    [[ "$(<"$candidate/idVendor")" == 27c6 ]] || continue
    [[ "$(<"$candidate/idProduct")" == 550c ]] || continue
    usb_devices+=("$candidate")
done
if ((${#usb_devices[@]} != 1)); then
    printf 'Refusing: expected exactly one 27c6:550c USB device.\n' >&2
    exit 2
fi

usb_device="${usb_devices[0]}"
interface="$usb_device:1.0"
if [[ ! -d "$interface" || "$(<"$interface/bInterfaceNumber")" != 00 ]]; then
    printf 'Refusing: exact USB interface 0 is unavailable.\n' >&2
    exit 2
fi
if [[ -L "$interface/driver" ]]; then
    printf 'Refusing: interface 0 is already claimed or driver-bound.\n' >&2
    exit 2
fi

endpoint_addresses=()
for endpoint in "$interface"/ep_*; do
    [[ -r "$endpoint/bEndpointAddress" && -r "$endpoint/bmAttributes" && \
       -r "$endpoint/wMaxPacketSize" ]] || continue
    if [[ "$(<"$endpoint/bmAttributes")" != 02 || \
          "$(<"$endpoint/wMaxPacketSize")" != 0040 ]]; then
        printf 'Refusing: endpoint type or packet size differs from the exact profile.\n' >&2
        exit 2
    fi
    endpoint_addresses+=("$(<"$endpoint/bEndpointAddress")")
done
if ((${#endpoint_addresses[@]} != 2)) || \
   [[ "${endpoint_addresses[*]}" != '01 83' && \
      "${endpoint_addresses[*]}" != '83 01' ]]; then
    printf 'Refusing: expected only bulk endpoints 0x01 and 0x83.\n' >&2
    exit 2
fi

printf -v bus '%03d' "$((10#$(<"$usb_device/busnum")))"
printf -v device '%03d' "$((10#$(<"$usb_device/devnum")))"
usb_node="/dev/bus/usb/$bus/$device"
if [[ ! -c "$usb_node" ]]; then
    printf 'Refusing: exact USB character device is unavailable.\n' >&2
    exit 2
fi
if fuser -s "$usb_node"; then
    printf 'Refusing: a process already holds the USB node.\n' >&2
    exit 2
fi

LIVE_RUN_DIR="$(mktemp -d "$PROJECT_ROOT/build/goodix550c-tod-run.XXXXXX")"
chmod 0700 "$LIVE_RUN_DIR"
install -d -m 0700 \
    "$LIVE_RUN_DIR/cache" \
    "$LIVE_RUN_DIR/config" \
    "$LIVE_RUN_DIR/data" \
    "$LIVE_RUN_DIR/home" \
    "$LIVE_RUN_DIR/runtime" \
    "$LIVE_RUN_DIR/tmp"

# The private HOME/TMP/XDG tree is per-run state, so remove it on every exit
# path instead of accumulating one root-owned tree under build/ per run. Only a
# path this script created under its own build/ prefix is ever removed.
cleanup_live_run_dir() {
    case "$LIVE_RUN_DIR" in
        "$PROJECT_ROOT"/build/goodix550c-tod-run.??????)
            rm -rf -- "$LIVE_RUN_DIR"
            ;;
    esac
}
trap cleanup_live_run_dir EXIT

# The experimental runtime gate is supplied only when this run acknowledged it,
# so an unacknowledged run cannot enable manual polling and a control run stays
# possible against a manual-FDT stage.
MANUAL_FDT_ENV=()
if ((MANUAL_FDT_ACK == 1)); then
    MANUAL_FDT_ENV=(GOODIX550C_ALLOW_MANUAL_FDT_POLL=1)
fi

# Diagnostic logging only. The driver logs phase transitions and quality
# decisions; it never logs key material, raw readings, images, or templates.
DEBUG_ENV=()
if ((DEBUG_LOG == 1)); then
    DEBUG_ENV=(G_MESSAGES_DEBUG=all)
fi

printf 'Preflight passed; running %s against only 27c6:550c through the project-local TOD module.\n' "$ACTION"
HARNESS_STATUS=0
timeout --foreground --signal=INT --kill-after=5s "$HARNESS_TIMEOUT" \
    env -i \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        HOME="$LIVE_RUN_DIR/home" \
        LANG=C.UTF-8 \
        TMPDIR="$LIVE_RUN_DIR/tmp" \
        XDG_CACHE_HOME="$LIVE_RUN_DIR/cache" \
        XDG_CONFIG_HOME="$LIVE_RUN_DIR/config" \
        XDG_DATA_HOME="$LIVE_RUN_DIR/data" \
        XDG_RUNTIME_DIR="$LIVE_RUN_DIR/runtime" \
        FP_TOD_DRIVERS_DIR="$BUILD_DIR" \
        FP_DRIVERS_ALLOWLIST=goodix53x5 \
        GOODIX550C_ALLOW_VOLATILE_INIT=1 \
        "${MANUAL_FDT_ENV[@]}" \
        "${DEBUG_ENV[@]}" \
        GOODIX550C_PSK_FILE="$PSK_FILE" \
        "$HARNESS" || HARNESS_STATUS=$?

exit "$HARNESS_STATUS"
