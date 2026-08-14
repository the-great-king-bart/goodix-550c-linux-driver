#!/usr/bin/env python3
"""Print the /dev/bus/usb node of the exact 27c6:550c sensor, or nothing.

Kept as its own file rather than inlined in the shell: a here-document nested
inside a command substitution is easy to get subtly wrong, and the shell caller
runs under ``set -o pipefail`` where a partially consumed pipeline fails the
assignment silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

VENDOR = "27c6"
PRODUCT = "550c"
USB_DEVICES = Path("/sys/bus/usb/devices")


def read_attribute(base: Path, name: str) -> str | None:
    try:
        return (base / name).read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return None


def find_node() -> str | None:
    try:
        entries = sorted(USB_DEVICES.iterdir())
    except OSError:
        return None

    for base in entries:
        if read_attribute(base, "idVendor") != VENDOR:
            continue
        if read_attribute(base, "idProduct") != PRODUCT:
            continue

        busnum = read_attribute(base, "busnum")
        devnum = read_attribute(base, "devnum")
        if busnum is None or devnum is None:
            continue
        try:
            return f"/dev/bus/usb/{int(busnum):03d}/{int(devnum):03d}"
        except ValueError:
            continue

    return None


def main() -> int:
    node = find_node()
    if node is None:
        return 1
    print(node)
    return 0


if __name__ == "__main__":
    sys.exit(main())
