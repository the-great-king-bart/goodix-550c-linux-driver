#!/usr/bin/env python3
"""A small window for enrolling and verifying on the guarded Goodix 550c session.

The command-line harnesses in this project print their prompts to a terminal,
which only works when the operator is the one who started them. This talks to
the same private fprintd over its per-run bus address and draws the prompts in a
window instead, so it does not matter who launched it.

It never touches the host system bus: the address is passed in by the session
wrapper and must be a private unix socket.
"""

from __future__ import annotations

import argparse
import contextlib
import sys

import dbus
import gi
from dbus.mainloop.glib import DBusGMainLoop

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402  (needs require_version first)

FPRINT_NAME = "net.reactivated.Fprint"
MANAGER_PATH = "/net/reactivated/Fprint/Manager"
MANAGER_IFACE = "net.reactivated.Fprint.Manager"
DEVICE_IFACE = "net.reactivated.Fprint.Device"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

FINGERS = (
    "right-index-finger",
    "right-middle-finger",
    "right-thumb",
    "right-ring-finger",
    "right-little-finger",
    "left-index-finger",
    "left-middle-finger",
    "left-thumb",
    "left-ring-finger",
    "left-little-finger",
)

# fprintd reports a stage outcome; anything that is not a pass is the device
# asking for the same stage again rather than a failure of the enrolment.
ENROLL_RETRY = {
    "enroll-retry-scan": "Didn't read that one — place again.",
    "enroll-swipe-too-short": "Held too briefly — place again.",
    "enroll-finger-not-centered": "Only part of the pad was covered — place flatter.",
    "enroll-remove-and-retry": "Lift, then place again.",
}
VERIFY_RETRY = {
    "verify-retry-scan": "Didn't read that one — place again.",
    "verify-swipe-too-short": "Held too briefly — place again.",
    "verify-finger-not-centered": "Only part of the pad was covered — place flatter.",
    "verify-remove-and-retry": "Lift, then place again.",
}


class FingerprintWindow(Gtk.Window):
    def __init__(self, bus: dbus.Bus, username: str) -> None:
        super().__init__(title="Goodix 550c Fingerprint")
        self.set_default_size(560, 460)
        self.set_border_width(16)

        self.bus = bus
        self.username = username
        self.device = None
        self.device_props = None
        self.claimed = False
        self.busy = False
        self.total_stages = 0
        self.stages_done = 0

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(outer)

        self.device_label = Gtk.Label(xalign=0.0)
        self.device_label.set_markup("<i>connecting…</i>")
        outer.pack_start(self.device_label, False, False, 0)

        self.status = Gtk.Label()
        self.status.set_markup("<span size='xx-large' weight='bold'>Ready</span>")
        self.status.set_line_wrap(True)
        self.status.set_justify(Gtk.Justification.CENTER)
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.set_border_width(24)
        inner.pack_start(self.status, True, True, 0)
        frame.add(inner)
        outer.pack_start(frame, False, False, 0)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_text("")
        outer.pack_start(self.progress, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.pack_start(Gtk.Label(label="Finger:"), False, False, 0)
        self.finger = Gtk.ComboBoxText()
        for name in FINGERS:
            self.finger.append_text(name)
        self.finger.set_active(0)
        controls.pack_start(self.finger, True, True, 0)
        outer.pack_start(controls, False, False, 0)

        # Verifying a named finger loads only that slot. Matching against every
        # enrolled slot is what a login gate does, and it is also what makes
        # extra passes of the same finger, stored under spare slots, count for
        # anything at all.
        self.match_any = Gtk.CheckButton(label="Verify against any enrolled finger")
        self.match_any.set_active(True)
        outer.pack_start(self.match_any, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.enroll_button = Gtk.Button(label="Enrol")
        self.enroll_button.connect("clicked", self.on_enroll)
        buttons.pack_start(self.enroll_button, True, True, 0)
        self.verify_button = Gtk.Button(label="Verify")
        self.verify_button.connect("clicked", self.on_verify)
        buttons.pack_start(self.verify_button, True, True, 0)
        self.cancel_button = Gtk.Button(label="Stop")
        self.cancel_button.connect("clicked", self.on_stop)
        self.cancel_button.set_sensitive(False)
        buttons.pack_start(self.cancel_button, True, True, 0)
        self.delete_button = Gtk.Button(label="Delete all")
        self.delete_button.connect("clicked", self.on_delete)
        buttons.pack_start(self.delete_button, True, True, 0)
        outer.pack_start(buttons, False, False, 0)

        self.enrolled_label = Gtk.Label(xalign=0.0)
        outer.pack_start(self.enrolled_label, False, False, 0)

        self.log_buffer = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.log_buffer)
        view.set_editable(False)
        view.set_monospace(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(view)
        outer.pack_start(scroller, True, True, 0)

        self.connect("destroy", self.on_destroy)
        GLib.idle_add(self.connect_device)

    # -- plumbing ---------------------------------------------------------

    def log(self, text: str) -> None:
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, text + "\n")

    def set_status(self, text: str, tone: str = "") -> None:
        colour = {"good": "#5bd67d", "bad": "#ff6b6b", "": ""}.get(tone, "")
        style = f" foreground='{colour}'" if colour else ""
        self.status.set_markup(
            f"<span size='xx-large' weight='bold'{style}>{GLib.markup_escape_text(text)}</span>"
        )

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.enroll_button.set_sensitive(not busy)
        self.verify_button.set_sensitive(not busy)
        self.delete_button.set_sensitive(not busy)
        self.finger.set_sensitive(not busy)
        self.match_any.set_sensitive(not busy)
        self.cancel_button.set_sensitive(busy)

    def connect_device(self) -> bool:
        try:
            manager = dbus.Interface(
                self.bus.get_object(FPRINT_NAME, MANAGER_PATH), MANAGER_IFACE
            )
            paths = manager.GetDevices()
            if not paths:
                self.device_label.set_markup("<b>No fingerprint device found.</b>")
                self.set_status("No device", "bad")
                return False

            obj = self.bus.get_object(FPRINT_NAME, paths[0])
            self.device = dbus.Interface(obj, DEVICE_IFACE)
            self.device_props = dbus.Interface(obj, PROPERTIES_IFACE)
            name = self.device_props.Get(DEVICE_IFACE, "name")
            self.total_stages = int(self.device_props.Get(DEVICE_IFACE, "num-enroll-stages"))
            self.device_label.set_markup(
                f"<b>{GLib.markup_escape_text(str(name))}</b>  —  "
                f"{self.total_stages} enrolment stages  —  user "
                f"{GLib.markup_escape_text(self.username)}"
            )

            obj.connect_to_signal("EnrollStatus", self.on_enroll_status)
            obj.connect_to_signal("VerifyStatus", self.on_verify_status)
            obj.connect_to_signal("VerifyFingerSelected", self.on_finger_selected)

            self.device.Claim(self.username)
            self.claimed = True
            self.log("Device claimed.")
            self.refresh_enrolled()
        except dbus.DBusException as error:
            self.device_label.set_markup("<b>Could not reach the device.</b>")
            self.set_status("Connection failed", "bad")
            self.log(str(error))
        return False

    def refresh_enrolled(self) -> None:
        try:
            fingers = list(self.device.ListEnrolledFingers(self.username))
        except dbus.DBusException:
            fingers = []
        if fingers:
            self.enrolled_label.set_text("Enrolled: " + ", ".join(str(f) for f in fingers))
        else:
            self.enrolled_label.set_text("Enrolled: none")

    # -- actions ----------------------------------------------------------

    def on_enroll(self, _button: Gtk.Button) -> None:
        if self.device is None or self.busy:
            return
        self.stages_done = 0
        self.progress.set_fraction(0.0)
        self.progress.set_text(f"0 / {self.total_stages}")
        self.set_busy(True)
        self.set_status("Place your finger on the reader")
        self.log(f"Enrolling {self.finger.get_active_text()} ({self.total_stages} stages).")
        try:
            self.device.EnrollStart(self.finger.get_active_text())
        except dbus.DBusException as error:
            self.set_busy(False)
            self.set_status("Could not start", "bad")
            self.log(str(error))

    def on_verify(self, _button: Gtk.Button) -> None:
        if self.device is None or self.busy:
            return
        self.set_busy(True)
        self.progress.set_fraction(0.0)
        self.progress.set_text("")
        target = "any" if self.match_any.get_active() else self.finger.get_active_text()
        self.set_status("Place your finger on the reader")
        self.log(f"Verifying against {target}.")
        try:
            self.device.VerifyStart(target)
        except dbus.DBusException as error:
            self.set_busy(False)
            self.set_status("Could not start", "bad")
            self.log(str(error))

    def on_stop(self, _button: Gtk.Button) -> None:
        if self.device is None:
            return
        for stop in ("EnrollStop", "VerifyStop"):
            try:
                getattr(self.device, stop)()
            except dbus.DBusException:
                continue
        self.set_busy(False)
        self.set_status("Stopped")

    def on_delete(self, _button: Gtk.Button) -> None:
        if self.device is None or self.busy:
            return

        # DeleteEnrolledFingers2 erases every finger at once and there is no
        # undo: recovering costs a full re-enrolment of each one, sixteen
        # placements apiece. A destructive button beside the two ordinary ones
        # should not fire on a single misclick.
        enrolled: list[str] = []
        with contextlib.suppress(dbus.DBusException):
            enrolled = [str(f) for f in self.device.ListEnrolledFingers(self.username)]
        if not enrolled:
            self.log("Nothing enrolled to delete.")
            return

        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Delete all {len(enrolled)} enrolled fingerprint(s)?",
        )
        dialog.format_secondary_text(
            "This erases " + ", ".join(enrolled) + " for "
            f"{self.username} and cannot be undone. Each finger needs a full "
            f"{self.total_stages}-stage enrolment to restore."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Delete", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT:
            self.log("Deletion cancelled.")
            return

        try:
            self.device.DeleteEnrolledFingers2()
            self.log("Deleted every enrolled finger for this user.")
        except dbus.DBusException as error:
            self.log(str(error))
        self.refresh_enrolled()

    # -- signals ----------------------------------------------------------

    def on_enroll_status(self, result: str, done: bool) -> None:
        result = str(result)
        if result == "enroll-stage-passed":
            self.stages_done += 1
            if self.total_stages:
                self.progress.set_fraction(min(1.0, self.stages_done / self.total_stages))
            self.progress.set_text(f"{self.stages_done} / {self.total_stages}")
            self.set_status("Lift your finger, then place it again", "good")
            self.log(f"Stage {self.stages_done}/{self.total_stages} captured.")
        elif result in ENROLL_RETRY:
            self.set_status(ENROLL_RETRY[result])
            self.log(f"Retry: {result}")

        if not done:
            return

        with contextlib.suppress(dbus.DBusException):
            self.device.EnrollStop()
        self.set_busy(False)
        if result == "enroll-completed":
            self.progress.set_fraction(1.0)
            self.set_status("Enrolled", "good")
            self.log("Enrolment complete and stored.")
        elif result == "enroll-duplicate":
            self.set_status("Already enrolled", "bad")
            self.log("This finger is already enrolled.")
        else:
            self.set_status("Enrolment failed", "bad")
            self.log(f"Enrolment ended: {result}")
        self.refresh_enrolled()

    def on_finger_selected(self, finger: str) -> None:
        self.log(f"Device selected slot: {finger}")

    def on_verify_status(self, result: str, done: bool) -> None:
        result = str(result)
        if result in VERIFY_RETRY and not done:
            self.set_status(VERIFY_RETRY[result])
            self.log(f"Retry: {result}")
            return
        if not done:
            return

        with contextlib.suppress(dbus.DBusException):
            self.device.VerifyStop()
        self.set_busy(False)
        if result == "verify-match":
            self.set_status("Match", "good")
            self.log("Verified: match.")
        elif result == "verify-no-match":
            self.set_status("No match", "bad")
            self.log("Verified: no match.")
        else:
            self.set_status("Verification failed", "bad")
            self.log(f"Verification ended: {result}")

    def on_destroy(self, _widget: Gtk.Widget) -> None:
        if self.device is not None and self.claimed:
            for stop in ("EnrollStop", "VerifyStop"):
                with contextlib.suppress(dbus.DBusException):
                    getattr(self.device, stop)()
            with contextlib.suppress(dbus.DBusException):
                self.device.Release()
        Gtk.main_quit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bus-address", required=True)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()

    if not args.bus_address.startswith("unix:path=/"):
        parser.error("refusing a bus address that is not a private unix socket")

    DBusGMainLoop(set_as_default=True)
    try:
        bus = dbus.bus.BusConnection(args.bus_address)
    except dbus.DBusException as error:
        print(f"Cannot reach the private session bus: {error}", file=sys.stderr)
        return 1

    # The desktop theme is not consulted for a window launched out of a root
    # session wrapper, and the default light theme is glaring next to a dark
    # desktop, so ask for the dark variant explicitly.
    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-application-prefer-dark-theme", True)

    window = FingerprintWindow(bus, args.username)
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
