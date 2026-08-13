#!/usr/bin/env bash
# Validate TOD loading and fprintd's private D-Bus API with USB fully hidden.
# This script never invokes systemd, D-Bus activation, or a hardware-facing API.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE=''
RUN_DIR=''
UBUNTU_LIBFPRINT_VERSION='1:1.95.1+tod1-0ubuntu2'
UBUNTU_FPRINTD_VERSION='1.94.5-4'
BUS_PID=''
SANDBOX_PID=''
BUS_START=''
SANDBOX_START=''
SANDBOX_RUN_DIR='/run/goodix550c-tod-smoke'

usage() {
    printf '%s\n' \
        "Usage: $0 --module PATH [--run-dir PATH]" \
        '' \
        'Runs installed fprintd directly on a private non-activating D-Bus.' \
        'Bubblewrap supplies a fresh /dev and empty /sys and /run, so the' \
        'process cannot enumerate or open host USB devices.'
}

while (($#)); do
    case "$1" in
        --module)
            shift
            (($#)) || { printf 'Missing value for --module\n' >&2; exit 2; }
            MODULE="$1"
            ;;
        --run-dir)
            shift
            (($#)) || { printf 'Missing value for --run-dir\n' >&2; exit 2; }
            RUN_DIR="$1"
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

if [[ -z "$MODULE" ]]; then
    printf '%s\n' '--module is required' >&2
    exit 2
fi
if [[ -L "$MODULE" ]]; then
    printf 'Module path must not be a symlink\n' >&2
    exit 2
fi
MODULE="$(realpath -e "$MODULE")"
case "$MODULE" in
    "$PROJECT_ROOT"/build/*/libgoodix550c.so|"$PROJECT_ROOT"/build/*/*/libgoodix550c.so) ;;
    *)
        printf 'Module must be a libgoodix550c.so below project build/\n' >&2
        exit 2
        ;;
esac
if [[ ! -f "$MODULE" || -L "$MODULE" ]]; then
    printf 'Module must be a regular, non-symlink file\n' >&2
    exit 2
fi

mkdir -p "$PROJECT_ROOT/build"
if [[ -z "$RUN_DIR" ]]; then
    RUN_DIR="$(mktemp -d "$PROJECT_ROOT/build/tod-private-smoke.XXXXXX")"
else
    RUN_DIR="$(realpath -m "$RUN_DIR")"
    case "$RUN_DIR" in
        "$PROJECT_ROOT"/build/*) ;;
        *)
            printf 'Run directory must be below %s/build\n' "$PROJECT_ROOT" >&2
            exit 2
            ;;
    esac
    if [[ -e "$RUN_DIR" ]] && \
       [[ -n "$(find "$RUN_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
        printf 'Run directory is not empty: %s\n' "$RUN_DIR" >&2
        exit 2
    fi
    mkdir -p "$RUN_DIR"
fi
chmod 0700 "$RUN_DIR"

for command in bwrap chmod dbus-daemon dpkg-query find gdbus grep install kill \
    python3 readelf realpath sleep timeout; do
    command -v "$command" >/dev/null || {
        printf 'Required command is unavailable: %s\n' "$command" >&2
        exit 1
    }
done
if [[ ! -x /usr/libexec/fprintd ]]; then
    printf 'Installed fprintd executable is unavailable\n' >&2
    exit 1
fi

for package_name in libfprint-2-2 libfprint-2-tod1; do
    actual_version="$(dpkg-query -W -f='${Version}' "$package_name" 2>/dev/null || true)"
    if [[ "$actual_version" != "$UBUNTU_LIBFPRINT_VERSION" ]]; then
        printf 'Installed %s mismatch: got %s, expected %s\n' \
            "$package_name" "${actual_version:-missing}" "$UBUNTU_LIBFPRINT_VERSION" >&2
        exit 1
    fi
done
actual_fprintd="$(dpkg-query -W -f='${Version}' fprintd 2>/dev/null || true)"
if [[ "$actual_fprintd" != "$UBUNTU_FPRINTD_VERSION" ]]; then
    printf 'Installed fprintd mismatch: got %s, expected %s\n' \
        "${actual_fprintd:-missing}" "$UBUNTU_FPRINTD_VERSION" >&2
    exit 1
fi

dynamic="$(readelf --dynamic "$MODULE")"
if [[ "$dynamic" != *'Shared library: [libfprint-2.so.2]'* || \
      "$dynamic" != *'Shared library: [libfprint-2-tod.so.1]'* || \
      "$dynamic" == *'(RPATH)'* || "$dynamic" == *'(RUNPATH)'* ]]; then
    printf 'Module failed the smoke-test SONAME/RPATH preflight\n' >&2
    exit 1
fi

install -d -m 0700 \
    "$RUN_DIR/home" \
    "$RUN_DIR/logs" \
    "$RUN_DIR/modules" \
    "$RUN_DIR/runtime" \
    "$RUN_DIR/state" \
    "$RUN_DIR/tmp"
install -m 0644 "$MODULE" "$RUN_DIR/modules/libgoodix550c.so"

BUS_SOCKET="$RUN_DIR/bus"
if ((${#BUS_SOCKET} > 100)); then
    printf 'Private D-Bus socket path is too long: %s\n' "$BUS_SOCKET" >&2
    exit 2
fi
BUS_ADDRESS="unix:path=$(python3 -c \
    'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe="/"))' \
    "$BUS_SOCKET")"

python3 - \
    "$PROJECT_ROOT/tod/private-bus.conf" \
    "$RUN_DIR/private-bus.conf" \
    "$BUS_ADDRESS" <<'PY'
from __future__ import annotations

import html
import sys
from pathlib import Path

template_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
address = sys.argv[3]
template = template_path.read_text(encoding="utf-8")
if template.count("@BUS_ADDRESS@") != 1:
    raise SystemExit("private bus template must contain one address placeholder")
output_path.write_text(
    template.replace("@BUS_ADDRESS@", html.escape(address)),
    encoding="utf-8",
)
PY
chmod 0600 "$RUN_DIR/private-bus.conf"

ENV_EXE="$(realpath -e "$(command -v env)")"
DBUS_EXE="$(realpath -e "$(command -v dbus-daemon)")"
BWRAP_EXE="$(realpath -e "$(command -v bwrap)")"
FPRINTD_EXE="$(realpath -e /usr/libexec/fprintd)"

process_start_time() {
    local pid="$1"
    local stat_text
    local stat_rest
    local -a stat_fields=()
    [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/stat" ]] || return 1
    IFS= read -r stat_text < "/proc/$pid/stat" || return 1
    stat_rest="${stat_text##*) }"
    read -r -a stat_fields <<< "$stat_rest"
    ((${#stat_fields[@]} >= 20)) || return 1
    printf '%s' "${stat_fields[19]}"
}

recorded_child_is_live() {
    local pid="$1"
    local expected_start="$2"
    local kind="$3"
    local stat_text
    local stat_rest
    local current_exe
    local job_pid
    local owned=0
    local -a stat_fields=()

    [[ "$pid" =~ ^[1-9][0-9]*$ && "$expected_start" =~ ^[0-9]+$ ]] || return 1
    [[ -r "/proc/$pid/stat" && -e "/proc/$pid/exe" ]] || return 1
    IFS= read -r stat_text < "/proc/$pid/stat" || return 1
    stat_rest="${stat_text##*) }"
    read -r -a stat_fields <<< "$stat_rest"
    ((${#stat_fields[@]} >= 20)) || return 1
    [[ "${stat_fields[1]}" == "$$" && "${stat_fields[19]}" == "$expected_start" ]] || return 1

    while IFS= read -r job_pid; do
        if [[ "$job_pid" == "$pid" ]]; then
            owned=1
            break
        fi
    done < <(jobs -pr)
    ((owned == 1)) || return 1

    current_exe="$(realpath -e "/proc/$pid/exe")" || return 1
    case "$kind:$current_exe" in
        "bus:$ENV_EXE"|"bus:$DBUS_EXE"|\
        "sandbox:$ENV_EXE"|"sandbox:$BWRAP_EXE"|"sandbox:$FPRINTD_EXE")
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

stop_recorded_pid() {
    local pid="$1"
    local expected_start="$2"
    local kind="$3"
    local attempt
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 0
    if recorded_child_is_live "$pid" "$expected_start" "$kind"; then
        kill -TERM "$pid" 2>/dev/null || true
        for attempt in {1..40}; do
            recorded_child_is_live "$pid" "$expected_start" "$kind" || break
            sleep 0.05
        done
        if recorded_child_is_live "$pid" "$expected_start" "$kind"; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    fi
    wait "$pid" 2>/dev/null || true
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    stop_recorded_pid "$SANDBOX_PID" "$SANDBOX_START" sandbox
    stop_recorded_pid "$BUS_PID" "$BUS_START" bus
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

env -i \
    PATH=/usr/bin:/bin \
    HOME="$RUN_DIR/home" \
    TMPDIR="$RUN_DIR/tmp" \
    XDG_RUNTIME_DIR="$RUN_DIR/runtime" \
    dbus-daemon \
        --config-file="$RUN_DIR/private-bus.conf" \
        --nofork \
        --nopidfile \
        --nosyslog \
        >"$RUN_DIR/logs/dbus.log" 2>&1 &
BUS_PID=$!
BUS_START="$(process_start_time "$BUS_PID")" || {
    printf 'Cannot record private D-Bus child identity\n' >&2
    exit 1
}

for _ in {1..100}; do
    [[ -S "$BUS_SOCKET" ]] && break
    kill -0 "$BUS_PID" 2>/dev/null || {
        printf 'Private D-Bus exited during startup; see %s\n' \
            "$RUN_DIR/logs/dbus.log" >&2
        exit 1
    }
    sleep 0.05
done
if [[ ! -S "$BUS_SOCKET" ]]; then
    printf 'Private D-Bus socket did not appear\n' >&2
    exit 1
fi

sandbox_common=(
    --unshare-all
    --unshare-user
    --die-with-parent
    --new-session
    --disable-userns
    --cap-drop ALL
    --ro-bind / /
    --dev /dev
    --proc /proc
    --tmpfs /run
    --tmpfs /sys
    --tmpfs /tmp
    --dir "$SANDBOX_RUN_DIR"
    --bind "$RUN_DIR" "$SANDBOX_RUN_DIR"
    --tmpfs "$PROJECT_ROOT"
)

# This assertion executes inside the same mount policy used for fprintd.
env -i PATH=/usr/bin:/bin bwrap "${sandbox_common[@]}" \
    /bin/sh -eu -c \
    'test ! -e /dev/bus/usb
     test ! -e /sys/bus/usb/devices
     test ! -e /run/udev
     test ! -e "$1/.env"
     test ! -e "$1/research/secrets"
     test -r "$2/modules/libgoodix550c.so"' \
    sandbox-check "$PROJECT_ROOT" "$SANDBOX_RUN_DIR"

SANDBOX_BUS_ADDRESS="unix:path=$SANDBOX_RUN_DIR/bus"

env -i PATH=/usr/bin:/bin bwrap "${sandbox_common[@]}" \
    --setenv PATH /usr/bin:/bin \
    --setenv HOME "$SANDBOX_RUN_DIR/home" \
    --setenv TMPDIR /tmp \
    --setenv XDG_RUNTIME_DIR "$SANDBOX_RUN_DIR/runtime" \
    --setenv STATE_DIRECTORY "$SANDBOX_RUN_DIR/state" \
    --setenv DBUS_SYSTEM_BUS_ADDRESS "$SANDBOX_BUS_ADDRESS" \
    --setenv FP_TOD_DRIVERS_DIR "$SANDBOX_RUN_DIR/modules" \
    --setenv FP_DRIVERS_ALLOWLIST goodix53x5 \
    --setenv G_MESSAGES_DEBUG all \
    /usr/libexec/fprintd --no-timeout \
    >"$RUN_DIR/logs/fprintd.log" 2>&1 &
SANDBOX_PID=$!
SANDBOX_START="$(process_start_time "$SANDBOX_PID")" || {
    printf 'Cannot record private fprintd child identity\n' >&2
    exit 1
}

introspection="$RUN_DIR/logs/introspection.txt"
introspection_errors="$RUN_DIR/logs/introspection-errors.log"
ready=0
for _ in {1..40}; do
    kill -0 "$SANDBOX_PID" 2>/dev/null || break
    if env -i PATH=/usr/bin:/bin HOME="$RUN_DIR/home" \
        XDG_RUNTIME_DIR="$RUN_DIR/runtime" \
        timeout 0.5s gdbus introspect \
            --address "$BUS_ADDRESS" \
            --dest net.reactivated.Fprint \
            --object-path /net/reactivated/Fprint/Manager \
            >"$introspection" 2>>"$introspection_errors"; then
        ready=1
        break
    fi
    sleep 0.05
done
if ((ready == 0)); then
    printf 'Private fprintd did not export its Manager; see %s\n' \
        "$RUN_DIR/logs/fprintd.log" >&2
    exit 1
fi

devices="$(
    env -i PATH=/usr/bin:/bin HOME="$RUN_DIR/home" \
        XDG_RUNTIME_DIR="$RUN_DIR/runtime" \
        timeout 5s gdbus call \
            --address "$BUS_ADDRESS" \
            --dest net.reactivated.Fprint \
            --object-path /net/reactivated/Fprint/Manager \
            --method net.reactivated.Fprint.Manager.GetDevices
)"
printf '%s\n' "$devices" > "$RUN_DIR/logs/get-devices.txt"
if [[ "$devices" != *'[]'* ]]; then
    printf 'No-USB sandbox unexpectedly reported a fingerprint device\n' >&2
    exit 1
fi

if ! grep -Fq "Opening driver $SANDBOX_RUN_DIR/modules/libgoodix550c.so" \
        "$RUN_DIR/logs/fprintd.log" || \
   ! grep -Fq 'Loading driver goodix53x5 (' "$RUN_DIR/logs/fprintd.log"; then
    printf 'Installed libfprint did not log successful loading of the TOD module\n' >&2
    exit 1
fi

printf 'Private no-USB fprintd/TOD smoke passed\n'
printf 'GetDevices returned an empty object-path array\n'
printf 'Smoke logs: %s\n' "$RUN_DIR/logs"
