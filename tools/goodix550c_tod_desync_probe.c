/*
 * Minimal post-action desynchronization probe for the project-local Goodix 550c
 * TOD module and Ubuntu's installed libfprint runtime.
 *
 * The first verification or identification of a session succeeds and every
 * later one reads a dead sensor. Reproducing that with the verification harness
 * costs a full eight-stage enrollment plus two match trials; this reaches the
 * same state with identification actions against an empty gallery, which need
 * one placement each.
 *
 * The corruption appears in the following action's reference capture, which
 * runs before contact is requested, so the reply-framing diagnostic answers the
 * question before that action asks for a finger at all.
 *
 * Nothing is enrolled, matched, or stored: the gallery is empty by design.
 * SPDX-License-Identifier: MIT
 */

#include <fprint.h>

#include <glib.h>
#include <stdio.h>
#include <unistd.h>

#define EXPECTED_DRIVER "goodix53x5"
#define EXPECTED_NAME "Goodix HTK32 Fingerprint Sensor"

/* Three actions, because only an action that produces a verdict reaches the
 * deactivation exit path: one that ends in a retry routes through the
 * finger-up sub-SSM instead and leaves the next action's frames clean, which
 * was measured. Three gives a retry room to happen without costing the run. */
#define PROBE_ACTIONS 3

static gboolean
environment_is_exact (void)
{
  const gchar *module_dir = g_getenv ("FP_TOD_DRIVERS_DIR");
  const gchar *allowlist = g_getenv ("FP_DRIVERS_ALLOWLIST");
  const gchar *volatile_gate = g_getenv ("GOODIX550C_ALLOW_VOLATILE_INIT");
  const gchar *manual_fdt_gate = g_getenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL");
  const gchar *psk_file = g_getenv ("GOODIX550C_PSK_FILE");

  /* The probe reaches the finger-wait states, so the manual-FDT gate is
   * required rather than optional, exactly as for enrollment. */
  return module_dir != NULL && g_path_is_absolute (module_dir) &&
         g_strcmp0 (allowlist, EXPECTED_DRIVER) == 0 &&
         g_strcmp0 (volatile_gate, "1") == 0 &&
         g_strcmp0 (manual_fdt_gate, "1") == 0 &&
         psk_file != NULL && g_path_is_absolute (psk_file) &&
         g_getenv ("GOODIX550C_PSK") == NULL &&
         g_getenv ("LD_LIBRARY_PATH") == NULL &&
         g_getenv ("LD_PRELOAD") == NULL;
}

static void
on_finger_status_changed (GObject    *object,
                          GParamSpec *pspec,
                          gpointer    user_data)
{
  FpFingerStatusFlags status = fp_device_get_finger_status (FP_DEVICE (object));

  (void) pspec;
  (void) user_data;

  if (status & FP_FINGER_STATUS_NEEDED)
    puts (">>> PLACE your finger on the sensor now.");
  else if (status & FP_FINGER_STATUS_PRESENT)
    puts ("    Finger detected; hold until the action is reported.");
  else
    puts ("    Finger released.");

  fflush (stdout);
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

  printf ("Open succeeded. %d identification actions against an empty gallery.\n",
          PROBE_ACTIONS);
  puts ("Each action reports its reference frame before asking for a finger.");
  puts ("Keep the sensor CLEAR until the first PLACE prompt.");
  fflush (stdout);

  g_signal_connect (device, "notify::finger-status",
                    G_CALLBACK (on_finger_status_changed), NULL);

  g_autoptr(GPtrArray) gallery = g_ptr_array_new_with_free_func (g_object_unref);

  for (int action = 0; action < PROBE_ACTIONS; action++)
    {
      g_autoptr(FpPrint) matched = NULL;
      g_autoptr(FpPrint) scanned = NULL;

      printf (">>> ACTION %d/%d starting.\n", action + 1, PROBE_ACTIONS);
      fflush (stdout);

      if (!fp_device_identify_sync (device, gallery, NULL, NULL, NULL,
                                    &matched, &scanned, &error))
        {
          printf ("Action %d/%d ended with: %s\n>>> LIFT your finger off the sensor.\n",
                  action + 1, PROBE_ACTIONS, error->message);
          fflush (stdout);
          g_clear_error (&error);
          continue;
        }

      /* An empty gallery cannot match; only the capture path is under test. */
      printf ("Action %d/%d completed (no gallery entry to match).\n"
              ">>> LIFT your finger off the sensor.\n",
              action + 1, PROBE_ACTIONS);
      fflush (stdout);
    }

  if (!fp_device_close_sync (device, NULL, &error))
    {
      fprintf (stderr, "Close failed: %s\n", error->message);
      fflush (stderr);
      _exit (1);
    }

  puts ("Device closed cleanly through the installed Ubuntu TOD runtime.");
  fflush (stdout);
  fflush (stderr);

  /* The module's OpenCV worker pool outlives this harness and cannot be
   * joined; running global destructors beside it crashed the enrollment
   * harness inside OPENSSL_cleanup. Terminate without them. */
  _exit (0);
}
