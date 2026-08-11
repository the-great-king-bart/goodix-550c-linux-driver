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

`0001` and the additional secret-loader hardening in `0003` apply to the driver
repository. `0002` applies to a pristine libfprint v1.94.10 tree. The build
script verifies both revisions before it
does anything and copies an explicit source allow-list; the upstream firmware
loader, firmware tools, `open_test`, installer, and sysext deployment scripts
never enter the prepared tree.

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

An eventual isolated test build can retain the volatile path while keeping it
runtime-disabled:

```bash
scripts/build_goodix550c_libfprint.sh --allow-volatile-init
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
