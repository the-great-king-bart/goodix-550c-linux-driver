#!/usr/bin/env bash
# Run the project-local Goodix 550c TOD module under a private fprintd so the
# standard fprintd client tools can enrol, verify, list and delete fingerprints.
#
# Unlike the no-USB smoke, this one deliberately exposes the sensor, and unlike
# every harness in this project it deliberately *stores* templates: that is the
# point of it. Storage stays project-local and is never installed system-wide,
# the host fprintd is never started or stopped, and no persistent sensor write
# is possible because the module's fail-closed policy is unchanged.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_DRIVER_COMMIT='a3de5a1b6174ace5db0bb2a8796c5be6e55428f0'
EXPECTED_FIRMWARE='GF5288_GM168SEC_APP_13021'
EXPECTED_UBUNTU_VERSION='1:1.95.1+tod1-0ubuntu2'
EXPECTED_FPRINTD_VERSION='1.94.5-4'
STATE_ROOT="$PROJECT_ROOT/build/goodix550c-fprintd-state"
STAGE_DIR=''
PSK_FILE=''
VOLATILE_ACK=0
MANUAL_FDT_ACK=0
ACTION=''
FINGER='right-index-finger'
BUS_PID=''
SANDBOX_PID=''
POLKIT_PID=''
SANDBOX_RUN_DIR='/run/goodix550c-fprintd'

usage() {
    printf '%s\n' \
        "Usage: sudo $0 --stage-dir BUILD_STAGE --psk-file SECRET_FILE \\" \
        '  --allow-volatile-init --allow-manual-fdt-poll \\' \
        '  (--gui | --enroll | --verify | --list | --delete) [--finger NAME]' \
        '' \
        'Starts the installed fprintd on a private, non-activating D-Bus with' \
        'only the project-local TOD module loaded, then runs the matching' \
        'fprintd client tool against it. The host fprintd service is never' \
        'started, stopped, or contacted.' \
        '' \
        'Templates are stored under build/goodix550c-fprintd-state/prints,' \
        'which is git-ignored. This is the one part of the project that keeps' \
        'fingerprint data; delete that directory to remove every template.' \
        '' \
        '--gui opens a window instead, which is the only mode whose prompts are\nvisible when somebody other than the operator starts the session.'
}

while (($#)); do
    case "$1" in
        --stage-dir)
            shift; (($#)) || { printf 'Missing value for --stage-dir\n' >&2; exit 2; }
            STAGE_DIR="$1" ;;
        --psk-file)
            shift; (($#)) || { printf 'Missing value for --psk-file\n' >&2; exit 2; }
            PSK_FILE="$1" ;;
        --allow-volatile-init) VOLATILE_ACK=1 ;;
        --allow-manual-fdt-poll) MANUAL_FDT_ACK=1 ;;
        --enroll) ACTION=enroll ;;
        --verify) ACTION=verify ;;
        --list) ACTION=list ;;
        --gui) ACTION=gui ;;
        --delete) ACTION=delete ;;
        --finger)
            shift; (($#)) || { printf 'Missing value for --finger\n' >&2; exit 2; }
            FINGER="$1" ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if ((EUID != 0)); then
    printf 'Refusing: opening the sensor requires running this through sudo.\n' >&2
    exit 2
fi
if ((VOLATILE_ACK != 1 || MANUAL_FDT_ACK != 1)); then
    printf 'Refusing: both --allow-volatile-init and --allow-manual-fdt-poll are required.\n' >&2
    exit 2
fi
if [[ -z "$STAGE_DIR" || -z "$PSK_FILE" || -z "$ACTION" ]]; then
    usage >&2
    exit 2
fi
case "$FINGER" in
    left-thumb|left-index-finger|left-middle-finger|left-ring-finger|left-little-finger|\
    right-thumb|right-index-finger|right-middle-finger|right-ring-finger|right-little-finger) ;;
    *) printf 'Refusing unrecognised finger name: %s\n' "$FINGER" >&2; exit 2 ;;
esac

for command in bwrap chmod chown dbus-daemon dpkg-query find fuser gdbus id \
    install python3 realpath setpriv sha256sum stdbuf systemctl timeout; do
    command -v "$command" >/dev/null || {
        printf 'Required command is unavailable: %s\n' "$command" >&2
        exit 1
    }
done
if [[ "$ACTION" == gui ]]; then
    CLIENT="$PROJECT_ROOT/tools/goodix550c_gui.py"
    [[ -f "$CLIENT" ]] || { printf 'Missing GUI: %s\n' "$CLIENT" >&2; exit 1; }
else
    CLIENT="/usr/bin/fprintd-$ACTION"
    [[ -x "$CLIENT" ]] || { printf 'Missing fprintd client: %s\n' "$CLIENT" >&2; exit 1; }
fi
[[ -x /usr/libexec/fprintd ]] || { printf 'Installed fprintd is unavailable\n' >&2; exit 1; }

for supplied in "$STAGE_DIR" "$PSK_FILE"; do
    [[ -L "$supplied" ]] && { printf 'Refusing a symlink argument: %s\n' "$supplied" >&2; exit 2; }
done
STAGE_DIR="$(realpath -e "$STAGE_DIR")"
PSK_FILE="$(realpath -e "$PSK_FILE")"
case "$STAGE_DIR" in "$PROJECT_ROOT"/build/*) ;;
    *) printf 'Refusing stage outside project build/.\n' >&2; exit 2 ;; esac
case "$PSK_FILE" in "$PROJECT_ROOT"/research/secrets/*) ;;
    *) printf 'Refusing PSK outside project research/secrets/.\n' >&2; exit 2 ;; esac

MODULE="$STAGE_DIR/builddir/libgoodix550c.so"
METADATA="$STAGE_DIR/build-metadata.txt"
SOURCE_CHECKSUMS="$STAGE_DIR/source-checksums.sha256"
for regular in "$MODULE" "$METADATA" "$SOURCE_CHECKSUMS" "$PSK_FILE"; do
    [[ -f "$regular" && ! -L "$regular" ]] || {
        printf 'Refusing missing, non-regular, or symlink input: %s\n' "$regular" >&2
        exit 2
    }
done

metadata_value() {
    local key="$1"
    local -a values=()
    mapfile -t values < <(awk -F= -v key="$key" '$1 == key {print substr($0, length(key) + 2)}' "$METADATA")
    ((${#values[@]} == 1)) || return 1
    printf '%s' "${values[0]}"
}
if [[ "$(metadata_value driver_commit || true)" != "$EXPECTED_DRIVER_COMMIT" || \
      "$(metadata_value expected_firmware || true)" != "$EXPECTED_FIRMWARE" || \
      "$(metadata_value expected_usb_id || true)" != '27c6:550c' || \
      "$(metadata_value ubuntu_version || true)" != "$EXPECTED_UBUNTU_VERSION" || \
      "$(metadata_value volatile_init || true)" != true || \
      "$(metadata_value manual_fdt_poll || true)" != true ]]; then
    printf 'Refusing: TOD build metadata does not match the exact live profile.\n' >&2
    exit 2
fi
if [[ "$(metadata_value module_sha256 || true)" != "$(sha256sum "$MODULE" | awk '{print $1}')" ]]; then
    printf 'Refusing: module digest does not match its build metadata.\n' >&2
    exit 2
fi
(cd "$PROJECT_ROOT" && sha256sum --check --strict --quiet "$SOURCE_CHECKSUMS") || {
    printf 'Refusing: audited sources changed since this stage was built.\n' >&2
    exit 2
}

actual_libfprint="$(dpkg-query -W -f='${Version}' libfprint-2-tod1 2>/dev/null || true)"
actual_fprintd="$(dpkg-query -W -f='${Version}' fprintd 2>/dev/null || true)"
if [[ "$actual_libfprint" != "$EXPECTED_UBUNTU_VERSION" || \
      "$actual_fprintd" != "$EXPECTED_FPRINTD_VERSION" ]]; then
    printf 'Refusing: installed libfprint/fprintd versions are not the validated pair.\n' >&2
    exit 2
fi

fprintd_state="$(systemctl is-active fprintd.service 2>/dev/null || true)"
if [[ "$fprintd_state" != inactive && "$fprintd_state" != failed ]]; then
    printf 'Refusing: host fprintd.service is %s; this tool never displaces it.\n' \
        "$fprintd_state" >&2
    exit 2
fi
# Resolved in a single pass. A pipeline ending in head(1) would SIGPIPE its
# producer and, under pipefail, fail this assignment with no message at all.
usb_node="$(python3 "$PROJECT_ROOT/scripts/find_goodix550c_usb_node.py")"
if [[ -z "$usb_node" ]]; then
    printf 'Refusing: no 27c6:550c USB node was found.\n' >&2
    exit 2
fi
if fuser -s "$usb_node" 2>/dev/null; then
    printf 'Refusing: another process already holds %s.\n' "$usb_node" >&2
    exit 2
fi

DESKTOP_USER="${SUDO_USER:-}"
if [[ -z "$DESKTOP_USER" ]]; then
    printf 'Refusing: SUDO_USER is unset, so there is no account to enrol for.\n' >&2
    exit 2
fi
DESKTOP_UID="$(id -u "$DESKTOP_USER")"
DESKTOP_GID="$(id -g "$DESKTOP_USER")"

install -d -m 0700 "$STATE_ROOT" "$STATE_ROOT/prints"
RUN_DIR="$(mktemp -d "$PROJECT_ROOT/build/goodix550c-fprintd-run.XXXXXX")"
chmod 0700 "$RUN_DIR"
install -d -m 0700 "$RUN_DIR/home" "$RUN_DIR/logs" "$RUN_DIR/modules" \
    "$RUN_DIR/runtime" "$RUN_DIR/tmp"
install -m 0644 "$MODULE" "$RUN_DIR/modules/libgoodix550c.so"
install -m 0600 "$PSK_FILE" "$RUN_DIR/goodix550c.psk"
# The window runs as the desktop user, and handing over a root-owned socket
# after the fact was not enough for it to complete the D-Bus handshake. Own the
# bus from the start instead; the root daemon can still connect to it.
# Reachable by exactly the two parties of this session and nobody else: the
# desktop user owns it, and the daemon gets in through the group bits as root.
# That avoids both a world-accessible socket and granting the sandbox
# CAP_DAC_OVERRIDE.
install -d -o "$DESKTOP_USER" -g root -m 0710 "$RUN_DIR/bus.d"
chmod 0711 "$RUN_DIR"

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    [[ -n "$SANDBOX_PID" ]] && kill -TERM "$SANDBOX_PID" 2>/dev/null || true
    [[ -n "$POLKIT_PID" ]] && kill -TERM "$POLKIT_PID" 2>/dev/null || true
    [[ -n "$BUS_PID" ]] && kill -TERM "$BUS_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    # The run directory is removed below, so keep the logs where a failure can
    # still be read afterwards.
    if [[ -d "$RUN_DIR/logs" ]]; then
        install -d -m 0700 "$STATE_ROOT/logs"
        cp -a "$RUN_DIR/logs/." "$STATE_ROOT/logs/" 2>/dev/null || true
    fi
    case "$RUN_DIR" in
        "$PROJECT_ROOT"/build/goodix550c-fprintd-run.??????) rm -rf -- "$RUN_DIR" ;;
    esac
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

BUS_SOCKET="$RUN_DIR/bus.d/bus"
BUS_ADDRESS="unix:path=$(python3 -c \
    'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe="/"))' \
    "$BUS_SOCKET")"
python3 - "$PROJECT_ROOT/tod/app-bus.conf" "$RUN_DIR/private-bus.conf" \
    "$BUS_ADDRESS" <<'PY'
import html, sys
from pathlib import Path

template = Path(sys.argv[1]).read_text(encoding="utf-8")
if template.count("@BUS_ADDRESS@") != 1:
    raise SystemExit("private bus template must contain one address placeholder")
Path(sys.argv[2]).write_text(
    template.replace("@BUS_ADDRESS@", html.escape(sys.argv[3])), encoding="utf-8"
)
PY
chmod 0600 "$RUN_DIR/private-bus.conf"

chown "$DESKTOP_USER" "$RUN_DIR/private-bus.conf"
# setpriv rather than runuser: runuser opens a PAM session and tears it down
# again, which killed the bus with "Session terminated, killing shell".
setpriv --reuid="$DESKTOP_UID" --regid="$DESKTOP_GID" --init-groups \
    env -i PATH=/usr/bin:/bin \
    HOME="/home/$DESKTOP_USER" TMPDIR=/tmp \
    XDG_RUNTIME_DIR="/run/user/$DESKTOP_UID" \
    dbus-daemon --config-file="$RUN_DIR/private-bus.conf" --nofork --nopidfile \
        --nosyslog >"$RUN_DIR/logs/dbus.log" 2>&1 &
BUS_PID=$!
for _ in {1..100}; do
    [[ -S "$BUS_SOCKET" ]] && break
    kill -0 "$BUS_PID" 2>/dev/null || {
        printf 'Private D-Bus exited during startup; see %s\n' "$RUN_DIR/logs/dbus.log" >&2
        exit 1
    }
    sleep 0.05
done
[[ -S "$BUS_SOCKET" ]] || { printf 'Private D-Bus socket did not appear\n' >&2; exit 1; }
# Both sides of this session must reach the socket: the daemon runs as root
# under bubblewrap, which drops the capability that would let it ignore the
# owning user's mode bits, and the window runs as the desktop user.
chown "$DESKTOP_USER":root "$BUS_SOCKET"
chmod 0660 "$BUS_SOCKET"

# fprintd asks PolicyKit before every device method, and this private bus has no
# polkit on it. The stub answers only net.reactivated.fprint.* actions and binds
# only to this per-run socket; physical presence has already been established by
# running under sudo and touching the sensor.
env -i PATH=/usr/bin:/bin HOME="$RUN_DIR/home" \
    python3 "$PROJECT_ROOT/scripts/goodix550c_private_polkit.py" \
        --bus-address "$BUS_ADDRESS" \
        >"$RUN_DIR/logs/polkit.log" 2>&1 &
POLKIT_PID=$!
for _ in {1..100}; do
    grep -Fq 'private polkit stub ready' "$RUN_DIR/logs/polkit.log" 2>/dev/null && break
    kill -0 "$POLKIT_PID" 2>/dev/null || {
        printf 'Private polkit stub exited during startup; see %s\n' \
            "$RUN_DIR/logs/polkit.log" >&2
        exit 1
    }
    sleep 0.05
done
if ! grep -Fq 'private polkit stub ready' "$RUN_DIR/logs/polkit.log" 2>/dev/null; then
    printf 'Private polkit stub did not become ready; see %s\n' \
        "$RUN_DIR/logs/polkit.log" >&2
    exit 1
fi

# The sensor is exposed on purpose here, so /dev/bus/usb and sysfs are bound
# rather than masked. The project tree is still hidden apart from the module and
# the PSK copy, so a fault in fprintd cannot read .env or research/secrets.
# No --unshare-user here: this runs as root, and inside a fresh user namespace
# bwrap loses the privilege to traverse the operator's home directory, so it
# cannot resolve the run directory it is being asked to bind. Mount, PID, IPC
# and UTS isolation are kept.
env -i PATH=/usr/bin:/bin bwrap \
    --unshare-ipc --unshare-pid --unshare-uts \
    --die-with-parent --new-session \
    --ro-bind / / \
    --dev /dev \
    --dev-bind /dev/bus/usb /dev/bus/usb \
    --ro-bind /sys /sys \
    --proc /proc \
    --tmpfs /run \
    --tmpfs /tmp \
    --dir "$SANDBOX_RUN_DIR" \
    --bind "$RUN_DIR" "$SANDBOX_RUN_DIR" \
    --bind "$STATE_ROOT/prints" /var/lib/fprint \
    --tmpfs "$PROJECT_ROOT" \
    --setenv PATH /usr/bin:/bin \
    --setenv HOME "$SANDBOX_RUN_DIR/home" \
    --setenv TMPDIR /tmp \
    --setenv XDG_RUNTIME_DIR "$SANDBOX_RUN_DIR/runtime" \
    --setenv DBUS_SYSTEM_BUS_ADDRESS "unix:path=$SANDBOX_RUN_DIR/bus.d/bus" \
    --setenv FP_TOD_DRIVERS_DIR "$SANDBOX_RUN_DIR/modules" \
    --setenv FP_DRIVERS_ALLOWLIST goodix53x5 \
    --setenv GOODIX550C_ALLOW_VOLATILE_INIT 1 \
    --setenv GOODIX550C_ALLOW_MANUAL_FDT_POLL 1 \
    --setenv GOODIX550C_PSK_FILE "$SANDBOX_RUN_DIR/goodix550c.psk" \
    --setenv G_MESSAGES_DEBUG all \
    /usr/libexec/fprintd --no-timeout \
    >"$RUN_DIR/logs/fprintd.log" 2>&1 &
SANDBOX_PID=$!

ready=0
for _ in {1..200}; do
    kill -0 "$SANDBOX_PID" 2>/dev/null || break
    if env -i PATH=/usr/bin:/bin HOME="$RUN_DIR/home" \
        timeout 0.5s gdbus introspect --address "$BUS_ADDRESS" \
            --dest net.reactivated.Fprint \
            --object-path /net/reactivated/Fprint/Manager >/dev/null 2>&1; then
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
    env -i PATH=/usr/bin:/bin HOME="$RUN_DIR/home" timeout 5s gdbus call \
        --address "$BUS_ADDRESS" --dest net.reactivated.Fprint \
        --object-path /net/reactivated/Fprint/Manager \
        --method net.reactivated.Fprint.Manager.GetDevices
)"
if [[ "$devices" == *'[]'* ]]; then
    printf 'Private fprintd found no fingerprint device; see %s\n' \
        "$RUN_DIR/logs/fprintd.log" >&2
    exit 1
fi

printf 'Private fprintd is up with the project-local TOD module.\n'
printf 'Templates live in %s\n' "$STATE_ROOT/prints"
printf 'Follow the prompts below; they come from fprintd itself.\n\n'

if [[ "$ACTION" == gui ]]; then
    printf 'Opening the window on the %s desktop session.\n' "$DESKTOP_USER"
    set +e
    setpriv --reuid="$DESKTOP_UID" --regid="$DESKTOP_GID" --init-groups env \
        DISPLAY="${DISPLAY:-:0}" \
        WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" \
        XDG_RUNTIME_DIR="/run/user/$DESKTOP_UID" \
        XDG_SESSION_TYPE="${XDG_SESSION_TYPE:-wayland}" \
        python3 "$CLIENT" --bus-address "$BUS_ADDRESS" --username "$DESKTOP_USER"
    client_status=$?
    set -e
    printf '\nWindow closed with status %d\n' "$client_status"
    printf 'Daemon log: %s\n' "$RUN_DIR/logs/fprintd.log"
    exit "$client_status"
fi

client_args=()
case "$ACTION" in
    enroll|verify) client_args=(-f "$FINGER" "$DESKTOP_USER") ;;
    delete|list)   client_args=("$DESKTOP_USER") ;;
esac

# stdbuf keeps each prompt visible as it happens rather than held in a block
# buffer until exit, which matters when this runs with its output redirected.
set +e
env -i PATH=/usr/bin:/bin HOME="$RUN_DIR/home" TERM="${TERM:-dumb}" \
    DBUS_SYSTEM_BUS_ADDRESS="$BUS_ADDRESS" \
    stdbuf -oL -eL "$CLIENT" "${client_args[@]}"
client_status=$?
set -e

printf '\nfprintd-%s exited with status %d\n' "$ACTION" "$client_status"
printf 'Daemon log: %s\n' "$RUN_DIR/logs/fprintd.log"
if [[ -n "${GOODIX550C_KEEP_LOG:-}" ]]; then
    install -m 0600 "$RUN_DIR/logs/fprintd.log" "$STATE_ROOT/last-fprintd.log"
    printf 'Copied to %s\n' "$STATE_ROOT/last-fprintd.log"
fi
exit "$client_status"
