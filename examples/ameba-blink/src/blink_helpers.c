/*
 * src/blink_helpers.c — second source file demonstrating multi-file user code.
 *
 * v0.3 EXTERN_DIR auto-bridge: this file is auto-collected by
 * platform-realtek-ameba and registered to the SDK build via
 * app_example/_pio_src_fragment.cmake (regenerated each `pio run`).
 */

#include "ameba_soc.h"

void log_blink_message(int count)
{
    DiagPrintf("[blink] toggle #%d (helper from src/blink_helpers.c)\n", count);
}
