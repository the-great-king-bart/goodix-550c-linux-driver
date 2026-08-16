#!/usr/bin/env python3
"""Minimal PolicyKit1 Authority that grants fprintd actions on a private bus.

fprintd asks PolicyKit before every device method. The private, non-activating
bus this project starts for the guarded fprintd session has no polkit on it, so
without this stub every call fails with ServiceUnknown and nothing can be
enrolled or verified.

Scope, deliberately narrow:

* it binds only to the bus address given on the command line, and refuses
  the system bus socket by path, so it cannot become the authority for the
  whole host;
* it answers only ``net.reactivated.fprint.*`` actions and returns
  not-authorized for anything else, so it cannot be used to wave through some
  unrelated privileged action if it is ever pointed at a wider bus; and
* it is started and killed by the session wrapper and dies with it.

The operator has already proven physical presence by running the wrapper under
sudo and touching the sensor, which is what the real polkit rule for these
actions checks for.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

AUTHORITY_IFACE = "org.freedesktop.PolicyKit1.Authority"
AUTHORITY_PATH = "/org/freedesktop/PolicyKit1/Authority"
AUTHORITY_NAME = "org.freedesktop.PolicyKit1"
ALLOWED_ACTION_PREFIX = "net.reactivated.fprint."


class Authority(dbus.service.Object):
    """Answers CheckAuthorization for fprintd actions only."""

    def __init__(self, bus: dbus.Bus) -> None:
        super().__init__(bus, AUTHORITY_PATH)

    @dbus.service.method(
        AUTHORITY_IFACE,
        in_signature="(sa{sv})sa{ss}us",
        out_signature="(bba{ss})",
    )
    def CheckAuthorization(self, subject, action_id, details, flags, cancellation_id):
        del subject, details, flags, cancellation_id
        authorized = str(action_id).startswith(ALLOWED_ACTION_PREFIX)
        if not authorized:
            print(f"refusing non-fprintd action: {action_id}", file=sys.stderr)
        # (is_authorized, is_challenge, details)
        return (authorized, False, {})

    @dbus.service.method(AUTHORITY_IFACE, in_signature="", out_signature="s")
    def BackendName(self):
        return "goodix550c-private-stub"

    @dbus.service.method(AUTHORITY_IFACE, in_signature="", out_signature="s")
    def BackendVersion(self):
        return "0"

    @dbus.service.method(AUTHORITY_IFACE, in_signature="", out_signature="u")
    def BackendFeatures(self):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bus-address", required=True)
    args = parser.parse_args()

    if not args.bus_address.startswith("unix:path=/"):
        parser.error("refusing a bus address that is not a private unix socket")

    # "Is an absolute unix socket" does not mean "is private": the system bus is
    # unix:path=/run/dbus/system_bus_socket and satisfies it. Claiming
    # org.freedesktop.PolicyKit1 there, on a host where no real polkit holds the
    # name, would answer every fprintd authorization check for every caller with
    # no challenge -- enroll, verify and delete-all included. Refuse the system
    # bus by path rather than trusting the caller to pass the right socket.
    socket_path = Path(args.bus_address[len("unix:path=") :].split(",", 1)[0])
    for reserved in (Path("/run/dbus"), Path("/var/run/dbus")):
        if socket_path == reserved or reserved in socket_path.parents:
            parser.error(f"refusing the system bus socket: {socket_path}")

    DBusGMainLoop(set_as_default=True)
    bus = dbus.bus.BusConnection(args.bus_address)

    # Both references must outlive this function: dbus-python releases the bus
    # name as soon as the BusName object is collected, which leaves the stub
    # running and answering nothing while callers see ServiceUnknown.
    bus_name = dbus.service.BusName(AUTHORITY_NAME, bus)
    authority = Authority(bus)

    loop = GLib.MainLoop()
    print("private polkit stub ready", flush=True)
    try:
        loop.run()
    finally:
        authority.remove_from_connection()
        del bus_name
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
