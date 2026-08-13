# Goodix 27c6:550c Linux driver lab

This project enables the USB fingerprint reader in a Lenovo Yoga 9 14IAP7. Linux
enumerates the hardware as `27c6:550c`, but upstream `libfprint` does not bind a
driver to it.

There are three deliberately separated layers:

- a tightly allow-listed Python identity/backup probe that cannot reset or configure
  the sensor; and
- an isolated `libfprint` 1.94.10 build for the exact `550c` that can perform a
  TLS open, enrollment, and verification. Persistent mutation is denied at the
  transport boundary, and volatile initialization needs both a build-time and a
  runtime opt-in; and
- a source-recompiled external TOD module for Ubuntu's exact
  `libfprint 1.95.1+tod1` runtime. It leaves Ubuntu's core library and `fprintd`
  untouched and has a private-D-Bus smoke mode in which USB is not visible.

## Set up

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,reverse,dpapi]'
.venv/bin/pytest
```

Fetch and verify the exact public source revisions used by both native builds:

```bash
scripts/fetch_upstream_sources.sh
```

That command initializes only ignored directories below `research/upstream/` and
performs depth-one fetches of these immutable revisions:

| Source | Public URL | Revision |
|---|---|---|
| Goodix 550c driver | `https://github.com/brianporeilly/goodix-550c-driver.git` | `a3de5a1b6174ace5db0bb2a8796c5be6e55428f0` |
| libfprint | `https://gitlab.freedesktop.org/libfprint/libfprint.git` | tag `v1.94.10`, commit `0c97a47d8ef405cd577b87058c1e89cae9d242e7` |

Use `scripts/fetch_upstream_sources.sh --offline` to verify cached clones without
network access. Build scripts refuse missing inputs and point back to this command;
they also independently verify the pinned commits before archiving any source.

The native driver build additionally needs Meson/Ninja, GLib/GIO, GUsb, OpenSSL,
and the OpenCV core/features2d/flann/imgproc development components. On this host:

```bash
scripts/build_goodix550c_libfprint.sh \
  --allow-volatile-init \
  --allow-manual-fdt-poll \
  --stage-dir build/goodix550c-13021-manual-fdt-v7
```

The build stays below `build/`; it neither installs nor runs the driver. Its
minimal exact-device harness is emitted as
`build/goodix550c-13021-manual-fdt-v7/builddir/goodix550c-open-close`.
Before emitting that harness, the build runs offline native regressions for the
paired TLS channel and for the patched production FDT-down command path. The latter
compiles the actual Goodix command and protocol encoder sources, replaces only USB
submission with an in-memory capture, and checks the complete firmware-13021 wire
frame.

After confirming fprintd is inactive and no process/driver owns the USB interface,
the guarded live open can be repeated with:

```bash
sudo chown root:root research/secrets/goodix550c.psk
sudo scripts/run_goodix550c_open_close.sh \
  --stage-dir build/goodix550c-13021-manual-fdt-v7 \
  --psk-file research/secrets/goodix550c.psk \
  --allow-volatile-init
sudo chown bart:bart research/secrets/goodix550c.psk
```

`--allow-volatile-init` is required for each run; the volatile USB/MCU reset gate
is never enabled without that explicit acknowledgement. `--allow-manual-fdt-poll`
is accepted but optional here, because this harness only opens and closes and
reaches no manual-FDT state; pass it only to reproduce the exact live environment,
and omit it for a control run against a manual-FDT stage. The runtime gate
`GOODIX550C_ALLOW_MANUAL_FDT_POLL=1` is exported only when the flag is given, and
any inherited value is cleared. The wrapper stops nothing: any holder, bound
interface driver, or active fprintd causes refusal.

This isolated 1.94.10 path exists to validate the driver against a build-tree
library. For a live open against Ubuntu's *installed* runtime, prefer the guarded
TOD open/close under "Guarded physical TOD open/close" below: that wrapper adds
build-metadata, module/harness digest, source-drift, package-version, and exact
endpoint checks that this isolated wrapper does not perform.

The manual fallback is firmware-13021-specific and requires both the compile-time
option above and runtime value `GOODIX550C_ALLOW_MANUAL_FDT_POLL=1`, which the
guarded wrapper supplies only after the explicit flag. Keep the pad fully clear
during open and the pre/post-reference baseline phases, and do not suspend the
machine during the experiment. Manual responses are classified only from two
consecutive raw 24-byte readings; the device's touch flag is ignored because it
was observed nonzero with hands clear. No raw readings or per-channel metrics are
logged or persisted.

Neither reference-baseline phase waits forever: each fails the action after 100
polls (10 s), and the finger-up wait fails after 600 polls (60 s), so a pad that
never settles ends in an error instead of an endless USB poll. Only the
finger-down wait is deliberately unbounded, matching the asynchronous path it
replaces; it ends on cancellation. If the machine is suspended while a poll owns
the device, the driver now refuses the suspend rather than reporting one it cannot
honor, and the in-flight action fails at its next cancellation check.

## Ubuntu 26.04 TOD module without USB access

The preferred integration artifact is `libgoodix550c.so`, an external TOD driver
for the installed `fprintd 1.94.5-4` and
`libfprint 1:1.95.1+tod1-0ubuntu2`. The build refuses a different architecture or
runtime package version. It recompiles the exact fail-closed source allowlist
against verified Ubuntu headers; it does not reuse 1.94.10 object files or replace
the installed core library.

Fetch and verify the two pinned public development packages, then build with the
volatile path compiled out:

```bash
scripts/fetch_ubuntu_tod_sdk.sh
scripts/build_goodix550c_tod_module.sh \
  --offline-sdk \
  --stage-dir build/goodix550c-tod-default-off
```

The package digests are pinned in the fetcher. All downloads, extracted headers,
patched sources, binaries, and metadata remain below `build/`. The build runs a
source-policy audit, an exact core/TOD ABI audit, and the offline paired TLS test.
It never installs the module or invokes fprintd.

The loader and D-Bus integration can be tested without exposing the host's USB
devices:

```bash
scripts/smoke_goodix550c_tod_fprintd.sh \
  --module build/goodix550c-tod-default-off/builddir/libgoodix550c.so \
  --run-dir build/goodix550c-tod-smoke
```

This starts only a directly executed fprintd and a non-activating private bus, both
as recorded child processes. Bubblewrap supplies a fresh `/dev`, empty `/sys`,
`/run`, and `/tmp`, a read-only host root, and an empty overlay at the checkout
path. Only the dedicated smoke run directory is rebound at
`/run/goodix550c-tod-smoke`; assertions prove `.env` and `research/secrets` are not
visible. The environment is rebuilt from empty, so no PSK is inherited. Cleanup
checks shell-job ownership plus `/proc` parent, start-time, and executable identity
before it signals either recorded child; no system service is queried, stopped, or
restarted.

Two independent hardened default-off builds produced identical module SHA-256
`d2c473c98b47bae29fceed2532002d8cccbd6d8911a495de8dc1b293401abd5a`
and identical installed-runtime harness SHA-256
`bf0f4e6d079f636a77182f1ad4140ff0b77a93d4b5ddf93537dc6176071dfc66`.
This module digest reflects the full `driver-series` including patch `0005`,
whose manual-FDT source is present but compile-gated off in a default-off build.
The no-USB smoke loaded driver ID `goodix53x5` through the installed TOD loader and
returned an empty device array, as required. This validates loading and D-Bus
plumbing only; installation and physical-device testing remain separate,
explicitly authorized phases.

## Guarded physical TOD open/close

Build a separate module with volatile initialization compiled in. This still
installs and runs nothing:

```bash
scripts/build_goodix550c_tod_module.sh \
  --offline-sdk \
  --allow-volatile-init \
  --allow-manual-fdt-poll \
  --stage-dir build/goodix550c-tod-manual-fdt-v3
```

The build also emits `goodix550c-tod-open-close`, a PIE linked directly to the
installed Ubuntu core and TOD SONAMEs without an RPATH. The physical wrapper
requires a fresh command-line opt-in, verifies module, harness, and project-relative
tracked-input manifests, reruns the source/ABI audit, compares the decoded PSK to
the independent live hash oracle without printing either, and checks the exact USB
interface and endpoints.
It refuses an active fprintd, a bound interface, or any process holding the USB
node; it never stops any of them.

```bash
sudo chown root:root research/secrets/goodix550c.psk
sudo scripts/run_goodix550c_tod_open_close.sh \
  --stage-dir build/goodix550c-tod-manual-fdt-v3 \
  --psk-file research/secrets/goodix550c.psk \
  --expected-psk-hash research/artifacts/psk-hash-current.json \
  --allow-volatile-init
sudo chown bart:bart research/secrets/goodix550c.psk
```

This run needs no finger: the harness opens the device and closes it, and reaches
no manual-FDT state. Keep the pad clear for the whole run.

As with the isolated wrapper, `--allow-manual-fdt-poll` is accepted but optional
and changes nothing an open/close can reach; the runtime gate is exported only when
it is given. Only the project-local module is exposed to the installed TOD loader.
The initial USB reset remains behind the volatile opt-in; after that reset and
firmware read, the driver requires `GF5288_GM168SEC_APP_13021` before MCU reset,
register writes, or configuration upload. A successful PSK-TLS handshake is required
before open can pass. The wrapper uses project-local HOME/TMP/XDG directories, a
rebuilt environment, and a 45-second bound; it performs no installation or
persistent sensor command. Its per-run private directory under `build/` is removed
on every exit path.

## Guarded enrollment capture test

This is the only command that reads a finger. It runs `fp_device_enroll_sync`
through the same guarded wrapper and **discards the template**: nothing is stored,
printed, or hashed. It is a capture test, not a way to register a fingerprint.

```bash
sudo scripts/run_goodix550c_tod_open_close.sh \
  --stage-dir build/goodix550c-tod-manual-fdt-v4 \
  --psk-file research/secrets/goodix550c.psk \
  --expected-psk-hash research/artifacts/psk-hash-current.json \
  --allow-volatile-init \
  --allow-manual-fdt-poll \
  --enroll --debug
```

`--enroll` requires `--allow-manual-fdt-poll`. Keep the pad **completely clear**
until the harness prints `PLACE your finger on the sensor now` — before that it is
establishing two no-finger reference baselines, and contact corrupts them. Then
place, hold until the stage is reported, lift, and repeat. `--debug` adds driver
diagnostics; it logs no key, raw reading, image, or template. The run is bounded at
240 seconds.

Current state: contact detection and capture work, but enrollment does not finish.
After the first two to four stages, every capture decodes as a blank frame and is
rejected with "the finger was not centered properly" at a measured 95.4% non-contact
area. This is a capture-path defect under investigation, not a placement problem —
do not work around it by loosening the coverage gate, which would enroll unusable
templates. See DOCUMENTATION.md for the evidence.

## Offline packet preview

This produces the exact bytes without opening the USB device:

```bash
.venv/bin/goodix550c packets
```

## Offline recovery of the existing Windows PSK

The recovery utility performs no USB I/O. It reads the captured 324-byte slot
`0xbb010002` DPAPI blob and the matching offline Windows DPAPI hives/master keys,
then requires the plaintext's SHA-256 to match the separately captured live hash
oracle from slot `0xbb020001`. It never prints the PSK.

The Windows source must be exposed through a repository-local read-only mount. That
bind is currently not mounted; do not run recovery until it has deliberately been
re-established read-only. Check the mount and machine-key prerequisites first:

```bash
findmnt .dpapi-windows-ro
.venv/bin/python scripts/recover_windows_dpapi_psk.py --preflight
```

Verify and discard the plaintext immediately:

```bash
.venv/bin/python scripts/recover_windows_dpapi_psk.py
```

Or explicitly create a driver-compatible hex key, mode `0600`, under the ignored
secret directory:

```bash
.venv/bin/python scripts/recover_windows_dpapi_psk.py \
  --blob research/secrets/psk-dpapi.bin \
  --expected-hash research/artifacts/psk-hash.json \
  --output research/secrets/goodix550c.psk
```

The utility refuses a writable Windows source, output outside `research/secrets`,
existing output files, malformed or wrong-slot E4 data, DPAPI integrity failure, and
any mismatch with the live sensor hash. Its Impacket registry adapter opens the
SYSTEM and SECURITY hives with `rb`, not Impacket's default `r+b` mode.

On this machine the independent recovery run passed both DPAPI integrity and the live
hash-oracle check. The resulting `research/secrets/goodix550c.psk` is ignored by Git,
65 bytes of driver-compatible lowercase hex plus newline, and mode `0600`. Its
contents must never be printed, logged, or committed.

## Live, read-only identity probe

The probe sends only a protocol NOP and a firmware-version request. USB access normally requires root until a dedicated udev rule exists:

```bash
sudo .venv/bin/goodix550c probe \
  --i-understand-this-sends-two-read-only-requests \
  --output research/artifacts/live-probe.json
```

Do not run any upstream `goodix-fp-dump` driver script against this sensor. It does not support PID `550c`, and several scripts intentionally erase and replace firmware.

## Proven live path

On this machine, the recovered Windows PSK has completed a real TLS handshake with
firmware `GF5288_GM168SEC_APP_13021`; volatile initialization and close both
succeeded using the isolated build. As of 2026-08-13 the same open/close also
passes through Ubuntu's *installed* `libfprint 1.95.1+tod1` runtime via the TOD
module, three consecutive runs with the pad clear. No firmware, PSK, or production
data was written, and no system library or service was changed. No fingerprint has
been read under Linux: enrollment and verification are still unbuilt.

If an open ever fails with `error:1C800066:Provider routines::cipher operation
failed`, the sensor is holding a stale TLS session rather than rejecting the key.
Run the read-only probe once and retry:

```bash
sudo .venv/bin/goodix550c identify psk-hash \
  --i-understand-this-sends-two-read-only-requests
```

See the engineering record for the exact safety gates and private
enrollment/verification procedure.

See [DOCUMENTATION.md](DOCUMENTATION.md) for the evidence, protocol notes, safety model, and current status.
