#!/usr/bin/env bash
# Build the fail-closed Goodix 550c driver as an external Ubuntu TOD module.
# This script never installs, activates fprintd, or accesses a USB device.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER_REPO="$PROJECT_ROOT/research/upstream/goodix-550c-driver"
DRIVER_COMMIT='a3de5a1b6174ace5db0bb2a8796c5be6e55428f0'
SOURCE_DATE_EPOCH='1784248643'
UBUNTU_VERSION='1:1.95.1+tod1-0ubuntu2'
UBUNTU_ARCH='amd64'
SDK_DIR="$PROJECT_ROOT/build/ubuntu-libfprint-tod-sdk"
STAGE_DIR=''
PREPARE_ONLY=0
OFFLINE_SDK=0
VOLATILE_INIT=false
MANUAL_FDT_POLL=false

usage() {
    printf '%s\n' \
        "Usage: $0 [OPTIONS]" \
        '' \
        'Options:' \
        '  --prepare-only         patch/copy/audit sources, but do not compile' \
        '  --offline-sdk          require already cached, verified Ubuntu packages' \
        '  --allow-volatile-init  compile volatile init; runtime gate remains required' \
        '  --allow-manual-fdt-poll compile manual FDT polling; runtime gate remains required' \
        '  --sdk-dir PATH         project-local extracted SDK location' \
        '  --stage-dir PATH       empty output directory below project build/' \
        '' \
        'The default build keeps volatile initialization compiled out.'
}

while (($#)); do
    case "$1" in
        --prepare-only)
            PREPARE_ONLY=1
            ;;
        --offline-sdk)
            OFFLINE_SDK=1
            ;;
        --allow-volatile-init)
            VOLATILE_INIT=true
            ;;
        --allow-manual-fdt-poll)
            MANUAL_FDT_POLL=true
            ;;
        --sdk-dir)
            shift
            (($#)) || { printf 'Missing value for --sdk-dir\n' >&2; exit 2; }
            SDK_DIR="$1"
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

if [[ ! -d "$DRIVER_REPO/.git" ]]; then
    printf '%s\n' \
        'Pinned driver source is missing.' \
        'Run scripts/fetch_upstream_sources.sh first.' >&2
    exit 1
fi

for command in awk cc dpkg dpkg-query find git install meson ninja nm patch \
    pkg-config python3 readelf realpath sha256sum tar; do
    command -v "$command" >/dev/null || {
        printf 'Required command is unavailable: %s\n' "$command" >&2
        exit 1
    }
done

mkdir -p "$PROJECT_ROOT/build"
SDK_DIR="$(realpath -m "$SDK_DIR")"
case "$SDK_DIR" in
    "$PROJECT_ROOT"/build/*|"$PROJECT_ROOT"/research/*) ;;
    *)
        printf 'SDK directory must be below this project\n' >&2
        exit 2
        ;;
esac

if [[ -z "$STAGE_DIR" ]]; then
    STAGE_DIR="$(mktemp -d "$PROJECT_ROOT/build/goodix550c-tod.XXXXXX")"
else
    STAGE_DIR="$(realpath -m "$STAGE_DIR")"
    case "$STAGE_DIR" in
        "$PROJECT_ROOT"/build/*) ;;
        *)
            printf 'Stage directory must be below %s/build\n' "$PROJECT_ROOT" >&2
            exit 2
            ;;
    esac
    if [[ -e "$STAGE_DIR" ]] && \
       [[ -n "$(find "$STAGE_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
        printf 'Stage directory is not empty: %s\n' "$STAGE_DIR" >&2
        exit 2
    fi
    mkdir -p "$STAGE_DIR"
fi

actual_arch="$(dpkg --print-architecture)"
if [[ "$actual_arch" != "$UBUNTU_ARCH" ]]; then
    printf 'Unsupported host architecture: got %s, expected %s\n' \
        "$actual_arch" "$UBUNTU_ARCH" >&2
    exit 1
fi
for package_name in libfprint-2-2 libfprint-2-tod1; do
    actual_version="$(dpkg-query -W -f='${Version}' "$package_name" 2>/dev/null || true)"
    if [[ "$actual_version" != "$UBUNTU_VERSION" ]]; then
        printf 'Installed %s mismatch: got %s, expected %s\n' \
            "$package_name" "${actual_version:-missing}" "$UBUNTU_VERSION" >&2
        exit 1
    fi
done

sdk_args=(--sdk-dir "$SDK_DIR")
if ((OFFLINE_SDK)); then
    sdk_args+=(--offline)
fi
"$PROJECT_ROOT/scripts/fetch_ubuntu_tod_sdk.sh" "${sdk_args[@]}"
SDK_ROOT="$SDK_DIR/root"

driver_head="$(git -C "$DRIVER_REPO" rev-parse HEAD)"
if [[ "$driver_head" != "$DRIVER_COMMIT" ]]; then
    printf 'Pinned driver mismatch: got %s, expected %s\n' \
        "$driver_head" "$DRIVER_COMMIT" >&2
    exit 1
fi
commit_epoch="$(git -C "$DRIVER_REPO" show -s --format=%ct "$DRIVER_COMMIT")"
if [[ "$commit_epoch" != "$SOURCE_DATE_EPOCH" ]]; then
    printf 'Pinned driver timestamp mismatch\n' >&2
    exit 1
fi

PATCH_TREE="$STAGE_DIR/source/patched-driver"
MODULE_SOURCE="$STAGE_DIR/source/module"
BUILD_DIR="$STAGE_DIR/builddir"
mkdir -p "$PATCH_TREE" "$MODULE_SOURCE"
git -C "$DRIVER_REPO" archive "$DRIVER_COMMIT" | tar -x -C "$PATCH_TREE"

while IFS= read -r patch_name || [[ -n "$patch_name" ]]; do
    [[ -z "$patch_name" || "$patch_name" == \#* ]] && continue
    case "$patch_name" in
        */*|*..*)
            printf 'Invalid driver patch name in driver-series: %s\n' "$patch_name" >&2
            exit 1
            ;;
    esac
    patch_file="$PROJECT_ROOT/patches/goodix-550c/$patch_name"
    if [[ ! -f "$patch_file" ]]; then
        printf 'Driver patch listed but absent: %s\n' "$patch_name" >&2
        exit 1
    fi
    patch --batch --forward -d "$PATCH_TREE" -p1 < "$patch_file"
done < "$PROJECT_ROOT/patches/goodix-550c/driver-series"

while IFS= read -r source_name || [[ -n "$source_name" ]]; do
    [[ -z "$source_name" || "$source_name" == \#* ]] && continue
    case "$source_name" in
        drivers/goodix53x5/*|sigfm/*) ;;
        *)
            printf 'Invalid TOD source allowlist entry: %s\n' "$source_name" >&2
            exit 1
            ;;
    esac
    if [[ "$source_name" == *..* || ! -f "$PATCH_TREE/$source_name" ]]; then
        printf 'Missing or unsafe allowlisted source: %s\n' "$source_name" >&2
        exit 1
    fi
    install -D -m 0644 "$PATCH_TREE/$source_name" "$MODULE_SOURCE/$source_name"
done < "$PROJECT_ROOT/tod/goodix550c-sources.txt"

python3 "$PROJECT_ROOT/scripts/verify_goodix550c_tod_module.py" \
    --source-root "$MODULE_SOURCE" \
    --sdk-root "$SDK_ROOT"

printf 'Prepared fail-closed TOD module sources: %s\n' "$MODULE_SOURCE"
if ((PREPARE_ONLY)); then
    exit 0
fi

CORE_LIBRARY='/usr/lib/x86_64-linux-gnu/libfprint-2.so.2'
TOD_LIBRARY='/usr/lib/x86_64-linux-gnu/libfprint-2-tod.so.1'
for runtime_library in "$CORE_LIBRARY" "$TOD_LIBRARY"; do
    if [[ ! -f "$runtime_library" ]]; then
        printf 'Exact installed runtime library is unavailable: %s\n' \
            "$runtime_library" >&2
        exit 1
    fi
done

export SOURCE_DATE_EPOCH
export CFLAGS=''
export CXXFLAGS=''
export LDFLAGS=''

meson setup "$BUILD_DIR" "$PROJECT_ROOT/tod" \
    --wrap-mode=nodownload \
    --buildtype=debugoptimized \
    -Ddriver_source_root="$MODULE_SOURCE" \
    -Dsdk_root="$SDK_ROOT" \
    -Dcore_library="$CORE_LIBRARY" \
    -Dtod_library="$TOD_LIBRARY" \
    -Dgoodix550c_volatile_init="$VOLATILE_INIT" \
    -Dgoodix550c_manual_fdt_poll="$MANUAL_FDT_POLL"
meson compile -C "$BUILD_DIR"

MODULE="$BUILD_DIR/libgoodix550c.so"
HARNESS="$BUILD_DIR/goodix550c-tod-open-close"
ENROLL_HARNESS="$BUILD_DIR/goodix550c-tod-enroll"

# Link the physical harness to the installed Ubuntu core and TOD SONAMEs.  It
# has no RPATH/RUNPATH, so the guarded runner cannot redirect it to a build-tree
# core library.  The project-local module itself is selected at runtime through
# FP_TOD_DRIVERS_DIR.
cc -std=c11 -O2 -g -Wall -Wextra -Werror \
    -fPIE -pie -D_FORTIFY_SOURCE=3 -fstack-protector-strong \
    "-ffile-prefix-map=$PROJECT_ROOT=/usr/src/goodix550c-project" \
    "-fdebug-prefix-map=$PROJECT_ROOT=/usr/src/goodix550c-project" \
    "-ffile-prefix-map=$SDK_ROOT=/usr/src/ubuntu-libfprint-tod-sdk" \
    "-fdebug-prefix-map=$SDK_ROOT=/usr/src/ubuntu-libfprint-tod-sdk" \
    -isystem "$SDK_ROOT/usr/include/libfprint-2" \
    $(pkg-config --cflags glib-2.0 gio-2.0) \
    "$PROJECT_ROOT/tools/goodix550c_tod_open_close.c" \
    -Wl,-z,relro,-z,now,-z,noexecstack \
    -Wl,--no-as-needed "$CORE_LIBRARY" "$TOD_LIBRARY" -Wl,--as-needed \
    $(pkg-config --libs glib-2.0 gio-2.0) \
    -o "$HARNESS"

# The enrollment harness is built identically. It is the only harness that
# reaches the finger-wait states, and it discards its template rather than
# storing one.
cc -std=c11 -O2 -g -Wall -Wextra -Werror \
    -fPIE -pie -D_FORTIFY_SOURCE=3 -fstack-protector-strong \
    "-ffile-prefix-map=$PROJECT_ROOT=/usr/src/goodix550c-project" \
    "-fdebug-prefix-map=$PROJECT_ROOT=/usr/src/goodix550c-project" \
    "-ffile-prefix-map=$SDK_ROOT=/usr/src/ubuntu-libfprint-tod-sdk" \
    "-fdebug-prefix-map=$SDK_ROOT=/usr/src/ubuntu-libfprint-tod-sdk" \
    -isystem "$SDK_ROOT/usr/include/libfprint-2" \
    $(pkg-config --cflags glib-2.0 gio-2.0 gobject-2.0) \
    "$PROJECT_ROOT/tools/goodix550c_tod_enroll.c" \
    -Wl,-z,relro,-z,now,-z,noexecstack \
    -Wl,--no-as-needed "$CORE_LIBRARY" "$TOD_LIBRARY" -Wl,--as-needed \
    $(pkg-config --libs glib-2.0 gio-2.0 gobject-2.0) \
    -o "$ENROLL_HARNESS"

python3 "$PROJECT_ROOT/scripts/verify_goodix550c_tod_module.py" \
    --source-root "$MODULE_SOURCE" \
    --sdk-root "$SDK_ROOT" \
    --module "$MODULE" \
    --harness "$HARNESS" \
    --enroll-harness "$ENROLL_HARNESS" \
    --core-library "$CORE_LIBRARY" \
    --tod-library "$TOD_LIBRARY" \
    --build-dir "$BUILD_DIR" \
    --expected-volatile-init "$VOLATILE_INIT" \
    --expected-manual-fdt-poll "$MANUAL_FDT_POLL"

cc -std=c11 -Wall -Wextra -Werror \
    "-ffile-prefix-map=$PROJECT_ROOT=/usr/src/goodix550c-project" \
    "-fdebug-prefix-map=$PROJECT_ROOT=/usr/src/goodix550c-project" \
    -I"$MODULE_SOURCE/drivers/goodix53x5" \
    "$PROJECT_ROOT/tests/native/test_goodix550c_tls.c" \
    "$MODULE_SOURCE/drivers/goodix53x5/goodix53x5-tls.c" \
    $(pkg-config --cflags --libs gio-2.0 openssl) \
    -o "$BUILD_DIR/test-goodix550c-tls"
"$BUILD_DIR/test-goodix550c-tls"

cc -std=c11 -Wall -Wextra -Werror -Wno-unused-parameter \
    -ffunction-sections -fdata-sections \
    -D_GNU_SOURCE -DGOODIX550C_ENABLE_MANUAL_FDT_POLL=1 \
    "-ffile-prefix-map=$PROJECT_ROOT=/usr/src/goodix550c-project" \
    "-fdebug-prefix-map=$PROJECT_ROOT=/usr/src/goodix550c-project" \
    -I"$MODULE_SOURCE/drivers/goodix53x5" \
    -isystem "$SDK_ROOT/usr/include/libfprint-2" \
    -isystem "$SDK_ROOT/usr/include/libfprint-2/tod-1" \
    "$PROJECT_ROOT/tests/native/test_goodix550c_manual_fdt.c" \
    "$MODULE_SOURCE/drivers/goodix53x5/goodix53x5-calibration.c" \
    $(pkg-config --cflags --libs gio-2.0 gusb openssl) \
    -Wl,--gc-sections \
    -o "$BUILD_DIR/test-goodix550c-manual-fdt"
"$BUILD_DIR/test-goodix550c-manual-fdt"

module_sha="$(sha256sum "$MODULE" | awk '{print $1}')"
harness_sha="$(sha256sum "$HARNESS" | awk '{print $1}')"
enroll_harness_sha="$(sha256sum "$ENROLL_HARNESS" | awk '{print $1}')"
source_checksums="$STAGE_DIR/source-checksums.sha256"
(
    cd "$PROJECT_ROOT"
    sha256sum \
        patches/goodix-550c/driver-series \
        scripts/build_goodix550c_tod_module.sh \
        scripts/fetch_ubuntu_tod_sdk.sh \
        scripts/verify_goodix550c_tod_module.py \
        tests/native/test_goodix550c_manual_fdt.c \
        tools/goodix550c_tod_open_close.c \
        tools/goodix550c_tod_enroll.c \
        tod/goodix550c-sources.txt \
        tod/goodix550c-tod-entry.c \
        tod/goodix550c-tod.map \
        tod/meson.build \
        tod/meson_options.txt
    while IFS= read -r patch_name || [[ -n "$patch_name" ]]; do
        [[ -z "$patch_name" || "$patch_name" == \#* ]] && continue
        sha256sum "patches/goodix-550c/$patch_name"
    done < patches/goodix-550c/driver-series
) > "$source_checksums"
source_checksums_sha="$(sha256sum "$source_checksums" | awk '{print $1}')"
metadata="$STAGE_DIR/build-metadata.txt"
{
    printf 'driver_commit=%s\n' "$DRIVER_COMMIT"
    printf 'source_date_epoch=%s\n' "$SOURCE_DATE_EPOCH"
    printf 'expected_usb_id=27c6:550c\n'
    printf 'expected_firmware=GF5288_GM168SEC_APP_13021\n'
    printf 'ubuntu_version=%s\n' "$UBUNTU_VERSION"
    printf 'ubuntu_arch=%s\n' "$UBUNTU_ARCH"
    printf 'volatile_init=%s\n' "$VOLATILE_INIT"
    printf 'manual_fdt_poll=%s\n' "$MANUAL_FDT_POLL"
    printf 'module_sha256=%s\n' "$module_sha"
    printf 'harness_sha256=%s\n' "$harness_sha"
    printf 'enroll_harness_sha256=%s\n' "$enroll_harness_sha"
    printf 'source_checksums_sha256=%s\n' "$source_checksums_sha"
} > "$metadata"

printf 'Built and verified TOD module: %s\n' "$MODULE"
printf 'Module SHA-256: %s\n' "$module_sha"
printf 'Built installed-runtime harness: %s\n' "$HARNESS"
printf 'Harness SHA-256: %s\n' "$harness_sha"
printf 'Built enrollment harness: %s\n' "$ENROLL_HARNESS"
printf 'Enrollment harness SHA-256: %s\n' "$enroll_harness_sha"
printf 'Project-relative source checksums: %s\n' "$source_checksums"
printf 'Build metadata: %s\n' "$metadata"
