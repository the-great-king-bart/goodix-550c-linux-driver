/*
 * Minimal physical verification harness for the project-local Goodix 550c TOD
 * module and Ubuntu's installed libfprint runtime.
 *
 * This enrolls a template, holds it in process memory only, and matches live
 * fingers against it. The template is never serialized, written, printed, or
 * hashed, and neither are the scanned prints produced by each verification. It
 * exists to answer one question — does a template this driver enrolls actually
 * match the finger that produced it, and reject one that did not.
 * SPDX-License-Identifier: MIT
 */

#include <fprint.h>

#include <glib.h>
#include <stdio.h>
#include <unistd.h>

#define EXPECTED_DRIVER "goodix53x5"
#define EXPECTED_NAME "Goodix HTK32 Fingerprint Sensor"

/* Two trials with the enrolled finger and one with a different finger. A
 * positive-only test would pass just as well against a driver that matched
 * everything, so the negative trial is what gives the result meaning. */
#define VERIFY_SAME_FINGER_TRIALS 2
#define VERIFY_DIFFERENT_FINGER_TRIALS 1
#define VERIFY_TRIALS (VERIFY_SAME_FINGER_TRIALS + VERIFY_DIFFERENT_FINGER_TRIALS)

/* A retry is the device asking for another placement, not a verdict. Counting
 * one as a lost trial would report a failure the driver never made; note that
 * this driver also raises FP_DEVICE_RETRY_REMOVE_FINGER for a capture with too
 * few features, so retries are expected in normal use. */
#define VERIFY_MAX_RETRIES 4

static gboolean
environment_is_exact (void)
{
  const gchar *module_dir = g_getenv ("FP_TOD_DRIVERS_DIR");
  const gchar *allowlist = g_getenv ("FP_DRIVERS_ALLOWLIST");
  const gchar *volatile_gate = g_getenv ("GOODIX550C_ALLOW_VOLATILE_INIT");
  const gchar *manual_fdt_gate = g_getenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL");
  const gchar *psk_file = g_getenv ("GOODIX550C_PSK_FILE");

  /* Verification reaches the same finger-wait states as enrollment, so on this
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

  /* Confirmations, not instructions: the finger stays PRESENT for the whole
   * finger-up wait, so the lift cue is issued where a step actually ends. */
  if (status & FP_FINGER_STATUS_NEEDED)
    puts (">>> PLACE your finger on the sensor now.");
  else if (status & FP_FINGER_STATUS_PRESENT)
    puts ("    Finger detected; hold until the result is reported.");
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

  if (error != NULL)
    printf ("Stage %d/%d rejected, retry: %s\n>>> LIFT your finger off the sensor.\n",
            completed_stages, total_stages, error->message);
  else
    printf ("Stage %d/%d captured.\n>>> LIFT your finger off the sensor.\n",
            completed_stages, total_stages);

  fflush (stdout);
}

/**
 * run_verify_trial:
 *
 * Run one match against the in-memory template. Returns TRUE when the trial
 * produced a verdict, with @match set; FALSE when the device raised an error,
 * which the caller reports without treating it as a non-match.
 */
static gboolean
run_verify_trial (FpDevice  *device,
                  FpPrint   *enrolled,
                  gboolean  *match,
                  GError   **error)
{
  g_autoptr(FpPrint) scanned = NULL;

  if (!fp_device_verify_sync (device, enrolled, NULL, NULL, NULL,
                              match, &scanned, error))
    return FALSE;

  /* scanned is dropped here: the live print is no more storable than the
   * template it was compared against. */
  return TRUE;
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

  if (!fp_device_has_feature (device, FP_DEVICE_FEATURE_VERIFY))
    {
      fprintf (stderr, "Refusing: device does not advertise verification.\n");
      return 2;
    }

  if (!fp_device_open_sync (device, NULL, &error))
    {
      fprintf (stderr, "Open failed: %s\n", error->message);
      return 1;
    }

  gint total_stages = fp_device_get_nr_enroll_stages (device);

  printf ("Open succeeded. Enrollment needs %d stage(s), then %d verification trial(s).\n",
          total_stages, VERIFY_TRIALS);
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
  if (enrolled == NULL)
    {
      fprintf (stderr, "Enrollment failed: %s\n", error->message);
      fflush (stderr);
      g_clear_error (&error);
      fp_device_close_sync (device, NULL, NULL);
      _exit (1);
    }

  puts ("Enrollment complete. The template is held in memory only and is never stored.");
  fflush (stdout);

  int expected_matches = 0;
  int observed_matches = 0;
  int verdicts = 0;

  for (int trial = 0; trial < VERIFY_TRIALS; trial++)
    {
      gboolean same_finger = trial < VERIFY_SAME_FINGER_TRIALS;
      gboolean match = FALSE;

      if (same_finger)
        printf (">>> TRIAL %d/%d: use the SAME finger you enrolled (expect a match).\n",
                trial + 1, VERIFY_TRIALS);
      else
        printf (">>> TRIAL %d/%d: use a DIFFERENT finger (expect no match).\n",
                trial + 1, VERIFY_TRIALS);
      fflush (stdout);

      gboolean got_verdict = FALSE;

      for (int attempt = 0; attempt <= VERIFY_MAX_RETRIES; attempt++)
        {
          if (run_verify_trial (device, enrolled, &match, &error))
            {
              got_verdict = TRUE;
              break;
            }

          gboolean retryable = g_error_matches (error, FP_DEVICE_RETRY,
                                                FP_DEVICE_RETRY_GENERAL) ||
                               error->domain == FP_DEVICE_RETRY;

          printf ("Trial %d/%d attempt %d: %s\n>>> LIFT your finger off the sensor.\n",
                  trial + 1, VERIFY_TRIALS, attempt + 1, error->message);
          fflush (stdout);
          g_clear_error (&error);

          if (!retryable)
            break;

          if (attempt < VERIFY_MAX_RETRIES)
            {
              printf (">>> PLACE the same finger again for trial %d/%d.\n",
                      trial + 1, VERIFY_TRIALS);
              fflush (stdout);
            }
        }

      if (!got_verdict)
        continue;

      verdicts++;
      if (same_finger)
        expected_matches++;
      if (match)
        observed_matches++;

      printf ("Trial %d/%d result: %s (expected %s).\n>>> LIFT your finger off the sensor.\n",
              trial + 1, VERIFY_TRIALS,
              match ? "MATCH" : "no match",
              same_finger ? "MATCH" : "no match");
      fflush (stdout);
    }

  gboolean verified_ok = verdicts == VERIFY_TRIALS &&
                         observed_matches == expected_matches;

  printf ("Verification summary: %d/%d trials returned a verdict, %d matched, %d expected.\n",
          verdicts, VERIFY_TRIALS, observed_matches, expected_matches);
  fflush (stdout);

  if (!fp_device_close_sync (device, NULL, &error))
    {
      fprintf (stderr, "Close failed: %s\n", error->message);
      fflush (stderr);
      _exit (1);
    }

  puts ("Device closed cleanly through the installed Ubuntu TOD runtime.");
  fflush (stdout);
  fflush (stderr);

  /* The device is closed and every result is reported and flushed; only global
   * teardown remains. The TOD module links OpenCV, whose worker pool is created
   * on the first successful capture, outlives this harness and exposes no join
   * API. Running the exit handlers alongside those threads unmaps code they are
   * still executing, which crashed the enrollment harness with SIGSEGV inside
   * OPENSSL_cleanup. Terminate without global destructors: the kernel reclaims
   * the address space, and nothing is left to flush. */
  _exit (verified_ok ? 0 : 1);
}
