#!/usr/bin/env bash
# Prepare and optionally compile the fail-closed Goodix 550c driver against
# upstream libfprint v1.94.10.  This script never installs or runs the driver.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER_REPO="$PROJECT_ROOT/research/upstream/goodix-550c-driver"
LIBFPRINT_REPO="$PROJECT_ROOT/research/upstream/libfprint"
DRIVER_COMMIT="a3de5a1b6174ace5db0bb2a8796c5be6e55428f0"
LIBFPRINT_COMMIT="0c97a47d8ef405cd577b87058c1e89cae9d242e7"
LIBFPRINT_REF="v1.94.10"
PREPARE_ONLY=0
VOLATILE_INIT=false
STAGE_DIR=""

usage() {
    printf '%s\n' \
        "Usage: $0 [--prepare-only] [--allow-volatile-init] [--stage-dir PATH]" \
        "" \
        "Outputs only below $PROJECT_ROOT/build/." \
        "--allow-volatile-init compiles the volatile path, but runtime still" \
        "requires GOODIX550C_ALLOW_VOLATILE_INIT=1. Persistent writes stay denied."
}

while (($#)); do
    case "$1" in
        --prepare-only)
            PREPARE_ONLY=1
            ;;
        --allow-volatile-init)
            VOLATILE_INIT=true
            ;;
        --stage-dir)
            shift
            (($#)) || { printf 'Missing value for --stage-dir\n' >&2; exit 2; }
            STAGE_DIR="$1"
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

mkdir -p "$PROJECT_ROOT/build"
if [[ -z "$STAGE_DIR" ]]; then
    STAGE_DIR="$(mktemp -d "$PROJECT_ROOT/build/goodix550c-v1.94.10.XXXXXX")"
else
    STAGE_DIR="$(realpath -m "$STAGE_DIR")"
    case "$STAGE_DIR" in
        "$PROJECT_ROOT"/build/*) ;;
        *)
            printf 'Stage directory must be below %s/build\n' "$PROJECT_ROOT" >&2
            exit 2
            ;;
    esac
    if [[ -e "$STAGE_DIR" ]] && [[ -n "$(find "$STAGE_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
        printf 'Stage directory is not empty: %s\n' "$STAGE_DIR" >&2
        exit 2
    fi
    mkdir -p "$STAGE_DIR"
fi

driver_head="$(git -C "$DRIVER_REPO" rev-parse HEAD)"
if [[ "$driver_head" != "$DRIVER_COMMIT" ]]; then
    printf 'Pinned driver mismatch: got %s, expected %s\n' "$driver_head" "$DRIVER_COMMIT" >&2
    exit 1
fi

libfprint_head="$(git -C "$LIBFPRINT_REPO" rev-parse "$LIBFPRINT_REF^{commit}")"
if [[ "$libfprint_head" != "$LIBFPRINT_COMMIT" ]]; then
    printf 'Pinned libfprint mismatch: got %s, expected %s\n' \
        "$libfprint_head" "$LIBFPRINT_COMMIT" >&2
    exit 1
fi

DRIVER_STAGE="$STAGE_DIR/source/driver"
LIBFPRINT_STAGE="$STAGE_DIR/source/libfprint"
BUILD_DIR="$STAGE_DIR/builddir"
PREFIX_DIR="$STAGE_DIR/prefix"
mkdir -p "$DRIVER_STAGE" "$LIBFPRINT_STAGE"

git -C "$DRIVER_REPO" archive "$DRIVER_COMMIT" | tar -x -C "$DRIVER_STAGE"
git -C "$LIBFPRINT_REPO" archive "$LIBFPRINT_COMMIT" | tar -x -C "$LIBFPRINT_STAGE"

patch --batch --forward -d "$DRIVER_STAGE" -p1 \
    < "$PROJECT_ROOT/patches/goodix-550c/0001-goodix53x5-add-fail-closed-550c-policy.patch"
patch --batch --forward -d "$DRIVER_STAGE" -p1 \
    < "$PROJECT_ROOT/patches/goodix-550c/0003-goodix53x5-harden-secret-loading.patch"
patch --batch --forward -d "$LIBFPRINT_STAGE" -p1 \
    < "$PROJECT_ROOT/patches/goodix-550c/0002-libfprint-v1.94.10-integration.patch"

driver_files=(
    goodix53x5-auth.c goodix53x5-auth.h
    goodix53x5-calibration.c goodix53x5-calibration.h
    goodix53x5-commands.c goodix53x5-commands.h
    goodix53x5-crypto.c goodix53x5-crypto.h
    goodix53x5-enroll.c goodix53x5-enroll.h
    goodix53x5-image.c goodix53x5-image.h
    goodix53x5-match.c goodix53x5-match.h
    goodix53x5-private.h
    goodix53x5-proto.c goodix53x5-proto.h
    goodix53x5-safety.h
    goodix53x5-scan.c goodix53x5-scan.h
    goodix53x5-session.c goodix53x5-session.h
    goodix53x5-tls.c goodix53x5-tls.h
    goodix53x5-transport.c goodix53x5-transport.h
    goodix53x5.c goodix53x5.h
)
sigfm_files=(binary.hpp img-info.hpp sigfm.cpp sigfm.hpp)
integrated_driver="$LIBFPRINT_STAGE/libfprint/drivers/goodix53x5"
integrated_sigfm="$LIBFPRINT_STAGE/libfprint/sigfm"
mkdir -p "$integrated_driver" "$integrated_sigfm"

for name in "${driver_files[@]}"; do
    install -m 0644 "$DRIVER_STAGE/drivers/goodix53x5/$name" "$integrated_driver/$name"
done
for name in "${sigfm_files[@]}"; do
    install -m 0644 "$DRIVER_STAGE/sigfm/$name" "$integrated_sigfm/$name"
done

python3 "$PROJECT_ROOT/scripts/verify_goodix550c_patch.py" \
    --driver-tree "$DRIVER_STAGE" \
    --libfprint-tree "$LIBFPRINT_STAGE"

printf 'Prepared fail-closed source tree: %s\n' "$LIBFPRINT_STAGE"
if ((PREPARE_ONLY)); then
    exit 0
fi

meson setup "$BUILD_DIR" "$LIBFPRINT_STAGE" \
    --prefix="$PREFIX_DIR" \
    --wrap-mode=nodownload \
    --buildtype=debugoptimized \
    -Ddrivers=goodix53x5 \
    -Dgoodix550c_volatile_init="$VOLATILE_INIT" \
    -Dudev_hwdb=disabled \
    -Dudev_rules=disabled \
    -Dintrospection=false \
    -Dinstalled-tests=false \
    -Ddoc=false

meson compile -C "$BUILD_DIR"

cc -std=c11 -Wall -Wextra -Werror \
    -I"$DRIVER_STAGE/drivers/goodix53x5" \
    "$PROJECT_ROOT/tests/native/test_goodix550c_tls.c" \
    "$DRIVER_STAGE/drivers/goodix53x5/goodix53x5-tls.c" \
    $(pkg-config --cflags --libs gio-2.0 openssl) \
    -o "$BUILD_DIR/test-goodix550c-tls"
"$BUILD_DIR/test-goodix550c-tls"

# Build the minimal exact-device harness against this build tree.  Its RUNPATH is
# relative to itself, so it cannot silently fall back to the host libfprint.
cc -std=c11 -Wall -Wextra -Werror \
    -I"$BUILD_DIR" \
    -I"$LIBFPRINT_STAGE" \
    -I"$BUILD_DIR/libfprint" \
    -I"$LIBFPRINT_STAGE/libfprint" \
    $(pkg-config --cflags glib-2.0 gio-2.0) \
    "$PROJECT_ROOT/tools/goodix550c_open_close.c" \
    -L"$BUILD_DIR/libfprint" \
    '-Wl,-rpath,$ORIGIN/libfprint' \
    -lfprint-2 \
    $(pkg-config --libs glib-2.0 gio-2.0) \
    -o "$BUILD_DIR/goodix550c-open-close"

printf 'Build completed without installing or running the driver: %s\n' "$BUILD_DIR"
