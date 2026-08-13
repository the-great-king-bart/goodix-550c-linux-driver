/*
 * External TOD registration for the fail-closed Goodix 27c6:550c driver.
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */

#include <gmodule.h>

#include "goodix53x5.h"

G_MODULE_EXPORT GType fpi_tod_shared_driver_get_type (void);

G_MODULE_EXPORT GType
fpi_tod_shared_driver_get_type (void)
{
  return fpi_device_goodix53x5_get_type ();
}
