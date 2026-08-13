# Goodix 27c6:550c Linux enablement

This is the living engineering record for enabling the Goodix fingerprint reader in
this Lenovo Yoga 9 14IAP7 (`82LU`) on Linux. The reader is an image sensor using a
vendor protocol and a TLS-PSK-protected image channel; it is not a Goodix MOC device.

## Non-negotiable operating constraints

- Do not reboot, power off, suspend, cold-boot, or stop/restart existing services until
  the owner explicitly permits it.
- Create and modify files only below this repository. The repository-local read-only
  Windows evidence bind is currently not mounted and must not be replaced by direct
  access to a writable source volume.
- Never print, copy into logs, or commit `.env`, a plaintext PSK, Windows biometric
  templates, fingerprint images, or proprietary driver/firmware binaries.
- Never detach an active kernel driver or displace a process holding the USB node.
- Firmware erase/write, PSK/production write, and automatic "self-heal" are
  unconditionally denied in the isolated driver. Sensor-register writes,
  configuration upload, and MCU/USB reset are volatile operations and require both
  an explicit build option and `GOODIX550C_ALLOW_VOLATILE_INIT=1` at runtime.

No service has been stopped or restarted, and no host reboot, power-off, suspend, or
cold boot has been performed. A bounded live driver open has performed the expected
sensor-only USB/MCU reset and volatile RAM configuration; it completed and closed
cleanly.

## Host and hardware baseline (2026-08-11)

| Item | Observed value |
|---|---|
| Computer | Lenovo Yoga 9 14IAP7, machine type `82LU` |
| BIOS | `HNCN51WW` |
| OS | Ubuntu 26.04 LTS, x86-64 |
| Kernel | `7.0.0-29-generic` |
| Fingerprint USB ID | `27c6:550c` (`Goodix FingerPrint`) |
| USB location | bus 003, port path `3-6` |
| Interface | 0, vendor class `ff/00/00` |
| Endpoints | bulk OUT `0x01`, bulk IN `0x83`, 64-byte packets |
| USB link/power | full speed 12 Mbit/s, 100 mA |
| Bound kernel driver | none |
| Initial power state | runtime-autosuspended; unchanged |
| Installed userspace | `fprintd 1.94.5-4`, `libfprint 1.95.1+tod1` |
| Initial fprintd state | installed, inactive; `No devices available` |

## Confirmed device identity

A purpose-built, fail-closed PyUSB probe claimed and released interface 0 without
detaching a driver, resetting USB, selecting a configuration, or changing persistent
state. It sent only allow-listed read requests.

| Query | Result |
|---|---|
| Application firmware | `GF5288_GM168SEC_APP_13021` |
| Chip ID after volatile initialization | `0x00220ca1` |
| OTP | 32-byte value returned; raw value retained only in an ignored local transcript |
| Live PSK hash slot `0xbb020001` | a 32-byte non-default hash; not the public all-zero PSK hash |
| Pre-initialization chip query | exact five-byte register request ACKed and returned `00000000`; the later gated driver initialization read the useful ID above |
| IAP version | request timed out; not retried |
| Windows DPAPI PSK backup | slot `0xbb010002` returned exactly 324 bytes; standard DPAPI header validated |

The existing Windows-provisioned PSK remains active. Nothing has been erased,
reprovisioned, or reflashed.

The sealed backup is stored at `research/secrets/psk-dpapi.bin`, mode `0600`, and
ignored by Git. Its SHA-256 is
`8f6fabf5c0e461fbd7c051a501f93f586508132e94f9f7571a1826fe1f33bd05`.
This digest documents acquisition integrity without revealing the encrypted blob.

Local ignored transcripts are under `research/artifacts/`. They deliberately do not
enter Git because OTP data and PSK metadata are device-specific.

## Original Windows driver evidence

Static, read-only inspection of the original Windows 11 partition found the
model-specific Goodix package in the DriverStore. The temporary project-local
read-only bind used for that work is currently not mounted:

```text
Windows/System32/DriverStore/FileRepository/wbdiusb.inf_amd64_68affe4a1d2ed8cb
```

- Driver version: `3.0.550.180`, for `USB\\VID_27C6&PID_550C`
- Architecture: a UMDF/WinUSB userspace driver (`Wbdi.dll`), not a Goodix kernel `.sys`
- Embedded application identity: `GF5288_GM168SEC_APP_13021`
- Embedded IAP identity: `MILAN_GM168SEC_IAP_10007`
- Secure channel string: `TLS-PSK-WITH-AES-128-CBC-SHA256`
- Relevant static symbols include `McuGetFirmwareVersion`, `McuStartTls`,
  `PresetPskReadG`, `McuEraseApp`, and `McuUpdateApp`.

The isolated driver's 256-byte `default_config` matches the original `.180`
`Wbdi.dll` byte-for-byte at corrected file offset `0x127c50` (zero differing bytes,
SHA-256 `fe90fe662355096c9235b6fdaee422a5739966268fd36a943fd19dd366ee0e3d`).

Hashes of the locally installed, unmodified components:

| Component | SHA-256 |
|---|---|
| `Wbdi.dll` | `e0996e4976078ed7a0ac9c85ad0464e2ec794e4a976447b26ea24d2ccb7ba79a` |
| `GoodixEngineAdapter.dll` | `489a211be2c607518697a00722d1460e85550f73d4b2263783c0542331b95d85` |
| `SessionService.exe` | `148afe8333095138d7afa15ed6514fe19e9f982902e1e82a5279132814f3b579` |
| `WbdiUsb.inf` | `d2c009fec6435523cc08a779b41a73a93b233d2f03e401000bb0060563ddb1da` |
| `wbdiusb.cat` | `a4703f1648d09dbc9632c668a1840162812a05ef53c81c6f9571e595905ab088` |

No Windows biometric templates, `goodix.dat`, or opaque ProgramData log contents are
copied into this repository.

## Exact protocol

The executable open-source 550c implementation and our successful replies agree on
this framing:

```text
inner = command:u8 || length(payload + checksum):u16le || payload || checksum:u8
checksum = (0xaa - sum(command, both length bytes, payload)) & 0xff

outer = flag:u8 || length(inner):u16le || header_checksum:u8 || inner
header_checksum = (flag + length_low + length_high) & 0xff
```

Normal messages use flag `0xa0`; TLS handshake records use `0xb0`; TLS application and
image records use `0xb2`. Host writes are zero-padded to a 64-byte USB transfer.

Read-only requests exposed by the Python probe are exact `(command, payload)` pairs,
not arbitrary opcodes. Its hard-deny set includes at minimum PSK write `0xe0`, app
erase `0xa4`, firmware write/check `0xf0`/`0xf4`, sensor write `0x80`, config upload
`0x90`, and reset `0xa2`. The isolated libfprint transport independently and
unconditionally denies app erase, PSK/production write, and firmware write/check;
its reset/register/config path is available only through the double volatile gate.

## Existing upstream work and risk assessment

- Upstream libfprint currently lists `27c6:550c` as known unsupported.
- `goodix-fp-dump` at pinned commit
  `cc43bb3b3154a0bccc0412ae024013c7e1923139` has no 550c runner. Its 55x4 code is
  for GF3268 and may erase/reflash automatically; it must not be adapted by PID swap.
- `brianporeilly/goodix-550c-driver` at pinned commit
  `a3de5a1b6174ace5db0bb2a8796c5be6e55428f0` is an exact experimental GF5288
  libfprint driver. Its author reports working TLS capture, SIGFM enrollment,
  verification, and fprintd on firmware 13022.
- That fork has no release and little independent review. Its open path can
  automatically erase, reprovision, flash, reset, and re-enumerate a sensor after a
  TLS failure if it finds firmware. It is never run or installed unmodified here.
- Its suggested firmware is a newer 14IRP8 `13022` image. This machine has the
  original 14IAP7 `13021`; cross-flashing is not an acceptable rollback plan.
- Firmware read (`0xf2`) reportedly cannot back up this device. Incorrect write
  chunking can wedge USB until a physical cold boot, which is currently forbidden.

## Safest path to a working driver

Windows generated a random 32-byte live PSK and stored two sensor records:

- `0xbb010003`: the live PSK encoded as a vendor white-box;
- `0xbb010002`: a 324-byte machine-bound LocalSystem DPAPI backup blob ignored by
  the sensor.

The latter was read without mutation and unsealed using the matching Windows machine
secrets. This preserved both the current firmware and Windows Hello compatibility.
Reprovisioning to a public all-zero PSK would require erasing and reflashing the
sensor and remains prohibited.

The captured blob has `DPAPI_BLOB.Flags = 0`; that serialized field does not prove
the original `CryptProtectData` scope. Its master-key GUID resolves specifically to
`Windows/System32/Microsoft/Protect/S-1-5-18/User`, whose master key decrypts only
with `DPAPI_SYSTEM.UserKey`. The recovery tool binds each LocalSystem namespace to
its corresponding DPAPI_SYSTEM key and never falls back across keys or to another
user SID. Final correctness and freshness come from matching `SHA256(plaintext PSK)`
to the independently read live hash slot `0xbb020001`.

Offline recovery is implemented in `scripts/recover_windows_dpapi_psk.py`. It:

1. requires the Windows source to be on a read-only mount;
2. replaces Impacket's unnecessary registry `r+b` open with an `rb`-only parser;
3. reads only SYSTEM boot-key material and SECURITY's `DPAPI_SYSTEM` LSA secret;
4. validates raw or framed E4 input as slot `0xbb010002`, exactly 324 bytes;
5. verifies DPAPI integrity, a 32-byte plaintext, and the live hash oracle;
6. prints no key and, only when requested, writes a non-overwriting mode-`0600` hex
   file directly below the ignored `research/secrets/` directory.

The non-mutating commands are:

```bash
.venv/bin/python scripts/recover_windows_dpapi_psk.py --preflight
.venv/bin/python scripts/recover_windows_dpapi_psk.py
```

An explicit repository-local output can be requested with:

```bash
.venv/bin/python scripts/recover_windows_dpapi_psk.py \
  --output research/secrets/goodix550c.psk
```

The independent recovery run completed successfully: DPAPI integrity passed and the
32-byte plaintext's SHA-256 matched the live sensor hash oracle. No key material was
printed. `research/secrets/goodix550c.psk` is a 65-byte lowercase-hex-plus-newline
file, owned by `bart`, mode `0600`, and ignored by Git.

## Fail-closed libfprint port

The tracked patch series integrates exact experimental driver commit
`a3de5a1b6174ace5db0bb2a8796c5be6e55428f0` into pristine libfprint tag
`v1.94.10` (`0c97a47d8ef405cd577b87058c1e89cae9d242e7`). The build script archives both
pinned trees, applies the patches, audits the resulting compiled source, and builds
only the `goodix53x5` driver below `build/`.

The ignored upstream trees are reproducibly obtained with
`scripts/fetch_upstream_sources.sh`. It depth-fetches and verifies only the public
Goodix repository commit above and libfprint tag `v1.94.10` commit
`0c97a47d8ef405cd577b87058c1e89cae9d242e7`; `--offline` validates existing origins
and commit objects. Both build pipelines give this exact setup command if an input
clone is absent, then independently reject a revision mismatch.

The resulting policy is fail-closed:

- The USB ID table contains only `27c6:550c`, interface 0, OUT `0x01`, IN `0x83`.
- Firmware must be exactly `GF5288_GM168SEC_APP_13021` before the MCU reset state.
- Exactly one explicit `GOODIX550C_PSK` or absolute `GOODIX550C_PSK_FILE` is
  required. A file is opened with `O_NOFOLLOW`, then checked/read through that
  descriptor; it must be regular, single-link, owned by the effective UID, and
  mode `0600`.
- Parsed key text and both transient and long-lived key buffers are cleansed; the
  key is never logged.
- TLS is restricted to `PSK-AES128-CBC-SHA256` and observed identity
  `Client_identity`.
- Firmware-loader/self-heal sources are excluded, their calls are compile-disabled,
  and persistent opcodes are denied again in the transport.
- USB interface claim uses flags `0`: it never auto-detaches or rebinds a kernel
  driver.
- Volatile USB/MCU reset, register/config writes, and FDT setup require Meson's
  default-off `goodix550c_volatile_init=true` plus the runtime value
  `GOODIX550C_ALLOW_VOLATILE_INIT=1`.
- Firmware-13021 manual FDT polling independently requires Meson's default-off
  `goodix550c_manual_fdt_poll=true` plus exact runtime value
  `GOODIX550C_ALLOW_MANUAL_FDT_POLL=1`. The gate is TLS-PSK-only; the non-TLS
  asynchronous flow is unchanged.

Installed build prerequisites on this host are `libgusb-dev`,
`libopencv-core-dev`, `libopencv-features2d-dev`, `libopencv-flann-dev`, and
`libopencv-imgproc-dev` plus their dependencies. Apt made no upgrades or removals
and restarted no services.

The clean isolated build completed without installation. A minimal harness then
loaded the build-tree library, found the sole exact-ID device, completed the TLS
handshake and volatile initialization with the recovered live PSK, and closed it
cleanly. No persistent command was sent. The generic libfprint raw-capture API is
intentionally not advertised; scans are consumed through enrollment/authentication.

System installation, PAM changes, and service-integrated physical testing remain
outside the current authorization. The external TOD module described below avoids
replacing Ubuntu's core libfprint, but installing it would still write outside this
repository. Any later PAM rollout must retain password authentication, prove
repeated positive and negative matches first, and must not make fingerprint the sole
administrator or disk-unlock credential.

## Ubuntu 26.04 external TOD module

The safest path from the working isolated 1.94.10 build to Ubuntu's installed stack
is an external TOD module, not an older core-library replacement. The installed
versions are exactly:

```text
fprintd:             1.94.5-4
libfprint-2-2:       1:1.95.1+tod1-0ubuntu2
libfprint-2-tod1:    1:1.95.1+tod1-0ubuntu2
architecture:        amd64
```

The exact fprintd binary imports 47 public libfprint symbols, all from
`LIBFPRINT_2.0.0`; the old isolated library happens to provide them. That narrow
compatibility is not a deployment justification. The old library omits Ubuntu's
SDCP addition, TOD loader, and other drivers. Conversely, libfprint's private driver
layouts changed in 1.95.1: `FpIdEntry`, `FpDeviceClass`, and `FpiUsbTransfer` have
TOD-specific layout/padding changes. Reusing 1.94.10 objects would therefore be
unsafe even though fprintd's public imports resolve.

Ubuntu's core loader honors `FP_TOD_DRIVERS_DIR`, opens regular `lib*.so` modules,
looks up `fpi_tod_shared_driver_get_type`, and verifies the returned GType derives
from `FpDevice`. The project now builds that interface from source using:

- the pinned driver commit and ordered `driver-series` patches `0001`, `0003`,
  `0004`, and `0005`;
- exactly 32 allowlisted upstream files: 13 C units, 15 Goodix headers, one SIGFM
  C++ unit, and three SIGFM headers;
- one tracked registration wrapper;
- Ubuntu's exact 1.95.1+tod1 public and TOD-private headers; and
- explicit links to installed SONAMEs `libfprint-2.so.2` and
  `libfprint-2-tod.so.1` with undefined references forbidden.

Patch `0004` keeps firmware-13021's TLS FDT-down setup as the raw generated
24-byte base. Non-TLS FDT-down, FDT-up, and manual FDT retain their prefixed payload
builder. In addition to the compiled-source audit, the isolated build compiles the
patched production `goodix53x5-commands.c` and `goodix53x5-proto.c` into a native
offline regression. Its test-only seam replaces only transport submission with an
in-memory capture, then proves the TLS branch supplies the dynamic raw 24-byte base
to the real encoder and produces the complete known 32-byte `0xa0` frame. It also
proves the sibling branch remains prefixed. The verifier checks the post-ACK
event-read diagnostic; this protocol correction has not been exercised against USB
as part of the TOD work.

### Firmware-13021 manual FDT fallback

The corrected raw-24-byte FDT-down request received an exact two-byte ACK with
acknowledged command `0x32` and flags `0x01`. It still produced no asynchronous
event for more than 15 seconds when the correct keyboard-pad sensor was held before
the command. Flag `0x02` was not set, so a missing configuration does not explain
that observation. Because the flag's general meaning is not established, patch
`0005` records only a rate-limited numeric diagnostic under the experimental gate
and does not reject it.

Patch `0005` supplies a firmware-13021 TLS-PSK fallback with two independent
opt-ins. Every poll is one complete bounded manual TX-on request/ACK/data
transaction; cancellation is checked before and after the transaction and during
the 100 ms delayed states. A cancellation can therefore wait for the current
bounded command, but cannot leave an unread partial response to poison the next
command.

Only the 24 raw FDT bytes after the four-byte protocol header are compared, and
only against an in-memory baseline. The thresholds remain the sensor's decoded OTP
values—there are no fixed public thresholds. The state machine enforces:

- two fresh readings stable within `delta_fdt` before the reference image;
- a second two-reading stability check after image mode is disabled, followed by
  an in-memory raw and encoded baseline refresh;
- two consecutive readings outside `delta_down` before finger-down;
- two consecutive readings back within `delta_up` before finger-up, then another
  baseline refresh and sensor sleep; and
- streak reset when the classification changes.

The first guided manual build safely stopped during open because firmware 13021
reported a nonzero manual touch flag while the operator's hands were fully clear.
No enrollment began. That live evidence invalidated the touch-flag-as-contact
assumption. The revised patch ignores those two header bytes for manual baseline,
down, and up classification and relies only on consecutive raw-reading stability.
For this guided experiment, the operator must keep the pad fully clear throughout
open and both reference-baseline phases. The machine must remain awake; suspend and
reboot are outside the experiment.

Every phase that waits for a *clear* pad carries a poll budget and fails the action
when it is spent: 100 polls (10 s) for each reference-baseline phase and 600 polls
(60 s) for finger-up. Without those budgets an unstable or contacted pad polls the
sensor every 100 ms forever, and the action neither completes nor errors — the
operator is not asked for contact in those phases, so nothing would ever break the
loop but an external cancel. The degenerate case matters here: `delta_fdt` is 0
whenever the decoded OTP reports no spread, and the comparison uses a strict
`>`, so stability then means two bit-identical 24-byte readings. The equivalent
upstream open-time check bounds itself with `GOODIX_OPEN_FDT_MAX_RETRIES = 2` for
the same reason. Only the finger-down wait polls without a bound, matching the
asynchronous event path it replaces; cancellation ends it.

Suspend is refused while a poll owns the device. The manual path never arms a
cancellable blocking read, so `blocking_ssm` stays `NULL` for the whole finger
wait, and the pre-existing "not in a blocking read" branch of
`goodix_session_suspend` would report a clean suspend while the delayed SSM kept
issuing bounded USB transactions until the machine actually slept. A
`manual_fdt_poll_active` flag, set when a poll state is attached to its SSM and
cleared by that state's destructor on every exit including failure and
cancellation, now makes that branch return `FP_DEVICE_ERROR_NOT_SUPPORTED`
instead. `needs_reinit` is still set first, so the in-flight action fails at its
next cancellation check.

The polling logs only phase transitions, candidate booleans, consecutive counts,
and bounded poll counts. It neither logs nor saves raw readings, touch masks,
deltas, per-channel counts, fingerprint images, or templates. Persistent-command
denial is unchanged. The revised isolated build passed at
`build/goodix550c-13021-manual-fdt-v7/builddir`; the matching TOD source/ABI build
passed at `build/goodix550c-tod-manual-fdt-v3/builddir`.

The only intentional exported function is the TOD registration entry point. A
linker version script localizes the driver and C++ implementation symbols. The ABI
audit verifies ELF64/amd64, both exact libfprint SONAME dependencies, absence of
RPATH/RUNPATH, non-executable stack, stack protector, FORTIFY, full
RELRO/BIND_NOW, the restricted export surface, all 37 `fp_*`/`fpi_*` imports against
the installed core/TOD exports, the `LIBFPRINT_TOD_1_1.95.1` version node, and the
Meson/compile-command state of both experimental gates. Build metadata records only
project-relative source names and content hashes, not checkout-specific paths.

### Pinned project-local SDK

`scripts/fetch_ubuntu_tod_sdk.sh` downloads over HTTPS, verifies metadata and
SHA-256, and extracts only below `build/` (or an explicitly selected project-local
`research/` directory). The exact development inputs are:

| Package | SHA-256 |
|---|---|
| `libfprint-2-dev_1.95.1+tod1-0ubuntu2_amd64.deb` | `db01d591d13f81312d618d2e247ceed66d72ee42cd07130b19637425df32ee28` |
| `libfprint-2-tod-dev_1.95.1+tod1-0ubuntu2_amd64.deb` | `d1f99081a0d314b7416bdc9d5d70bea47237787dea946a95d8de3e21bb0b8b91` |

The `--offline` fetch mode and build script's `--offline-sdk` mode refuse network
access unless those verified files are already cached. Nothing is installed with
apt or Meson.

The reproducible default-off build command is:

```bash
scripts/build_goodix550c_tod_module.sh \
  --offline-sdk \
  --stage-dir build/goodix550c-tod-default-off
```

Two clean builds in differently named stage directories produced identical
module and installed-runtime harness bytes:

```text
module:  d2c473c98b47bae29fceed2532002d8cccbd6d8911a495de8dc1b293401abd5a
harness: bf0f4e6d079f636a77182f1ad4140ff0b77a93d4b5ddf93537dc6176071dfc66
```

These digests cover the full `driver-series` including the bounded revision of
patch `0005`, whose manual-FDT source is present but compile-gated off in a
default-off build. Any change to that series changes the module digest.

The source-policy audit, ELF ABI audit, and paired offline TLS policy test all
passed. Compilation retains a small set of upstream unused-parameter and signedness
warnings; there were no link errors or unresolved libfprint references.

### No-USB private-fprintd validation

The private smoke test does not rely on private D-Bus alone, because constructing an
`FpContext` normally enumerates GUsb. `scripts/smoke_goodix550c_tod_fprintd.sh`
starts a non-activating private bus and the installed daemon directly inside
bubblewrap. The sandbox uses a new `/dev`, empty `/sys`, `/run`, and `/tmp`, a
read-only host root, and an empty tmpfs over the checkout's normal path. It rebinds
only the dedicated run directory at `/run/goodix550c-tod-smoke`; `.env` and
`research/secrets` are therefore absent, and the script asserts that absence along
with `/dev/bus/usb`, `/sys/bus/usb/devices`, and `/run/udev`. The environment is
rebuilt from empty. Cleanup signals a recorded child only while Bash still owns the
job and `/proc` confirms the original parent, start time, and expected executable;
this closes the numeric-PID reuse race. No service-manager command is present.

The final smoke result was:

```text
installed core:       Initializing FpContext (libfprint version 1.95.1+tod1)
TOD loader:           opened project-local libgoodix550c.so
registered driver:    goodix53x5 (Goodix HTK32 Fingerprint Sensor)
private GetDevices:   (@ao [],)
```

The private bus intentionally has no logind activation entry, so fprintd logs a
nonfatal missing-inhibitor warning. This validation proves loader ABI, GType
registration, daemon construction, storage redirection, and D-Bus export. It cannot
prove physical open, capture, enrollment, production systemd sandbox behavior, or
PAM integration. The TOD private ABI is not generally stable, so the current module
must remain pinned to the exact Ubuntu package release and be rebuilt/revalidated
after any libfprint update.

### Guarded installed-runtime physical harness

The TOD build also compiles `tools/goodix550c_tod_open_close.c` against the pinned
project-local Ubuntu headers and links it directly to the installed
`libfprint-2.so.2` and `libfprint-2-tod.so.1`. The ELF audit requires an amd64 PIE,
both SONAME dependencies, immediate relocation binding, the expected public
open/close calls, and no RPATH/RUNPATH. It therefore exercises Ubuntu's installed
core/TOD loader rather than the isolated 1.94.10 build.

The live module remains default-off. A separately named stage must be built with
both the compile-time opt-in and the normal offline SDK verification:

```bash
scripts/build_goodix550c_tod_module.sh \
  --offline-sdk \
  --allow-volatile-init \
  --allow-manual-fdt-poll \
  --stage-dir build/goodix550c-tod-manual-fdt-v3
```

`scripts/run_goodix550c_tod_open_close.sh` requires all of the following before it
runs the harness:

- root execution and an explicit `--allow-volatile-init` acknowledgement.
  `--allow-manual-fdt-poll` is accepted but optional, because open/close reaches
  no manual-FDT state; the runtime gate is exported only when that flag is given,
  so a control run against a manual-FDT stage stays possible;
- a non-symlink module, harness, metadata, source-checksum manifest, PSK, and
  live-hash oracle below the project; exact build metadata; matching
  module/harness SHA-256 values; and unchanged tracked build inputs;
- the pinned amd64 Ubuntu core/TOD package versions and a fresh source/ELF audit;
- a root-owned, mode-0600, single-link 32-byte key whose SHA-256 matches the
  independent live sensor oracle, compared without printing either value;
- exactly one `27c6:550c`, interface 0, only 64-byte bulk OUT `0x01` and IN `0x83`,
  with no kernel driver and no process holding its USB node; and
- fprintd exactly inactive. Any other state causes refusal; the wrapper contains no
  service-stop or existing-process signal operation.

After those preconditions, an empty environment exposes only the build directory
to `FP_TOD_DRIVERS_DIR`, allowlists only `goodix53x5`, and supplies the file-based
PSK and runtime volatile gate. The initial USB reset is already protected by the
volatile opt-ins. After that reset and the firmware read, the driver requires exact
firmware `GF5288_GM168SEC_APP_13021` before MCU reset, register writes, or
configuration upload, and must complete PSK-TLS before the harness reports success.
HOME, TMP, and all XDG locations are private directories below project `build/`; a
45-second timeout applies only to the new harness child, and the wrapper exits with
that child's status. The per-run private directory is created with `mktemp -d`
under `build/` and removed by an EXIT trap that only accepts a path matching the
prefix this script created, so runs do not accumulate root-owned trees. The
harness itself rejects any `GOODIX550C_ALLOW_MANUAL_FDT_POLL` value other than
absent or exactly `1`. The later command is:

```bash
sudo chown root:root research/secrets/goodix550c.psk
sudo scripts/run_goodix550c_tod_open_close.sh \
  --stage-dir build/goodix550c-tod-manual-fdt-v3 \
  --psk-file research/secrets/goodix550c.psk \
  --expected-psk-hash research/artifacts/psk-hash-current.json \
  --allow-volatile-init
sudo chown bart:bart research/secrets/goodix550c.psk
```

That command needs no finger contact: the harness opens the device and closes it.
Manual-FDT polling, and therefore any finger-down wait, is reachable only from
enrollment or verification, which this repository has no harness for yet.

It installs nothing and cannot send the driver's compile-time-elided persistent
mutation commands.

#### Physical result (2026-08-13)

The guarded physical TOD open/close ran on this machine and passed, three
consecutive times:

```text
Goodix 550c TOD source and ABI audit passed
Preflight passed; opening only 27c6:550c through the project-local TOD module.
Exact firmware, PSK-TLS, and volatile TOD open succeeded.
Device closed cleanly through the installed Ubuntu TOD runtime.
```

This is the first physical validation through Ubuntu's *installed*
`libfprint 1.95.1+tod1` runtime rather than the isolated 1.94.10 build tree. The
pad was clear throughout; no finger, enrollment, or template was involved.

The first two attempts of that session failed identically, in both the TOD and the
isolated harness:

```text
Device open failed: TLS handshake failed: error:1C800066:Provider routines::cipher operation failed
```

The cause was stale sensor state, not code, key, or environment. Ruled out during
diagnosis: the PSK still matched (the live `psk-hash` response bytes were
byte-identical to the 2026-08-11 oracle capture), OpenSSL and libfprint had not
changed since 2026-08-09 — before the last successful run — and the offline paired
TLS test passed on the same OpenSSL 3.5.5 that failed against the device. The
sensor had been left mid-session by the failed manual-FDT experiment of 2026-08-11
and sat untouched for two days. One read-only `goodix550c identify psk-hash`
(protocol NOP/wake plus a read-only query, no TLS) cleared it, and every open since
has succeeded.

Operationally: if the handshake fails with a provider cipher error, run the
read-only identity probe once and retry before suspecting the key or the build.

### Guarded enrollment harness

`tools/goodix550c_tod_enroll.c` is the only harness that reaches the finger-wait
states. It is a capture test, not a provisioning tool: it opens the device, runs
`fp_device_enroll_sync`, reports per-stage progress and finger-status transitions,
then discards the template. Nothing is written, printed, or hashed, and the audit
rejects the harness if it references any serialization or file-writing call.

It is built by the same script into the same stage, with its own
`enroll_harness_sha256` metadata entry, and is selected through the existing
guarded wrapper rather than a second runner, so every preflight gate applies
unchanged:

```bash
sudo scripts/run_goodix550c_tod_open_close.sh \
  --stage-dir build/goodix550c-tod-manual-fdt-v4 \
  --psk-file research/secrets/goodix550c.psk \
  --expected-psk-hash research/artifacts/psk-hash-current.json \
  --allow-volatile-init \
  --allow-manual-fdt-poll \
  --enroll --debug
```

`--enroll` requires `--allow-manual-fdt-poll`, because on firmware 13021 the
finger-wait states depend on manual polling; the harness enforces the same
requirement itself. The enrollment bound is 240 s rather than the open/close 45 s.
`--debug` adds `G_MESSAGES_DEBUG=all` for driver diagnostics; it changes no device
behaviour and logs no key, raw reading, image, or template.

The stage used below produced module SHA-256
`60db0a107fac417141df33d3e1c0181bb3d107b0153882dddb3e6f7b22ecec51`, open/close
harness `bf0f4e6d079f636a77182f1ad4140ff0b77a93d4b5ddf93537dc6176071dfc66`, and
enrollment harness
`7c64e047bfcf37903a357b7a8d31d4ccea5639642678627e023852c86ef276ea`.

#### Physical enrollment result (2026-08-13)

**The sensor read a fingerprint under Linux for the first time.** The manual-FDT
poll detected contact reliably on every placement, both reference baselines
confirmed after 2 polls each, and captured frames passed SIGFM feature extraction:

```text
Open succeeded. Enrollment needs 8 stage(s).
Manual FDT post-reference baseline confirmed after 2 polls
Manual FDT finger-down poll armed
Stage 1/8 captured.
```

This closes the firmware-13021 no-event question that blocked the project: the
guarded fallback works, and the earlier touch-flag assumption remains correctly
ignored.

Enrollment itself does not yet complete. Both runs ended at the 240 s bound:

```text
run 1 (no debug):  4 of 8 stages captured, 36 rejections
run 2 (--debug):   2 of 8 stages captured,  4 rejections, 7 placements
```

Every rejection was `FP_DEVICE_RETRY_CENTER_FINGER` from the enrollment coverage
gate at `goodix53x5-enroll.c:100`, never the keypoint gate. With debug enabled the
gate reported its exact measurement:

```text
Enrollment stage rejected: 95.4% of frame has no finger contact (limit 10.0%)
```

`goodix_device_image_clipped_fraction` counts raw12 pixels at ADC full scale
(`GOODIX_RAW12_CLIP` 4095) across the 108x88 frame, so 95.4% means roughly 9,067 of
9,504 pixels are railed: the decoded frame is effectively blank.
`GOODIX_ENROLL_MAX_CLIPPED_FRACTION` is `0.10`.

Three observations rule out finger placement and threshold tuning:

- the rejected measurements are 95.3-95.4%, repeatable to a tenth of a percent;
- the distribution is bimodal — a capture either passes well under 10% or lands at
  ~95.4%, with nothing in between; and
- the good captures are the early ones. Run 1 captured stages 1-4 and then
  rejected 36 consecutive attempts; run 2 captured stages 1-2 and then rejected
  every attempt.

The working hypothesis is therefore a capture-path state defect, not sensing: the
550c branch disables image readout after each capture with
`goodix_cmd_write_sensor_register (ssm, dev, 0x022c, 0x0a, 0x02)` and
`goodix53x5-scan.c` clears `self->reference_image` once consumed. If readout
re-enable or reference re-acquisition does not happen for later stages, the decode
returns a no-contact frame, which is exactly a ~95% railed image. This is not
believed to be caused by the manual-FDT work — contact detection succeeds every
time and hands off to untouched upstream capture code — but that has not been
proven and should be verified rather than assumed.

The next diagnostic needs no finger: log the reference frame's own clipped fraction
and the readout register state per stage, which confirms or kills the hypothesis in
one run. No template was produced or stored by either run.

## Verification

```text
project pytest:        49 passed
offline TLS pair test: passed, correct identity/cipher/decrypt + wrong-ID rejection
native 13021 FDT test: passed, production command branch + encoder + packed wire frame
native manual FDT:     passed, strict boundary/malformed input/dual-gate policy
ruff:                  passed
shell syntax:          passed
compiled-source audit: passed
isolated build:        passed, no warnings, no install
libfprint unit suite:  2 passed, 1 dependency skip
live TLS open/close:   passed
TOD source/ABI audit:  passed against exact Ubuntu 1.95.1+tod1
TOD reproducibility:   passed, identical output from two clean stage paths
private fprintd smoke: passed with USB/udev hidden and empty GetDevices
option default-off:    passed, per-option rule rejects a gate that defaults on
manual-poll bounds:    passed, both settle phases fail on a spent poll budget
live TOD open/close:   passed 3/3 against installed 1.95.1+tod1, pad clear
live TOD enrollment:   partial, contact and capture work, stages 2-4 of 8 only
```

Every line above was produced after the bounded revision of patch `0005`, including
both live lines: the isolated `live TLS open/close` was re-run against the
`build/goodix550c-13021-manual-fdt-v7` stage and the TOD line against
`build/goodix550c-tod-manual-fdt-v3`.

The full upstream Meson invocation also skips 28 unavailable driver-emulation tests.
Its two data-only checks fail for environmental reasons unrelated to this driver: an
upstream shell test does not quote this workspace's space-containing path, and
AppStream treats an unreachable upstream issue URL as a validation warning. Both
compiled unit tests pass.

The Python probe records exact padded OUT bytes and checked ACK/response bytes in
ignored JSON transcripts. It has bounded timeouts and no reset or retry loop. The
libfprint trial logs only state transitions and outcomes; PSK material is never
logged.

## Repository tree

Project-owned files are listed below. `.git/`, `.venv/`, Python caches
(including `*.egg-info/`, `.cache/`, and `.tmp/`), generated coverage data, the
ignored upstream clones, and the obsolete scaffold's dependency tree are
intentionally condensed.

```text
.
├── .dpapi-windows-ro/                # ignored mountpoint; currently not mounted
├── .env                              # ignored secret; never printed/committed
├── .gitignore
├── DOCUMENTATION.md                  # this living record and tree
├── LICENSE                           # MIT license for original lab tooling
├── README.md                         # operator-facing setup and commands
├── build/                            # ignored generated sources/binaries/test logs
├── patches/goodix-550c/
│   ├── 0001-goodix53x5-add-fail-closed-550c-policy.patch
│   ├── 0002-libfprint-v1.94.10-integration.patch
│   ├── 0003-goodix53x5-harden-secret-loading.patch
│   ├── 0004-goodix550c-use-13021-fdt-down-layout.patch
│   ├── 0005-goodix550c-add-guarded-manual-fdt-poll.patch
│   ├── driver-series                 # ordered driver-repository patches
│   └── README.md                     # patch-series threat model
├── pyproject.toml                    # Python package/test/lint metadata
├── research/
│   ├── artifacts/                    # ignored device-specific transcripts/errors
│   ├── obsolete-vite/                # ignored superseded scaffold + dependencies
│   ├── secrets/                       # ignored, mode-0700 key-recovery material
│   │   ├── goodix550c.psk            # recovered hex PSK, mode 0600; never print
│   │   ├── live-validation/           # private enrollment/verification artifacts
│   │   └── psk-dpapi.bin              # encrypted DPAPI blob, mode 0600
│   └── upstream/                     # ignored pinned public research clones
├── scripts/
│   ├── build_goodix550c_libfprint.sh # pinned isolated patch/build pipeline
│   ├── build_goodix550c_tod_module.sh # exact Ubuntu TOD module build
│   ├── fetch_ubuntu_tod_sdk.sh       # pinned dev-package fetch/extract
│   ├── fetch_upstream_sources.sh     # exact public git fetch/verification
│   ├── inventory.sh                  # passive host/USB inventory
│   ├── pe_string_xrefs.py            # offline PE string/xref helper
│   ├── recover_windows_dpapi_psk.py  # offline, non-printing DPAPI recovery
│   ├── run_goodix550c_open_close.sh  # holder-aware isolated live harness wrapper
│   ├── run_goodix550c_tod_open_close.sh # guarded installed-runtime TOD harness
│   ├── smoke_goodix550c_tod_fprintd.sh # private bus + no-USB smoke
│   ├── verify_goodix550c_patch.py    # 1.94.10 compiled-source policy audit
│   └── verify_goodix550c_tod_module.py # TOD source and ELF ABI audit
├── src/goodix550c/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                        # safety-gated CLI
│   ├── protocol.py                   # pure allow-listed protocol codec
│   └── usb_probe.py                  # bounded claim/query/release transport
├── tests/
│   ├── native/
│   │   ├── goodix550c_fdt_down_test_shim.h # transport-free production-source seam
│   │   ├── test_goodix550c_fdt_down.c # compiled 13021 command/encoder wire test
│   │   ├── test_goodix550c_manual_fdt.c # raw-delta boundary + runtime-gate test
│   │   └── test_goodix550c_tls.c      # paired memory-BIO TLS policy test
│   ├── test_cli.py
│   ├── test_dpapi_recovery.py
│   ├── test_goodix550c_patch_policy.py
│   ├── test_goodix550c_tod_module.py
│   ├── test_protocol.py
│   └── test_usb_probe.py
├── tod/
│   ├── goodix550c-sources.txt        # exact 32-file source allowlist
│   ├── goodix550c-tod-entry.c        # external TOD registration entry
│   ├── goodix550c-tod.map            # one-symbol export map
│   ├── meson.build                    # standalone mixed C/C++ module target
│   ├── meson_options.txt              # exact paths + two default-off gates
│   └── private-bus.conf               # non-activating smoke bus template
└── tools/
    ├── goodix550c_open_close.c       # isolated-build TLS open/close harness
    ├── goodix550c_tod_enroll.c       # installed-runtime enrollment capture test
    └── goodix550c_tod_open_close.c   # installed-runtime TOD open/close harness
```

Update this tree whenever project files are added, removed, or moved.

## Current status

Passive inventory, exact firmware identification, OTP validation, PSK-hash
classification, chip-register measurement, sealed-backup acquisition, offline DPAPI
unsealing, independent live-hash verification, fail-closed driver integration,
compiled-source audit, clean isolated build, and a real TLS open/close are complete.
The firmware-13021 no-event condition and invalid manual touch-flag assumption are
now independently reproduced; revised manual-poll builds pass both native pipelines.
The exact Ubuntu TOD module now also builds reproducibly, loads through a
workspace-only, no-USB private fprintd smoke, and has physically opened and closed
the sensor through Ubuntu's installed `1.95.1+tod1` runtime three times in a row.
The existing Windows firmware and PSK were not changed.

The sensor has now read a fingerprint under Linux. Manual-FDT contact detection,
capture, decode, and SIGFM feature extraction all work, which answers the
firmware-13021 no-event question that blocked this project. Enrollment does not yet
complete: after the first two to four stages every subsequent capture decodes as a
blank, ADC-railed frame and is rejected by the coverage gate. That capture-path
defect is the current work item. No template has been produced or stored.

Module installation and PAM integration remain separate phases, and no driver has
been installed system-wide.

A code review of the manual-FDT work found and this revision fixed: unbounded
reference-baseline and finger-up poll loops that could wedge an enrollment or
verification indefinitely; a suspend hook that reported a clean suspend while a
poll kept driving USB; three "default-off" audits whose substring test was
satisfied by an unrelated option and would have passed a gate that defaults on; a
mandatory experimental flag on both open/close wrappers that enabled nothing and
removed the control run; a harness environment check that ignored the manual gate;
a 60-second pytest timeout around a full Meson/Ninja build; an accumulating
per-run private directory; and a missing patch-existence check in the isolated
build loop. The bounded patch was regenerated from the pinned base rather than
edited in place, and the whole `driver-series` reapplies to a fresh archive
byte-identically to the edited tree.
