/* Minimal isolated libfprint harness for the fail-closed Goodix 27c6:550c build. */
#include <libfprint/fprint.h>

#include <glib.h>
#include <stdio.h>

int
main (void)
{
  g_autoptr(FpContext) context = fp_context_new ();
  GPtrArray *devices = fp_context_get_devices (context);
  g_autoptr(GError) error = NULL;

  if (devices == NULL || devices->len != 1)
    {
      fprintf (stderr, "Refusing: expected exactly one supported 27c6:550c device.\n");
      return 2;
    }

  FpDevice *device = g_ptr_array_index (devices, 0);
  if (g_strcmp0 (fp_device_get_driver (device), "goodix53x5") != 0)
    {
      fprintf (stderr, "Refusing: unexpected libfprint driver.\n");
      return 2;
    }

  printf ("Detected %s (%s).\n", fp_device_get_name (device),
          fp_device_get_device_id (device));

  if (!fp_device_open_sync (device, NULL, &error))
    {
      fprintf (stderr, "Open failed: %s\n", error->message);
      return 1;
    }

  puts ("TLS open and volatile initialization succeeded.");

  if (!fp_device_close_sync (device, NULL, &error))
    {
      fprintf (stderr, "Close failed: %s\n", error->message);
      return 1;
    }

  puts ("Device closed cleanly.");
  return 0;
}
