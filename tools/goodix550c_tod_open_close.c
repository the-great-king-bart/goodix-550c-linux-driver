/*
 * Minimal physical open/close harness for the project-local Goodix 550c TOD
 * module and Ubuntu's installed libfprint runtime.
 * SPDX-License-Identifier: MIT
 */

#include <fprint.h>

#include <glib.h>
#include <stdio.h>

#define EXPECTED_DRIVER "goodix53x5"
#define EXPECTED_NAME "Goodix HTK32 Fingerprint Sensor"

static gboolean
environment_is_exact (void)
{
  const gchar *module_dir = g_getenv ("FP_TOD_DRIVERS_DIR");
  const gchar *allowlist = g_getenv ("FP_DRIVERS_ALLOWLIST");
  const gchar *volatile_gate = g_getenv ("GOODIX550C_ALLOW_VOLATILE_INIT");
  const gchar *manual_fdt_gate = g_getenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL");
  const gchar *psk_file = g_getenv ("GOODIX550C_PSK_FILE");

  /* Open/close reaches no manual-FDT state, so that gate may be absent. It may
   * not carry an unrecognized value: the wrapper sets it to exactly "1" or not
   * at all, and anything else means this is not the guarded profile. */
  return module_dir != NULL && g_path_is_absolute (module_dir) &&
         g_strcmp0 (allowlist, EXPECTED_DRIVER) == 0 &&
         g_strcmp0 (volatile_gate, "1") == 0 &&
         (manual_fdt_gate == NULL ||
          g_strcmp0 (manual_fdt_gate, "1") == 0) &&
         psk_file != NULL && g_path_is_absolute (psk_file) &&
         g_getenv ("GOODIX550C_PSK") == NULL &&
         g_getenv ("LD_LIBRARY_PATH") == NULL &&
         g_getenv ("LD_PRELOAD") == NULL;
}

int
main (void)
{
  if (!environment_is_exact ())
    {
      fprintf (stderr, "Refusing: runtime environment is not the guarded TOD profile.\n");
      return 2;
    }

  g_autoptr(FpContext) context = fp_context_new ();
  GPtrArray *devices = fp_context_get_devices (context);
  g_autoptr(GError) error = NULL;

  if (devices == NULL || devices->len != 1)
    {
      fprintf (stderr, "Refusing: expected exactly one supported 27c6:550c device.\n");
      return 2;
    }

  FpDevice *device = g_ptr_array_index (devices, 0);
  if (g_strcmp0 (fp_device_get_driver (device), EXPECTED_DRIVER) != 0 ||
      g_strcmp0 (fp_device_get_name (device), EXPECTED_NAME) != 0 ||
      fp_device_get_scan_type (device) != FP_SCAN_TYPE_PRESS)
    {
      fprintf (stderr, "Refusing: enumerated fingerprint device is unexpected.\n");
      return 2;
    }

  if (!fp_device_open_sync (device, NULL, &error))
    {
      fprintf (stderr, "Open failed: %s\n", error->message);
      return 1;
    }

  puts ("Exact firmware, PSK-TLS, and volatile TOD open succeeded.");

  if (!fp_device_close_sync (device, NULL, &error))
    {
      fprintf (stderr, "Close failed: %s\n", error->message);
      return 1;
    }

  puts ("Device closed cleanly through the installed Ubuntu TOD runtime.");
  return 0;
}
