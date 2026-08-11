# Goodix 27c6:550c Linux enablement

This is the living engineering record for enabling the Goodix fingerprint reader in
this Lenovo Yoga 9 14IAP7 (`82LU`) on Linux. The reader is an image sensor using a
vendor protocol and a TLS-PSK-protected image channel; it is not a Goodix MOC device.

## Non-negotiable operating constraints

- Do not reboot, power off, suspend, cold-boot, or stop/restart existing services until
  the owner explicitly permits it.
- Create and modify files only below this repository. The mounted Windows partition is
  evidence and remains read-only from this project.
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

The original Windows 11 partition is mounted at `/run/media/bart/Windows-SSD`. Static,
read-only inspection found the model-specific Goodix package in the DriverStore:

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

Installed build prerequisites on this host are `libgusb-dev`,
`libopencv-core-dev`, `libopencv-features2d-dev`, `libopencv-flann-dev`, and
`libopencv-imgproc-dev` plus their dependencies. Apt made no upgrades or removals
and restarted no services.

The clean isolated build completed without installation. A minimal harness then
loaded the build-tree library, found the sole exact-ID device, completed the TLS
handshake and volatile initialization with the recovered live PSK, and closed it
cleanly. No persistent command was sent. The generic libfprint raw-capture API is
intentionally not advertised; scans are consumed through enrollment/authentication.

System installation, PAM changes, and fprintd activation remain outside the current
live test: they would require replacing or augmenting Ubuntu's TOD-enabled libfprint
and stopping/restarting fprintd, which the owner has explicitly forbidden for now.
Any later PAM rollout must retain password authentication, prove repeated positive
and negative matches first, and must not make fingerprint the sole administrator or
disk-unlock credential.

## Verification performed so far

```text
project pytest:        33 passed
offline TLS pair test: passed, correct identity/cipher/decrypt + wrong-ID rejection
ruff:                  passed
shell syntax:          passed
compiled-source audit: passed
isolated build:        passed, no warnings, no install
libfprint unit suite:  2 passed, 1 dependency skip
live TLS open/close:   passed
```

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

Project-owned files are listed below. `.git/`, `.venv/`, Python caches, generated
coverage data, the ignored upstream clones, and the obsolete scaffold's dependency
tree are intentionally condensed.

```text
.
├── .dpapi-windows-ro/                # ignored read-only Windows bind mount
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
│   └── README.md                     # patch-series threat model
├── pyproject.toml                    # Python package/test/lint metadata
├── research/
│   ├── artifacts/                    # ignored live USB transcripts
│   │   ├── chip-id-exact.json
│   │   ├── chip-id.json
│   │   ├── iap-version.json
│   │   ├── live-probe.json
│   │   ├── otp.json
│   │   └── psk-hash.json
│   ├── obsolete-vite/                # ignored superseded scaffold + dependencies
│   ├── secrets/                       # ignored, mode-0700 key-recovery material
│   │   ├── goodix550c.psk            # recovered hex PSK, mode 0600; never print
│   │   ├── live-validation/           # private enrollment/verification artifacts
│   │   └── psk-dpapi.bin              # encrypted DPAPI blob, mode 0600
│   └── upstream/                     # ignored pinned public research clones
├── scripts/
│   ├── build_goodix550c_libfprint.sh # pinned isolated patch/build pipeline
│   ├── inventory.sh                  # passive host/USB inventory
│   ├── pe_string_xrefs.py            # offline PE string/xref helper
│   ├── recover_windows_dpapi_psk.py  # offline, non-printing DPAPI recovery
│   ├── run_goodix550c_open_close.sh  # holder-aware isolated live harness wrapper
│   └── verify_goodix550c_patch.py    # effective compiled-source policy audit
├── src/goodix550c/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                        # safety-gated CLI
│   ├── protocol.py                   # pure allow-listed protocol codec
│   └── usb_probe.py                  # bounded claim/query/release transport
├── tests/
│   ├── native/test_goodix550c_tls.c   # paired memory-BIO TLS policy test
│   ├── test_cli.py
│   ├── test_dpapi_recovery.py
│   ├── test_goodix550c_patch_policy.py
│   ├── test_protocol.py
│   └── test_usb_probe.py
└── tools/
    └── goodix550c_open_close.c       # exact-device minimal TLS open/close harness
```

Update this tree whenever project files are added, removed, or moved.

## Current status

Passive inventory, exact firmware identification, OTP validation, PSK-hash
classification, chip-register measurement, sealed-backup acquisition, offline DPAPI
unsealing, independent live-hash verification, fail-closed driver integration,
compiled-source audit, clean isolated build, and a real TLS open/close are complete.
The existing Windows firmware and PSK were not changed. Interactive enrollment and
verification are in progress; no driver has been installed system-wide.
