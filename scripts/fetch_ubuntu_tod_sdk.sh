#!/usr/bin/env bash
# Fetch and extract the exact Ubuntu 26.04 libfprint TOD development packages.
# All persistent output stays below this repository's build/ or research/ tree.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="$PROJECT_ROOT/build/ubuntu-libfprint-tod-sdk"
OFFLINE=0

UBUNTU_VERSION='1:1.95.1+tod1-0ubuntu2'
UBUNTU_ARCH='amd64'
BASE_URL='https://archive.ubuntu.com/ubuntu/pool/main/libf/libfprint'

package_names=(
    libfprint-2-dev
    libfprint-2-tod-dev
)
package_files=(
    'libfprint-2-dev_1.95.1+tod1-0ubuntu2_amd64.deb'
    'libfprint-2-tod-dev_1.95.1+tod1-0ubuntu2_amd64.deb'
)
package_sha256=(
    'db01d591d13f81312d618d2e247ceed66d72ee42cd07130b19637425df32ee28'
    'd1f99081a0d314b7416bdc9d5d70bea47237787dea946a95d8de3e21bb0b8b91'
)

usage() {
    printf '%s\n' \
        "Usage: $0 [--offline] [--sdk-dir PATH]" \
        '' \
        "Fetches only the pinned public Ubuntu packages and extracts them below:" \
        "  $PROJECT_ROOT/build/ubuntu-libfprint-tod-sdk" \
        '' \
        '--offline refuses network access and requires verified cached packages.'
}

while (($#)); do
    case "$1" in
        --offline)
            OFFLINE=1
            ;;
        --sdk-dir)
            shift
            (($#)) || { printf 'Missing value for --sdk-dir\n' >&2; exit 2; }
            SDK_DIR="$1"
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

SDK_DIR="$(realpath -m "$SDK_DIR")"
case "$SDK_DIR" in
    "$PROJECT_ROOT"/build/*|"$PROJECT_ROOT"/research/*) ;;
    *)
        printf 'SDK directory must be below %s/build or %s/research\n' \
            "$PROJECT_ROOT" "$PROJECT_ROOT" >&2
        exit 2
        ;;
esac

for command in curl dpkg-deb realpath sha256sum; do
    command -v "$command" >/dev/null || {
        printf 'Required command is unavailable: %s\n' "$command" >&2
        exit 1
    }
done

DOWNLOAD_DIR="$SDK_DIR/downloads"
SYSROOT="$SDK_DIR/root"
install -d -m 0755 "$DOWNLOAD_DIR"

partial_file=''
extract_tmp=''
cleanup() {
    if [[ -n "$partial_file" && -e "$partial_file" ]]; then
        rm -f -- "$partial_file"
    fi
    if [[ -n "$extract_tmp" && -d "$extract_tmp" ]]; then
        case "$extract_tmp" in
            "$SDK_DIR"/root.tmp.*) rm -rf -- "$extract_tmp" ;;
        esac
    fi
}
trap cleanup EXIT INT TERM

for index in "${!package_files[@]}"; do
    package_name="${package_names[$index]}"
    package_file="${package_files[$index]}"
    expected_sha="${package_sha256[$index]}"
    destination="$DOWNLOAD_DIR/$package_file"

    actual_sha=''
    if [[ -f "$destination" ]]; then
        actual_sha="$(sha256sum "$destination" | awk '{print $1}')"
    fi

    if [[ "$actual_sha" != "$expected_sha" ]]; then
        if ((OFFLINE)); then
            printf 'Missing or invalid offline package: %s\n' "$destination" >&2
            exit 1
        fi
        partial_file="$destination.partial.$$"
        rm -f -- "$partial_file"
        curl \
            --fail \
            --location \
            --proto '=https' \
            --tlsv1.2 \
            --retry 3 \
            --retry-all-errors \
            --output "$partial_file" \
            "$BASE_URL/$package_file"
        actual_sha="$(sha256sum "$partial_file" | awk '{print $1}')"
        if [[ "$actual_sha" != "$expected_sha" ]]; then
            printf 'SHA-256 mismatch for %s\n' "$package_file" >&2
            exit 1
        fi
        mv -f -- "$partial_file" "$destination"
        partial_file=''
    fi

    actual_name="$(dpkg-deb --field "$destination" Package)"
    actual_version="$(dpkg-deb --field "$destination" Version)"
    actual_arch="$(dpkg-deb --field "$destination" Architecture)"
    if [[ "$actual_name" != "$package_name" || \
          "$actual_version" != "$UBUNTU_VERSION" || \
          "$actual_arch" != "$UBUNTU_ARCH" ]]; then
        printf 'Unexpected package metadata in %s\n' "$package_file" >&2
        exit 1
    fi
done

extract_tmp="$SDK_DIR/root.tmp.$$"
rm -rf -- "$extract_tmp"
install -d -m 0755 "$extract_tmp"
for package_file in "${package_files[@]}"; do
    dpkg-deb --extract "$DOWNLOAD_DIR/$package_file" "$extract_tmp"
done

required_paths=(
    'usr/include/libfprint-2/fprint.h'
    'usr/include/libfprint-2/tod-1/drivers_api.h'
    'usr/include/libfprint-2/tod-1/fpi-device.h'
    'usr/include/libfprint-2/tod-1/fpi-print.h'
    'usr/include/libfprint-2/tod-1/fpi-ssm.h'
    'usr/include/libfprint-2/tod-1/fpi-usb-transfer.h'
    'usr/lib/x86_64-linux-gnu/pkgconfig/libfprint-2-tod-1.pc'
)
for relative_path in "${required_paths[@]}"; do
    if [[ ! -f "$extract_tmp/$relative_path" ]]; then
        printf 'Pinned SDK is missing required path: %s\n' "$relative_path" >&2
        exit 1
    fi
done

if [[ -e "$SYSROOT" ]]; then
    case "$SYSROOT" in
        "$SDK_DIR"/root) rm -rf -- "$SYSROOT" ;;
        *) printf 'Refusing to replace unexpected sysroot path\n' >&2; exit 1 ;;
    esac
fi
mv -- "$extract_tmp" "$SYSROOT"
extract_tmp=''

manifest="$SDK_DIR/SHA256SUMS"
{
    for index in "${!package_files[@]}"; do
        printf '%s  %s\n' "${package_sha256[$index]}" "${package_files[$index]}"
    done
} >"$manifest"

printf 'Verified Ubuntu TOD SDK: %s\n' "$SYSROOT"
printf 'Pinned package version: %s (%s)\n' "$UBUNTU_VERSION" "$UBUNTU_ARCH"
