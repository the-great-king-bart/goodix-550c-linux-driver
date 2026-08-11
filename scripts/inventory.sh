#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' 'Machine'
for field in sys_vendor product_name product_version bios_version; do
  path="/sys/class/dmi/id/${field}"
  if [[ -r "$path" ]]; then
    printf '%-18s %s\n' "$field" "$(tr -d '\n' < "$path")"
  fi
done

printf '\n%s\n' 'Operating system'
uname -a
sed -n 's/^PRETTY_NAME=//p' /etc/os-release

printf '\n%s\n' 'Target USB device'
lsusb -d 27c6:550c
lsusb -t | sed -n '/Port 006/p'

printf '\n%s\n' 'Fingerprint stack'
dpkg-query -W -f='${binary:Package}\t${Version}\n' fprintd 'libfprint*' 2>/dev/null || true
timeout 10s fprintd-list "$(id -un)" 2>&1 || true
