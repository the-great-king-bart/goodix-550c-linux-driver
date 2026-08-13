#!/usr/bin/env bash
# Fetch the two exact public source revisions used by every driver build.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM_ROOT="$PROJECT_ROOT/research/upstream"
OFFLINE=0
FETCH_TMP=''

usage() {
    printf '%s\n' \
        "Usage: $0 [--offline]" \
        '' \
        'Fetches only these public revisions below research/upstream/:' \
        '  brianporeilly/goodix-550c-driver a3de5a1b6174ace5db0bb2a8796c5be6e55428f0' \
        '  libfprint/libfprint             0c97a47d8ef405cd577b87058c1e89cae9d242e7' \
        '' \
        '--offline performs only the same origin/revision verification.'
}

while (($#)); do
    case "$1" in
        --offline)
            OFFLINE=1
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

for command in git install mktemp mv rm; do
    command -v "$command" >/dev/null || {
        printf 'Required command is unavailable: %s\n' "$command" >&2
        exit 1
    }
done
install -d -m 0755 "$UPSTREAM_ROOT"

cleanup() {
    if [[ -n "$FETCH_TMP" && -d "$FETCH_TMP" ]]; then
        case "$FETCH_TMP" in
            "$UPSTREAM_ROOT"/.fetch.*) rm -rf -- "$FETCH_TMP" ;;
        esac
    fi
}
trap cleanup EXIT INT TERM

verify_checkout() {
    local destination="$1"
    local url="$2"
    local commit="$3"
    local required_tag="$4"
    local actual_url
    local actual_commit
    local tag_commit

    [[ -d "$destination/.git" ]] || return 1
    actual_url="$(git -C "$destination" remote get-url origin 2>/dev/null || true)"
    actual_commit="$(git -C "$destination" rev-parse "$commit^{commit}" 2>/dev/null || true)"
    [[ "$actual_url" == "$url" && "$actual_commit" == "$commit" ]] || return 1
    if [[ -n "$required_tag" ]]; then
        tag_commit="$(git -C "$destination" rev-parse "$required_tag^{commit}" 2>/dev/null || true)"
        [[ "$tag_commit" == "$commit" ]] || return 1
    fi
}

fetch_checkout() {
    local name="$1"
    local url="$2"
    local fetch_ref="$3"
    local commit="$4"
    local required_tag="$5"
    local destination="$UPSTREAM_ROOT/$name"
    local fetched_commit

    if [[ -e "$destination" ]]; then
        if verify_checkout "$destination" "$url" "$commit" "$required_tag"; then
            printf 'Verified pinned upstream source: %s (%s)\n' "$destination" "$commit"
            return 0
        fi
        printf 'Refusing unexpected existing upstream directory: %s\n' "$destination" >&2
        return 1
    fi
    if ((OFFLINE)); then
        printf 'Pinned upstream source is unavailable offline: %s\n' "$destination" >&2
        return 1
    fi

    FETCH_TMP="$(mktemp -d "$UPSTREAM_ROOT/.fetch.$name.XXXXXX")"
    git -C "$FETCH_TMP" init --quiet
    git -C "$FETCH_TMP" remote add origin "$url"
    git -C "$FETCH_TMP" fetch --depth=1 --no-tags origin "$fetch_ref"
    fetched_commit="$(git -C "$FETCH_TMP" rev-parse 'FETCH_HEAD^{commit}')"
    if [[ "$fetched_commit" != "$commit" ]]; then
        printf 'Fetched revision mismatch for %s\n' "$name" >&2
        return 1
    fi
    git -C "$FETCH_TMP" checkout --quiet --detach "$commit"
    if [[ -n "$required_tag" ]]; then
        git -C "$FETCH_TMP" tag "$required_tag" "$commit"
    fi
    mv -- "$FETCH_TMP" "$destination"
    FETCH_TMP=''
    verify_checkout "$destination" "$url" "$commit" "$required_tag" || {
        printf 'Post-fetch verification failed for %s\n' "$destination" >&2
        return 1
    }
    printf 'Fetched pinned upstream source: %s (%s)\n' "$destination" "$commit"
}

fetch_checkout \
    goodix-550c-driver \
    'https://github.com/brianporeilly/goodix-550c-driver.git' \
    'a3de5a1b6174ace5db0bb2a8796c5be6e55428f0' \
    'a3de5a1b6174ace5db0bb2a8796c5be6e55428f0' \
    ''

fetch_checkout \
    libfprint \
    'https://gitlab.freedesktop.org/libfprint/libfprint.git' \
    'refs/tags/v1.94.10' \
    '0c97a47d8ef405cd577b87058c1e89cae9d242e7' \
    'v1.94.10'
