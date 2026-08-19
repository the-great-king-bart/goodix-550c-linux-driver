#!/usr/bin/env bash
# Install the project-local Goodix 550c TOD module for the host fprintd, so the
# sensor can be used by the desktop and, optionally, at the login prompt.
#
# Every other harness in this project deliberately avoids touching the system:
# they run a private fprintd on a private bus and keep templates under build/.
# This one is the opposite by design, and it is therefore split into stages that
# can be taken and reversed independently:
#
#   --install-driver   host fprintd can use the sensor. Login is NOT changed.
#   --enable-login     pam_fprintd becomes a login factor. Reversible.
#   --disable-login    removes the login factor, leaves the driver installed.
#   --uninstall        removes everything this script installed.
#   --status           reports what is currently installed.
#
# The staging exists so the driver can be proven against the real host service
# with fprintd-verify before it is anywhere near an authentication prompt.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_DRIVER_COMMIT='a3de5a1b6174ace5db0bb2a8796c5be6e55428f0'
EXPECTED_FIRMWARE='GF5288_GM168SEC_APP_13021'
EXPECTED_UBUNTU_VERSION='1:1.95.1+tod1-0ubuntu2'
EXPECTED_FPRINTD_VERSION='1.94.5-4'

TOD_DIR='/usr/lib/x86_64-linux-gnu/libfprint-2/tod-1'
INSTALLED_MODULE="$TOD_DIR/libgoodix550c.so"
SECRET_DIR='/etc/goodix550c'
INSTALLED_PSK="$SECRET_DIR/goodix550c.psk"
DROPIN_DIR='/etc/systemd/system/fprintd.service.d'
DROPIN="$DROPIN_DIR/10-goodix550c.conf"
SYSTEM_PRINTS='/var/lib/fprint'
STATE_PRINTS="$PROJECT_ROOT/build/goodix550c-fprintd-state/prints"
PAM_CONFIG='/usr/share/pam-configs/fprintd'

STAGE_DIR=''
PSK_FILE=''
VOLATILE_ACK=0
MANUAL_FDT_ACK=0
ACTION=''
CONFIRM_LOGIN=0
REPLACE_PRINTS=0
PURGE_PRINTS=0

usage() {
    printf '%s\n' \
        "Usage: sudo $0 [--stage-dir DIR --psk-file FILE" \
        '        --allow-volatile-init --allow-manual-fdt-poll]' \
        '  (--install-driver | --enable-login | --disable-login |' \
        '   --uninstall | --status)' \
        '' \
        'Stages:' \
        '  --install-driver  Copy the module, PSK and service environment into' \
        '                    place and migrate enrolled templates to' \
        "                    $SYSTEM_PRINTS. The login prompt is not touched." \
        '                    Requires --stage-dir, --psk-file and both' \
        '                    --allow-* acknowledgements.' \
        '  --enable-login    Add pam_fprintd as a login factor. Requires' \
        '                    --yes-enable-login. Password stays available.' \
        '  --disable-login   Remove the login factor.' \
        '  --uninstall       Remove module, PSK and service environment. Add' \
        '                    --purge-prints to also erase enrolled templates.' \
        '  --status          Report what is installed. Needs no other flags.' \
        '  --harden-store    Re-tighten template files to root-only 0600.' \
        '                    fprintd writes each new one at 0644, so run this' \
        '                    after every enrollment.' \
        '' \
        'Options:' \
        '  --replace-prints    Overwrite templates already in the system store.' \
        '  --purge-prints      With --uninstall, erase the system template store.' \
        '  --yes-enable-login  Required acknowledgement for --enable-login.'
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
        --install-driver) ACTION=install-driver ;;
        --harden-store) ACTION=harden-store ;;
        --enable-login) ACTION=enable-login ;;
        --disable-login) ACTION=disable-login ;;
        --uninstall) ACTION=uninstall ;;
        --status) ACTION=status ;;
        --yes-enable-login) CONFIRM_LOGIN=1 ;;
        --replace-prints) REPLACE_PRINTS=1 ;;
        --purge-prints) PURGE_PRINTS=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

[[ -n "$ACTION" ]] || { usage >&2; exit 2; }
if ((EUID != 0)); then
    printf 'Refusing: installing for the host fprintd requires running through sudo.\n' >&2
    exit 2
fi

# ========================================================================
# Status
# ========================================================================

# fprintd creates each template with mode 0644. The directory above them is
# 0700 root-only, so nothing can reach them today, but a stored fingerprint
# should not be one chmod away from being world-readable. Re-runnable, because
# every new enrollment writes a fresh file at the loose mode again.
harden_store() {
    [[ -d "$SYSTEM_PRINTS" ]] || {
        printf 'No template store at %s.\n' "$SYSTEM_PRINTS"
        return 0
    }
    chown -R root:root "$SYSTEM_PRINTS"
    find "$SYSTEM_PRINTS" -type d -exec chmod 0700 {} +
    find "$SYSTEM_PRINTS" -type f -exec chmod 0600 {} +
    printf 'Tightened %s to root-only.\n' "$SYSTEM_PRINTS"
}

report_status() {
    local pam_state='disabled'
    local loose=0
    local total=0

    # Everything below is written as `if` blocks rather than `cmd && var=...`
    # on purpose. Under `set -e` a failing left-hand side aborts the whole
    # function, and under `set -o pipefail` a `find` on a missing directory
    # fails the pipeline even though `wc` succeeds. Reporting is the one thing
    # that has to work when nothing is installed, which is exactly when those
    # paths are absent.
    if grep -qs 'pam_fprintd' /etc/pam.d/common-auth; then
        pam_state='ENABLED'
    fi
    if [[ -d "$SYSTEM_PRINTS" ]]; then
        total="$(find "$SYSTEM_PRINTS" -type f | wc -l)"
        # Any group or other bit at all, rather than "not exactly 0600": a
        # stricter mode such as 0400 is not a finding.
        loose="$(find "$SYSTEM_PRINTS" -type f -perm /077 | wc -l)"
    fi

    printf 'TOD module:      %s\n' \
        "$([[ -f "$INSTALLED_MODULE" ]] && printf '%s' "$INSTALLED_MODULE" || printf 'not installed')"
    printf 'PSK:             %s\n' \
        "$([[ -f "$INSTALLED_PSK" ]] && printf '%s' "$INSTALLED_PSK" || printf 'not installed')"
    printf 'Service env:     %s\n' \
        "$([[ -f "$DROPIN" ]] && printf '%s' "$DROPIN" || printf 'not installed')"
    printf 'Templates:       %s in %s\n' "$total" "$SYSTEM_PRINTS"
    if [[ "$loose" != 0 ]]; then
        printf 'Template modes:  %s file(s) readable beyond root -- run --harden-store\n' "$loose"
    else
        printf 'Template modes:  root-only\n'
    fi
    printf 'Login factor:    %s\n' "$pam_state"
    printf 'fprintd.service: %s\n' "$(systemctl is-active fprintd.service 2>/dev/null || true)"
}

if [[ "$ACTION" == status ]]; then
    report_status
    exit 0
fi

# ========================================================================
# Driver install
# ========================================================================

validate_stage() {
    for command in dpkg-query find install sha256sum systemctl; do
        command -v "$command" >/dev/null || {
            printf 'Required command is unavailable: %s\n' "$command" >&2
            exit 1
        }
    done

    if ((VOLATILE_ACK != 1 || MANUAL_FDT_ACK != 1)); then
        printf 'Refusing: both --allow-volatile-init and --allow-manual-fdt-poll are required.\n' >&2
        exit 2
    fi
    if [[ -z "$STAGE_DIR" || -z "$PSK_FILE" ]]; then
        printf 'Refusing: --stage-dir and --psk-file are required to install the driver.\n' >&2
        exit 2
    fi

    for supplied in "$STAGE_DIR" "$PSK_FILE"; do
        [[ -L "$supplied" ]] && {
            printf 'Refusing a symlink argument: %s\n' "$supplied" >&2
            exit 2
        }
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

    local actual_libfprint actual_fprintd
    actual_libfprint="$(dpkg-query -W -f='${Version}' libfprint-2-tod1 2>/dev/null || true)"
    actual_fprintd="$(dpkg-query -W -f='${Version}' fprintd 2>/dev/null || true)"
    if [[ "$actual_libfprint" != "$EXPECTED_UBUNTU_VERSION" || \
          "$actual_fprintd" != "$EXPECTED_FPRINTD_VERSION" ]]; then
        printf 'Refusing: installed libfprint/fprintd versions are not the validated pair.\n' >&2
        exit 2
    fi

    # A running daemon would keep the old module mapped and hold the sensor.
    local fprintd_state
    fprintd_state="$(systemctl is-active fprintd.service 2>/dev/null || true)"
    if [[ "$fprintd_state" != inactive && "$fprintd_state" != failed ]]; then
        printf 'Refusing: fprintd.service is %s. Stop it first: systemctl stop fprintd.service\n' \
            "$fprintd_state" >&2
        exit 2
    fi
}

migrate_prints() {
    local -a stored=()

    [[ -d "$STATE_PRINTS" ]] || return 0
    mapfile -t stored < <(find "$STATE_PRINTS" -mindepth 1 -maxdepth 1 -type d -printf '%f\n')
    ((${#stored[@]})) || return 0

    install -d -m 0700 -o root -g root "$SYSTEM_PRINTS"
    for user in "${stored[@]}"; do
        if [[ -e "$SYSTEM_PRINTS/$user" ]] && ((REPLACE_PRINTS != 1)); then
            printf 'Keeping existing templates for %s; pass --replace-prints to overwrite.\n' "$user"
            continue
        fi
        rm -rf -- "${SYSTEM_PRINTS:?}/$user"
        cp -a -- "$STATE_PRINTS/$user" "$SYSTEM_PRINTS/$user"
        chown -R root:root "$SYSTEM_PRINTS/$user"
        printf 'Migrated %s template(s) for %s\n' \
            "$(find "$SYSTEM_PRINTS/$user" -type f | wc -l)" "$user"
    done
}

install_driver() {
    validate_stage

    install -d -m 0755 -o root -g root "$TOD_DIR"
    install -m 0644 -o root -g root "$MODULE" "$INSTALLED_MODULE"

    # The PSK is the sensor's session secret. Root-only, and never world- or
    # group-readable, because fprintd is the only thing that needs it.
    install -d -m 0700 -o root -g root "$SECRET_DIR"
    install -m 0600 -o root -g root "$PSK_FILE" "$INSTALLED_PSK"

    # Both gates default off in the module; the host service has to opt in the
    # same way every project harness does, and the allowlist keeps the host
    # daemon from probing unrelated hardware with an untested driver set.
    install -d -m 0755 -o root -g root "$DROPIN_DIR"
    # Written by redirect rather than `install /dev/stdin`: under sudo the
    # heredoc's /dev/stdin does not reliably resolve, and install(1) then fails
    # after the module is already in place.
    cat > "$DROPIN" <<EOF
# Installed by scripts/goodix550c_install_system.sh. Remove with --uninstall.
[Service]
Environment=FP_DRIVERS_ALLOWLIST=goodix53x5
Environment=GOODIX550C_ALLOW_VOLATILE_INIT=1
Environment=GOODIX550C_ALLOW_MANUAL_FDT_POLL=1
Environment=GOODIX550C_PSK_FILE=$INSTALLED_PSK
EOF
    chown root:root "$DROPIN"
    chmod 0644 "$DROPIN"

    migrate_prints
    harden_store
    systemctl daemon-reload

    printf '\nDriver installed. The login prompt is unchanged.\n'
    printf 'Prove it against the host service before going further:\n'
    printf '    fprintd-list %s\n' "${SUDO_USER:-\$USER}"
    printf '    fprintd-verify\n'
    printf 'Then, only if that works:  sudo "%s" --enable-login --yes-enable-login\n' "$0"
}

# ========================================================================
# Login factor
# ========================================================================

enable_login() {
    [[ -f "$INSTALLED_MODULE" ]] || {
        printf 'Refusing: the driver is not installed. Run --install-driver first.\n' >&2
        exit 2
    }
    command -v pam-auth-update >/dev/null || {
        printf 'Refusing: pam-auth-update is unavailable.\n' >&2
        exit 1
    }
    if ((CONFIRM_LOGIN != 1)); then
        printf 'Refusing: --enable-login needs --yes-enable-login.\n' >&2
        exit 2
    fi

    # The whole reason a failed finger cannot lock anybody out is that Ubuntu
    # ships this stanza with `default=ignore`, so PAM falls through to the next
    # primary module (the password). If a future package drops that, enabling
    # the factor could make a broken sensor fatal, so check rather than assume.
    if ! grep -q 'default=ignore' "$PAM_CONFIG" 2>/dev/null; then
        printf 'Refusing: %s does not fall through on failure.\n' "$PAM_CONFIG" >&2
        printf 'Enabling it could make a sensor fault block login entirely.\n' >&2
        exit 2
    fi

    DEBIAN_FRONTEND=noninteractive pam-auth-update --enable fprintd

    printf '\nFingerprint is now a login factor. The password still works:\n'
    printf 'the PAM stanza ignores a failed or absent finger and falls through.\n'
    printf 'Undo at any time with:  sudo "%s" --disable-login\n' "$0"
}

disable_login() {
    command -v pam-auth-update >/dev/null || {
        printf 'Refusing: pam-auth-update is unavailable.\n' >&2
        exit 1
    }
    DEBIAN_FRONTEND=noninteractive pam-auth-update --disable fprintd
    printf 'Fingerprint removed as a login factor.\n'
}

# ========================================================================
# Uninstall
# ========================================================================

uninstall_all() {
    if grep -qs 'pam_fprintd' /etc/pam.d/common-auth; then
        disable_login
    fi

    systemctl stop fprintd.service 2>/dev/null || true
    rm -f -- "$INSTALLED_MODULE" "$INSTALLED_PSK" "$DROPIN"
    # TOD_DIR is included because this script creates it: libfprint-2-tod1 ships
    # only the shared library, not the drivers directory. --ignore-fail-on-non-empty
    # leaves it alone if another TOD driver is installed beside ours.
    rmdir --ignore-fail-on-non-empty -- \
        "$SECRET_DIR" "$DROPIN_DIR" "$TOD_DIR" 2>/dev/null || true
    systemctl daemon-reload

    if ((PURGE_PRINTS == 1)); then
        rm -rf -- "${SYSTEM_PRINTS:?}"
        printf 'Erased %s\n' "$SYSTEM_PRINTS"
    else
        printf 'Left templates in %s; pass --purge-prints to erase them.\n' "$SYSTEM_PRINTS"
    fi
    printf 'Uninstalled.\n'
}

case "$ACTION" in
    install-driver) install_driver ;;
    harden-store) harden_store ;;
    enable-login) enable_login ;;
    disable-login) disable_login ;;
    uninstall) uninstall_all ;;
esac
