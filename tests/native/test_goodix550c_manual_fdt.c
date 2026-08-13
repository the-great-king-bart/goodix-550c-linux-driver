/* Offline tests for the guarded manual-FDT policy and raw-baseline math. */
#include "goodix53x5-calibration.h"
#include "goodix53x5-safety.h"

#include <gio/gio.h>
#include <stdio.h>
#include <string.h>

static gboolean
expect (gboolean condition,
        const char *message)
{
  if (!condition)
    fprintf (stderr, "FAIL: %s\n", message);
  return condition;
}

static void
put_pair (guint8  *data,
          guint    pair,
          guint16  value)
{
  data[pair * 2] = value & 0xff;
  data[pair * 2 + 1] = value >> 8;
}

static gboolean
test_delta_summary (void)
{
  guint8 baseline[GOODIX_FDT_BASE_LEN] = { 0 };
  guint8 reading[GOODIX_FDT_BASE_LEN] = { 0 };
  guint16 max_delta = 0;
  guint changed_pairs = 0;

  for (guint i = 0; i < GOODIX_FDT_BASE_LEN / 2; i++)
    {
      put_pair (baseline, i, 200);
      put_pair (reading, i, 200);
    }

  /* Pair deltas after the protocol's >>1 normalization are 13 and 10. */
  put_pair (reading, 0, 226);
  put_pair (reading, 1, 220);

  return expect (goodix_device_measure_fdt_delta (baseline, reading,
                                                   sizeof (baseline), 12,
                                                   &max_delta,
                                                   &changed_pairs),
                 "valid baseline comparison was rejected") &&
         expect (max_delta == 13, "aggregate max delta is wrong") &&
         expect (changed_pairs == 1, "changed-pair count is wrong") &&
         expect (goodix_device_measure_fdt_delta (baseline, reading,
                                                   sizeof (baseline), 13,
                                                   &max_delta,
                                                   &changed_pairs),
                 "threshold-boundary comparison was rejected") &&
         expect (changed_pairs == 0,
                 "threshold equality was treated as a changed pair") &&
         expect (!goodix_device_measure_fdt_delta (baseline, reading, 23, 12,
                                                    &max_delta,
                                                    &changed_pairs),
                 "odd-length FDT input was accepted") &&
         expect (!goodix_device_measure_fdt_delta (NULL, reading,
                                                    sizeof (reading), 12,
                                                    &max_delta,
                                                    &changed_pairs),
                 "NULL baseline was accepted") &&
         expect (!goodix_device_measure_fdt_delta (baseline, reading, 0, 12,
                                                    &max_delta,
                                                    &changed_pairs),
                 "empty FDT input was accepted");
}

static gboolean
test_dual_gate (void)
{
  g_unsetenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL");
  if (!expect (!goodix_550c_manual_fdt_poll_allowed (),
               "manual polling enabled without runtime opt-in"))
    return FALSE;

  g_setenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL", "true", TRUE);
  if (!expect (!goodix_550c_manual_fdt_poll_allowed (),
               "non-exact runtime value enabled manual polling"))
    return FALSE;

  g_setenv ("GOODIX550C_ALLOW_MANUAL_FDT_POLL", "1", TRUE);
  return expect (goodix_550c_manual_fdt_poll_allowed (),
                 "exact runtime opt-in did not enable compiled polling");
}

int
main (void)
{
  if (!test_delta_summary () || !test_dual_gate ())
    return 1;

  puts ("Goodix manual-FDT offline policy test passed.");
  return 0;
}
