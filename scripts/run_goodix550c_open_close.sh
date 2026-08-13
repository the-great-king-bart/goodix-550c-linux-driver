#!/usr/bin/env bash
# Run only the repository-local exact-device harness after fail-closed preflight.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    printf '%s\n' \
        "Usage: sudo $0 --stage-dir BUILD_STAGE --psk-file SECRET_FILE \\" \
        "  --allow-volatile-init [--allow-manual-fdt-poll]" \
        "Both paths must resolve below this project. No service is stopped." \
        "--allow-manual-fdt-poll is optional here: this harness only opens and" \
        "closes, so it reaches no manual-FDT state. Omit it for a control run."
}

STAGE_DIR=""
PSK_FILE=""
VOLATILE_INIT_ACK=0
MANUAL_FDT_ACK=0
while (($#)); do
    case "$1" in
        --stage-dir)
            shift
            (($#)) || { usage >&2; exit 2; }
            STAGE_DIR="$1"
            ;;
        --psk-file)
            shift
            (($#)) || { usage >&2; exit 2; }
            PSK_FILE="$1"
            ;;
        --allow-volatile-init)
            VOLATILE_INIT_ACK=1
            ;;
        --allow-manual-fdt-poll)
            MANUAL_FDT_ACK=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if ((EUID != 0)); then
    printf 'Refusing: USB open requires running this wrapper through sudo.\n' >&2
    exit 2
fi
[[ -n "$STAGE_DIR" && -n "$PSK_FILE" ]] || { usage >&2; exit 2; }
if ((VOLATILE_INIT_ACK != 1)); then
    printf 'Refusing: --allow-volatile-init is required for this one run.\n' >&2
    exit 2
fi

STAGE_DIR="$(realpath -e "$STAGE_DIR")"
PSK_FILE="$(realpath -e "$PSK_FILE")"
case "$STAGE_DIR" in
    "$PROJECT_ROOT"/build/*) ;;
    *) printf 'Refusing stage outside project build/.\n' >&2; exit 2 ;;
esac
case "$PSK_FILE" in
    "$PROJECT_ROOT"/research/secrets/*) ;;
    *) printf 'Refusing PSK outside project research/secrets/.\n' >&2; exit 2 ;;
esac

HARNESS="$STAGE_DIR/builddir/goodix550c-open-close"
LIB_DIR="$STAGE_DIR/builddir/libfprint"
[[ -x "$HARNESS" && -f "$LIB_DIR/libfprint-2.so.2" ]] || {
    printf 'Refusing: stage does not contain the audited harness/library.\n' >&2
    exit 2
}

read -r psk_mode psk_uid psk_links < <(stat -Lc '%a %u %h' "$PSK_FILE")
if [[ "$psk_mode" != 600 || "$psk_uid" != "$EUID" || "$psk_links" != 1 ]]; then
    printf 'Refusing: PSK must be root-owned, mode 0600, with one link.\n' >&2
    exit 2
fi

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
if [[ -L "$interface/driver" ]]; then
    printf 'Refusing: interface 0 is already claimed or driver-bound.\n' >&2
    exit 2
fi

printf -v bus '%03d' "$((10#$(<"$usb_device/busnum")))"
printf -v device '%03d' "$((10#$(<"$usb_device/devnum")))"
usb_node="/dev/bus/usb/$bus/$device"
if fuser -s "$usb_node"; then
    printf 'Refusing: a process already holds the USB node.\n' >&2
    exit 2
fi

# The experimental runtime gate is supplied only when this run acknowledged it,
# so an unacknowledged run cannot enable manual polling through an inherited
# value and a control run stays possible against a manual-FDT stage.
MANUAL_FDT_ENV=()
if ((MANUAL_FDT_ACK == 1)); then
    MANUAL_FDT_ENV=(GOODIX550C_ALLOW_MANUAL_FDT_POLL=1)
fi

exec env \
    -u GOODIX550C_PSK \
    -u LD_PRELOAD \
    -u GOODIX550C_ALLOW_MANUAL_FDT_POLL \
    GOODIX550C_ALLOW_VOLATILE_INIT=1 \
    "${MANUAL_FDT_ENV[@]}" \
    GOODIX550C_PSK_FILE="$PSK_FILE" \
    LD_LIBRARY_PATH="$LIB_DIR" \
    "$HARNESS"
