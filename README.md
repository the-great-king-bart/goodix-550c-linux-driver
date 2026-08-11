# Goodix 27c6:550c Linux driver lab

This project enables the USB fingerprint reader in a Lenovo Yoga 9 14IAP7. Linux
enumerates the hardware as `27c6:550c`, but upstream `libfprint` does not bind a
driver to it.

There are two deliberately separated layers:

- a tightly allow-listed Python identity/backup probe that cannot reset or configure
  the sensor; and
- an isolated `libfprint` 1.94.10 build for the exact `550c` that can perform a
  TLS open, enrollment, and verification. Persistent mutation is denied at the
  transport boundary, and volatile initialization needs both a build-time and a
  runtime opt-in.

## Set up

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,reverse,dpapi]'
.venv/bin/pytest
```

The native driver build additionally needs Meson/Ninja, GLib/GIO, GUsb, OpenSSL,
and the OpenCV core/features2d/flann/imgproc development components. On this host:

```bash
scripts/build_goodix550c_libfprint.sh \
  --allow-volatile-init \
  --stage-dir build/goodix550c-live
```

The build stays below `build/`; it neither installs nor runs the driver. Its
minimal exact-device harness is emitted as
`build/goodix550c-live/builddir/goodix550c-open-close`.

After confirming fprintd is inactive and no process/driver owns the USB interface,
the guarded live open can be repeated with:

```bash
sudo chown root:root research/secrets/goodix550c.psk
sudo scripts/run_goodix550c_open_close.sh \
  --stage-dir build/goodix550c-live \
  --psk-file research/secrets/goodix550c.psk
sudo chown bart:bart research/secrets/goodix550c.psk
```

The wrapper stops nothing: any holder, bound interface driver, or active fprintd
causes refusal.

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

The Windows source must be exposed through a repository-local read-only mount. Check
the existing mount and machine-key prerequisites first:

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
succeeded using the isolated build. No firmware, PSK, or production data was
written, and no system library or service was changed. See the engineering record
for the exact safety gates and private enrollment/verification procedure.

See [DOCUMENTATION.md](DOCUMENTATION.md) for the evidence, protocol notes, safety model, and current status.
