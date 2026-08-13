# Fail-closed Goodix 27c6:550c integration

The patched driver files retain their upstream LGPL-2.1-or-later licensing;
the repository's MIT license covers the original lab tooling, not a relicensing
of those derivative patches.

This patch set prepares the exact experimental driver without installing or
running it. It targets these immutable upstream inputs:

- `brianporeilly/goodix-550c-driver`
  `a3de5a1b6174ace5db0bb2a8796c5be6e55428f0`
- libfprint `v1.94.10`
  `0c97a47d8ef405cd577b87058c1e89cae9d242e7`

`0001`, the additional secret-loader hardening in `0003`, the firmware-13021
FDT-down layout correction in `0004`, and the guarded manual-FDT fallback in
`0005` apply to the driver repository in the order recorded by `driver-series`.
`0002` applies only to a pristine libfprint v1.94.10
tree. Both build pipelines verify the pinned driver revision and apply the same
ordered driver series before copying an explicit source allowlist; the upstream
firmware loader, firmware tools, `open_test`, installer, and sysext deployment
scripts never enter a prepared tree.

## Safety boundary

Persistent device mutation is unavailable even when volatile initialization is
enabled:

- automatic self-heal is excluded by the preprocessor and has no active caller;
- the firmware loader is omitted from Meson and from the copied source set;
- PSK/production write, application erase, firmware write, and firmware
  check/commit helpers are excluded from compilation;
- the common transport independently rejects command pairs `A/2`, `E/0`,
  `E/1`, `F/0`, and `F/2` before allocating or submitting a USB transfer;
- the driver binds only to `27c6:550c` and never requests automatic kernel
  driver detachment or rebinding.

Normal capture requires volatile USB reset, MCU `A2`, sensor/config writes,
finger-detection commands, EC control, and sleep. Those paths require both:

1. a build made with `--allow-volatile-init`; and
2. runtime environment value `GOODIX550C_ALLOW_VOLATILE_INIT=1`.

The build-time default is off. Persistent commands remain blocked under every
combination of these settings.

Before the first MCU reset, the driver requires firmware identity
`GF5288_GM168SEC_APP_13021`. This is the identity reported by this sensor and
embedded in its original Lenovo/Goodix 3.0.550.180 package. Static comparison
also found that the driver's 256-byte default configuration is byte-identical
to the original `Wbdi.dll` bytes at offset `0x127c50`; both hash to
`fe90fe662355096c9235b6fdaee422a5739966268fd36a943fd19dd366ee0e3d`.
The driver's OTP validator accepts the captured sensor OTP. These facts justify
the exact 13021 gate; they do not authorize a live run by themselves.

Firmware 13021's TLS FDT-down setup sends the live-generated 24-byte base directly.
The sibling prefixed form remains in the non-TLS branch, and FDT-up/manual FDT keep
their original prefixed builders. The compiled-source audits check this separation.
The isolated build additionally compiles the patched production command source and
protocol encoder into a transport-free native regression. That executable captures
the real FDT-down command call, runs its payload through the real inner and outer
encoders, compares the complete known 13021 wire frame, and checks that the sibling
layout remains prefixed.

Firmware 13021 acknowledged the raw FDT-down command with the two-byte ACK
`32 01`, but emitted no asynchronous event for more than 15 seconds even when the
finger was already held before the command. This rules out the earlier
configuration-missing hypothesis for that capture: ACK flag `0x02` was clear.
Its meaning remains unproven and the patch does not reject it.

Patch `0005` adds a TLS-PSK-only experimental fallback. It is disabled unless
both Meson option `goodix550c_manual_fdt_poll=true` and runtime value
`GOODIX550C_ALLOW_MANUAL_FDT_POLL=1` are present. It polls complete, bounded
manual TX-on transactions at 100 ms intervals, compares only the 24 raw FDT bytes
against an in-memory no-finger baseline, and requires two consecutive decisions.
Pre- and post-reference baselines require two fresh readings stable within the
OTP-derived `delta_fdt`; down requires two out-of-baseline readings using
`delta_down`; up requires two returns within `delta_up`, then refreshes the
in-memory raw/encoded bases before sensor sleep. The firmware's manual touch flag
is deliberately ignored because a live hands-off response proved it can be
nonzero. The guided operator must therefore keep the pad completely clear while
open and both reference baselines are established.

Each waiting phase that expects a clear pad also carries a poll budget: 100 polls
(10 s) per reference baseline and 600 polls (60 s) for finger-up. A pad that never
settles fails the action with a clear error instead of polling forever, which
matters because `delta_fdt` is 0 whenever the decoded OTP reports no spread and the
comparison is a strict `>`; stability then means two bit-identical readings. Only
the finger-down wait is unbounded, matching the asynchronous path it replaces, and
it ends on cancellation.

Polling cancellation is checked before and after each whole command, never between
its request, ACK, and data reply. Logs contain only phase transitions and bounded
poll counts—not raw readings, touch masks, deltas, changed-channel counts, images,
or templates. Normal non-TLS and default-off behavior is unchanged. Suspend is not
supported for the current guided experiment and is now refused rather than
acknowledged: the poll never arms a cancellable blocking read, so the driver's
"not in a blocking read" branch would otherwise report a clean suspend while the
delayed state machine kept issuing USB transactions. Keep the machine awake until
the action closes.

## Secret handling

The 550c path has no `/etc` or all-zero fallback. It requires exactly one of:

- `GOODIX550C_PSK_FILE=/absolute/path/to/research/secrets/goodix550c.psk`
  (preferred); or
- `GOODIX550C_PSK=<64 hexadecimal characters>`.

The file must be an absolute, non-symlink regular file owned by the process's
effective user with mode `0600` and exactly one link. It is opened once with
`O_NOFOLLOW`, then checked and read through that descriptor to prevent a pathname
race. A root-run harness therefore needs a temporary
root-owned `0600` copy inside this repository; the ordinary user-owned secret
correctly fails the owner check under root. The value never enters argv or log
messages. Parsed text and the temporary binary PSK are cleansed with
`OPENSSL_cleanse`. TLS is restricted to the captured
`PSK-AES128-CBC-SHA256` suite and the observed `Client_identity`; the long-lived
TLS key buffer is also securely cleansed.

## Offline preparation and build

Preparation applies and audits the patches without configuring, compiling, or
accessing USB:

```bash
scripts/build_goodix550c_libfprint.sh --prepare-only
```

The default build keeps volatile initialization compiled out:

```bash
scripts/build_goodix550c_libfprint.sh
```

An eventual isolated manual-poll test build can retain both experimental paths
while keeping them runtime-disabled:

```bash
scripts/build_goodix550c_libfprint.sh \
  --allow-volatile-init \
  --allow-manual-fdt-poll
```

The script writes only under `build/`, uses `--wrap-mode=nodownload`, and never
runs Meson install, fprintd, the USB device, or a service-management command.

Required development components are Meson, Ninja, a C/C++17 toolchain,
pkg-config, GLib/GIO, OpenSSL 3, libgusb, and OpenCV core/features2d/flann/imgproc.
On this Ubuntu host the minimal missing package request is:

```text
libgusb-dev libopencv-core-dev libopencv-features2d-dev
libopencv-flann-dev libopencv-imgproc-dev
```

The Meson integration first accepts `opencv5.pc` or `opencv4.pc`. If the OpenCV
meta pkg-config file is absent, it validates `/usr/include/opencv4` and links
only the four component libraries. SIGFM was narrowed to component headers, so
the full `libopencv-dev` meta-package is not required by this patch.

No deployment is included. The installed Ubuntu library is a newer TOD-enabled
build; replacing it with vanilla upstream v1.94.10 would be unsafe and is out of
scope. Any eventual live test must use the repository-local build through an
isolated harness, after verifying no process or kernel driver owns the USB
interface.

## Ubuntu TOD module build

The preferred Ubuntu 26.04 integration builds the same fail-closed source closure
as an external module against exact `1:1.95.1+tod1-0ubuntu2` development headers:

```bash
scripts/fetch_ubuntu_tod_sdk.sh
scripts/build_goodix550c_tod_module.sh \
  --offline-sdk \
  --stage-dir build/goodix550c-tod-default-off
```

`tod/goodix550c-sources.txt` is the authoritative 32-file allowlist. The build adds
only the external TOD registration wrapper, links the installed core and TOD
SONAMEs with undefined references forbidden, and restricts exports to the loader
entry point. Its verifier also confirms the default-off compile option, private API
imports, and absence of an RPATH/RUNPATH. It never installs or runs fprintd.

The guarded manual-poll TOD build uses the same two explicit compile options:

```bash
scripts/build_goodix550c_tod_module.sh \
  --offline-sdk \
  --allow-volatile-init \
  --allow-manual-fdt-poll \
  --stage-dir build/goodix550c-tod-manual-fdt-v3
```

The verifier compares both requested Meson values with `intro-buildoptions.json`
and the actual compile definitions; build metadata records both values. Its
default-off check matches each option's own declaration block rather than testing
for `value: false` anywhere in the file, so one default-off option can no longer
vouch for another.

The live wrapper requires a fresh `--allow-volatile-init` acknowledgement and
exports each runtime gate only to its new harness child. `--allow-manual-fdt-poll`
is optional there: the open/close harness reaches no manual-FDT state, so
requiring it would only remove the ability to run a control open against a
manual-FDT stage. When the flag is absent the gate is not exported at all, and the
harness rejects any value for it other than exactly `1`.

The separate private-bus smoke script runs fprintd with host USB and udev state
hidden by bubblewrap. It validates loader/D-Bus plumbing only and does not authorize
a physical-device operation or system installation.
