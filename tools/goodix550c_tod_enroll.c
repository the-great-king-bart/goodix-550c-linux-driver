/*
 * Minimal physical enrollment harness for the project-local Goodix 550c TOD
 * module and Ubuntu's installed libfprint runtime.
 *
 * This is a capture test, not a provisioning tool: the resulting template is
 * discarded before exit and is never written, printed, or hashed. It exists to
 * answer one question — can this driver read a finger on firmware 13021.
 * SPDX-License-Identifier: MIT
 */

#include <fprint.h>

#include <glib.h>
#include <stdio.h>
#include <unistd.h>

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

  /* Unlike open/close, enrollment reaches the finger-wait states, so on this
   * firmware the manual-FDT gate is required rather than optional. */
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

  /* These report what the device observed, so they are confirmations rather
   * than instructions. In particular the finger stays PRESENT throughout the
   * finger-up wait, so "hold still" here would tell the operator the opposite
   * of what that phase needs; the lift instruction is issued from the stage
   * callback below, which is what actually ends a stage. */
  if (status & FP_FINGER_STATUS_NEEDED)
    puts (">>> PLACE your finger on the sensor now.");
  else if (status & FP_FINGER_STATUS_PRESENT)
    puts ("    Finger detected; hold until the stage is reported.");
  else
    puts ("    Finger released.");

  fflush (stdout);
}

static void
on_enroll_progress (FpDevice *device,
                    gint      completed_stages,
                    FpPrint  *print,
                    gpointer  user_data,
                    GError   *error)
{
  gint total_stages = GPOINTER_TO_INT (user_data);

  (void) device;
  (void) print;

  /* A stage ends here, and the driver waits for the pad to clear before it
   * starts the next one. That wait is bounded, so the lift instruction has to
   * go out now rather than when the device later reports the finger gone. */
  if (error != NULL)
    printf ("Stage %d/%d rejected, retry: %s\n>>> LIFT your finger off the sensor.\n",
            completed_stages, total_stages, error->message);
  else
    printf ("Stage %d/%d captured.\n>>> LIFT your finger off the sensor.\n",
            completed_stages, total_stages);

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

  gint total_stages = fp_device_get_nr_enroll_stages (device);

  printf ("Open succeeded. Enrollment needs %d stage(s).\n", total_stages);
  puts ("Keep the sensor CLEAR until the first PLACE prompt: the driver first "
        "establishes two no-finger reference baselines.");
  fflush (stdout);

  g_signal_connect (device, "notify::finger-status",
                    G_CALLBACK (on_finger_status_changed), NULL);

  g_autoptr(FpPrint) template_print = fp_print_new (device);
  fp_print_set_finger (template_print, FP_FINGER_RIGHT_INDEX);

  g_autoptr(FpPrint) enrolled = fp_device_enroll_sync (device, g_object_ref (template_print),
                                                       NULL, on_enroll_progress,
                                                       GINT_TO_POINTER (total_stages),
                                                       &error);
  gboolean enrolled_ok = enrolled != NULL;

  if (!enrolled_ok)
    fprintf (stderr, "Enrollment failed: %s\n", error->message);
  else
    puts ("Enrollment completed; the template is discarded without being stored.");

  fflush (stdout);
  g_clear_error (&error);

  if (!fp_device_close_sync (device, NULL, &error))
    {
      fprintf (stderr, "Close failed: %s\n", error->message);
      return 1;
    }

  puts ("Device closed cleanly through the installed Ubuntu TOD runtime.");
  fflush (stdout);
  fflush (stderr);

  /* The device is closed and every result is reported and flushed; only global
   * teardown remains. The TOD module links OpenCV, whose worker pool is
   * created on the first successful capture, outlives this harness and exposes
   * no join API. Running the exit handlers alongside those threads unmaps code
   * they are still executing, which crashed this harness with SIGSEGV inside
   * OPENSSL_cleanup on the first run that returned normally instead of being
   * killed by the wrapper timeout. Terminate without global destructors: the
   * kernel reclaims the address space, and nothing is left to flush. */
  _exit (enrolled_ok ? 0 : 1);
}
