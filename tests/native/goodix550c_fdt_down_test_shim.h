/* Test-only seam for compiling the production command source without a
 * GObject-backed USB device or transport.  The native regression supplies a
 * capture-only goodix_test_run_cmd() and never submits I/O. */
#pragma once

#include "goodix53x5-private.h"

#define FPI_DEVICE_GOODIX53X5(instance) ((FpiDeviceGoodix53x5 *) (instance))
#define goodix_run_cmd goodix_test_run_cmd
